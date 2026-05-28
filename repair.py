import struct, os, zlib
from compression import CMPCompressor, VHCompressor, CMPArchive, VHArchive, _read_archive_header, _read_file_entry_v2, _derive_key, _aes_decrypt, _HAS_CRYPTO

ARCHIVERS_BY_MAGIC = {
    b"CMP2": ("CMP", CMPArchive, CMPCompressor),
    b"VHC2": ("VH", VHArchive, VHCompressor),
    b"CMPC": ("CMP", CMPArchive, CMPCompressor),
    b"VHC1": ("VH", VHArchive, VHCompressor),
}

def identify_archive(path):
    with open(path, "rb") as f:
        magic = f.read(4)
    for m, (name, arch, comp) in ARCHIVERS_BY_MAGIC.items():
        if magic == m:
            return name, arch, comp
    return None, None, None

def repair_archive(path, output_path, password=None):
    results = {"total": 0, "recovered": 0, "lost": 0, "errors": [], "files": []}
    with open(path, "rb") as f:
        data = f.read()
    fmt_name, ArchCls, CompCls = identify_archive(path)
    if not ArchCls:
        results["errors"].append("Nieznany format archiwum")
        return results
    v1_magic = CompCls.MAGIC
    v2_magic = ArchCls.V2_MAGIC
    hdr = _read_archive_header(data, v1_magic, v2_magic)
    if not hdr:
        results["errors"].append("Nie mozna odczytac naglowka archiwum")
        return results
    version, num_files, flags, salt, offset = hdr
    results["total"] = num_files
    key = None
    if (flags & 1) and password:
        if _HAS_CRYPTO and salt:
            try:
                key = _derive_key(password, salt)
            except Exception as e:
                results["errors"].append(f"Blad klucza: {e}")
                return results
    recovered_data = []
    for idx in range(num_files):
        saved_offset = offset
        try:
            if version == 1:
                if offset + 2 > len(data):
                    results["lost"] += 1
                    results["errors"].append(f"#{idx}: koniec danych")
                    break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data):
                    results["lost"] += 1
                    results["errors"].append(f"#{idx}: uszkodzona nazwa")
                    break
                name = data[offset:offset+name_len].decode("utf-8", errors="replace"); offset += name_len
                if offset + 8 > len(data):
                    results["lost"] += 1
                    results["errors"].append(f"#{idx}: uszkodzony naglowek")
                    break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                if offset + comp_size > len(data):
                    results["lost"] += 1
                    results["errors"].append(f"#{idx}: uszkodzone dane")
                    break
                comp_data = data[offset:offset+comp_size]; offset += comp_size
                dec = CompCls.decompress(comp_data)
                if dec is None:
                    results["lost"] += 1
                    results["errors"].append(f"#{idx} '{name}': nie udalo sie dekompresowac")
                    continue
                content = dec[:orig_size]
            else:
                if offset + 2 > len(data):
                    results["lost"] += 1
                    results["errors"].append(f"#{idx}: koniec danych (v2)")
                    break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data):
                    results["lost"] += 1;
                    results["errors"].append(f"#{idx}: uszkodzona nazwa")
                    break
                name = data[offset:offset+name_len].decode("utf-8", errors="replace"); offset += name_len
                if offset + 8 > len(data):
                    results["lost"] += 1;
                    results["errors"].append(f"#{idx}: uszkodzony naglowek")
                    break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                crc = 0
                if flags & 2:
                    if offset + 4 > len(data):
                        results["lost"] += 1;
                        results["errors"].append(f"#{idx}: uszkodzone CRC")
                        break
                    crc = struct.unpack("<I", data[offset:offset+4])[0]; offset += 4
                nonce, tag = None, None
                if (flags & 1) and key is not None:
                    if offset + 32 > len(data):
                        results["lost"] += 1;
                        results["errors"].append(f"#{idx}: uszkodzony nonce/tag")
                        break
                    nonce, tag = data[offset:offset+16], data[offset+16:offset+32]; offset += 32
                if offset + comp_size > len(data):
                    results["lost"] += 1;
                    results["errors"].append(f"#{idx}: uszkodzone dane skompresowane")
                    break
                comp_data = data[offset:offset+comp_size]
                if nonce and key:
                    try:
                        comp_data = _aes_decrypt(comp_data, key, nonce, tag)
                    except Exception as e:
                        results["lost"] += 1
                        results["errors"].append(f"#{idx} '{name}': blad deszyfrowania: {e}")
                        offset += comp_size
                        continue
                offset += comp_size
                try:
                    dec = CompCls.decompress(comp_data)
                    if dec is None:
                        results["lost"] += 1
                        results["errors"].append(f"#{idx} '{name}': nie udalo sie dekompresowac")
                        continue
                    content = dec[:orig_size]
                    if crc and zlib.crc32(content) & 0xFFFFFFFF != crc:
                        results["errors"].append(f"#{idx} '{name}': CRC niezgodne (odzyskano mimo to)")
                except Exception as e:
                    results["lost"] += 1
                    results["errors"].append(f"#{idx} '{name}': {e}")
                    continue
            results["recovered"] += 1
            results["files"].append((name, content, orig_size))
        except Exception as e:
            offset = saved_offset
            if version == 1:
                try:
                    if saved_offset + 2 <= len(data):
                        nl = data[saved_offset] | (data[saved_offset+1] << 8)
                        offset = saved_offset + 2 + nl + 8
                        if offset <= saved_offset + 2:
                            offset = saved_offset + 10
                    else:
                        offset = saved_offset + 10
                except Exception:
                    offset = saved_offset + 10
            else:
                offset = saved_offset + 10
            if offset > len(data): offset = len(data)
            results["lost"] += 1
            results["errors"].append(f"#{idx}: nieoczekiwany blad: {e}")
    if results["recovered"] > 0:
        ArchCls.pack([(name, content) for name, content, _ in results["files"]], output_path, password=password)
    return results

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uzycie: repair.py <archiwum> [output]")
        sys.exit(1)
    inp = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else inp.replace(".cmp", "_repaired.cmp").replace(".vh", "_repaired.vh")
    r = repair_archive(inp, out)
    print(f"Razem: {r['total']}, Odzyskane: {r['recovered']}, Utracone: {r['lost']}")
    for e in r["errors"]:
        print(f"  - {e}")
