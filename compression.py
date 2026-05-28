import struct, os, zlib
from collections import Counter

_HAS_CRYPTO = False
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    import hmac
    _HAS_CRYPTO = True
except ImportError:
    pass


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt,
        iterations=100000, backend=default_backend())
    return kdf.derive(password.encode("utf-8"))

def _aes_encrypt(plain: bytes, key: bytes) -> tuple:
    nonce = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce), backend=default_backend())
    enc = cipher.encryptor()
    ct = enc.update(plain) + enc.finalize()
    tag = hmac.new(key, nonce + ct, "sha256").digest()[:16]
    return ct, nonce, tag

def _aes_decrypt(ct: bytes, key: bytes, nonce: bytes, tag: bytes) -> bytes:
    expected = hmac.new(key, nonce + ct, "sha256").digest()[:16]
    if not hmac.compare_digest(expected, tag):
        raise ValueError("Nieprawidlowe haslo lub uszkodzone dane")
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce), backend=default_backend())
    dec = cipher.decryptor()
    return dec.update(ct) + dec.finalize()


# ─────────────────────────────────────────────
#  CMP COMPRESSOR (BPE + RLE)
# ─────────────────────────────────────────────

class CMPCompressor:
    MAGIC = b"CMPC"
    V1, V2 = 1, 2
    RLE_MARKER = 0xFF

    @staticmethod
    def _rle_encode(data):
        result = bytearray()
        i = 0
        while i < len(data):
            if i + 2 < len(data) and data[i] == data[i+1] == data[i+2]:
                b = data[i]; j = i + 3
                while j < len(data) and data[j] == b: j += 1
                count = j - i
                while count > 0:
                    chunk = min(count, 255)
                    result.extend([CMPCompressor.RLE_MARKER, chunk, b])
                    count -= chunk
                i = j
            else:
                if data[i] == CMPCompressor.RLE_MARKER:
                    result.extend([CMPCompressor.RLE_MARKER, 0, CMPCompressor.RLE_MARKER])
                else:
                    result.append(data[i])
                i += 1
        return bytes(result)

    @staticmethod
    def _rle_decode(data):
        result = bytearray(); i = 0
        while i < len(data):
            if data[i] == CMPCompressor.RLE_MARKER:
                if i + 2 >= len(data): break
                count, b = data[i+1], data[i+2]
                if count == 0: result.append(b)
                else: result.extend([b] * count)
                i += 3
            else:
                result.append(data[i]); i += 1
        return bytes(result)

    @staticmethod
    def _bpe_build(data, num_pairs=32):
        if len(data) < 2: return [], data
        pairs = Counter()
        for i in range(len(data)-1): pairs[(data[i], data[i+1])] += 1
        if not pairs: return [], data
        most_common = pairs.most_common(num_pairs)
        used = {b for b in data}
        free = [b for b in range(1, 256) if b not in used and b != CMPCompressor.RLE_MARKER]
        mapping = []
        for (a, b), _ in most_common:
            if not free: break
            repl = free.pop(0)
            mapping.append((a, b, repl))
        return mapping, data

    @staticmethod
    def _bpe_apply(data, mapping):
        if not mapping: return data
        map_pairs = {(a, b): repl for a, b, repl in mapping}
        result = bytearray(); i = 0
        while i < len(data):
            if i + 1 < len(data) and (data[i], data[i+1]) in map_pairs:
                result.append(map_pairs[(data[i], data[i+1])]); i += 2
            else:
                result.append(data[i]); i += 1
        return bytes(result)

    @staticmethod
    def _bpe_reverse(data, mappings):
        for a, b, repl in reversed(mappings):
            result = bytearray()
            for byte in data:
                if byte == repl: result.extend([a, b])
                else: result.append(byte)
            data = bytes(result)
        return data

    @staticmethod
    def compress(data, passes=3, num_pairs=16):
        if not data: return b""
        current = data; all_mappings = []
        for _ in range(passes):
            mapping, current = CMPCompressor._bpe_build(current, num_pairs)
            if not mapping: break
            current = CMPCompressor._bpe_apply(current, mapping)
            all_mappings.extend(mapping)
        encoded = CMPCompressor._rle_encode(current)
        header = struct.pack("<4sBH", CMPCompressor.MAGIC, CMPCompressor.V1, len(all_mappings))
        mapping_data = b"".join(struct.pack("BBB", a, b, r) for a, b, r in all_mappings)
        return header + mapping_data + struct.pack("<I", len(data)) + encoded

    @staticmethod
    def decompress(data):
        if not data or len(data) < 8: return None
        if data[:4] != CMPCompressor.MAGIC: return None
        version = data[4]
        num_mappings = struct.unpack("<H", data[5:7])[0]
        offset = 7
        mappings = []
        for _ in range(num_mappings):
            if offset + 3 > len(data): return None
            a, b, r = struct.unpack("BBB", data[offset:offset+3])
            mappings.append((a, b, r)); offset += 3
        if offset + 4 > len(data): return None
        orig_size = struct.unpack("<I", data[offset:offset+4])[0]; offset += 4
        decoded_rle = CMPCompressor._rle_decode(data[offset:])
        return CMPCompressor._bpe_reverse(decoded_rle, mappings)[:orig_size]


# ─────────────────────────────────────────────
#  VH COMPRESSOR (3-gram BPE + enhanced RLE)
# ─────────────────────────────────────────────

class VHCompressor:
    MAGIC = b"VHC1"
    V1, V2 = 1, 2
    RLE_MARKER = 0xFE
    RLE_LONG = 0xFD

    @staticmethod
    def _find_unused(data, count=1):
        used = {b for b in data}
        free = [b for b in range(1, 256) if b not in used
                and b not in (VHCompressor.RLE_MARKER, VHCompressor.RLE_LONG)]
        return free[:count]

    @staticmethod
    def _rle_encode(data):
        result = bytearray(); i = 0
        while i < len(data):
            if i + 3 < len(data) and data[i] == data[i+1] == data[i+2] == data[i+3]:
                b = data[i]; j = i + 4
                while j < len(data) and data[j] == b: j += 1
                count = j - i
                while count > 0:
                    chunk = min(count, 65535)
                    if chunk > 255:
                        result.extend([VHCompressor.RLE_LONG, chunk & 0xFF, (chunk >> 8) & 0xFF, b])
                    else: result.extend([VHCompressor.RLE_MARKER, chunk, b])
                    count -= chunk
                i = j
            elif i + 2 < len(data) and data[i] == data[i+1] == data[i+2]:
                b = data[i]; j = i + 3
                while j < len(data) and data[j] == b: j += 1
                count = j - i
                while count > 0:
                    chunk = min(count, 255)
                    result.extend([VHCompressor.RLE_MARKER, chunk, b])
                    count -= chunk
                i = j
            else:
                if data[i] in (VHCompressor.RLE_MARKER, VHCompressor.RLE_LONG):
                    result.extend([data[i], 0, 0, data[i]])
                else: result.append(data[i])
                i += 1
        return bytes(result)

    @staticmethod
    def _rle_decode(data):
        result = bytearray(); i = 0
        while i < len(data):
            b = data[i]
            if b == VHCompressor.RLE_LONG:
                if i + 3 >= len(data): break
                count = data[i+1] | (data[i+2] << 8); val = data[i+3]
                if count == 0: result.append(val)
                else: result.extend([val] * count)
                i += 4
            elif b == VHCompressor.RLE_MARKER:
                if i + 2 >= len(data): break
                count, val = data[i+1], data[i+2]
                if count == 0: result.append(val)
                else: result.extend([val] * count)
                i += 3
            else:
                result.append(b); i += 1
        return bytes(result)

    @staticmethod
    def _bpe_build_23(data, num_pairs=48):
        if len(data) < 2: return [], data
        pairs, triples = Counter(), Counter()
        for i in range(len(data)-1): pairs[(data[i], data[i+1])] += 1
        for i in range(len(data)-2): triples[(data[i], data[i+1], data[i+2])] += 1
        mapping = []
        free = VHCompressor._find_unused(data, num_pairs * 2)
        fi = 0
        for (a, b, c), _ in triples.most_common(max(1, num_pairs // 3)):
            if fi >= len(free): break
            mapping.append((3, a, b, c, free[fi])); fi += 1
        for (a, b), _ in pairs.most_common(num_pairs):
            if fi >= len(free): break
            mapping.append((2, a, b, 0, free[fi])); fi += 1
        return mapping, data

    @staticmethod
    def _bpe_apply(data, mapping):
        if not mapping: return data
        result = bytearray(); i = 0
        while i < len(data):
            matched = False
            for m in mapping:
                if m[0] == 3 and i + 2 < len(data):
                    _, a, b, c, repl = m
                    if data[i] == a and data[i+1] == b and data[i+2] == c:
                        result.append(repl); i += 3; matched = True; break
            if matched: continue
            for m in mapping:
                if m[0] == 2 and i + 1 < len(data):
                    _, a, b, _, repl = m
                    if data[i] == a and data[i+1] == b:
                        result.append(repl); i += 2; matched = True; break
            if matched: continue
            result.append(data[i]); i += 1
        return bytes(result)

    @staticmethod
    def _bpe_reverse(data, mappings):
        for typ, a, b, c, repl in reversed(mappings):
            result = bytearray()
            for byte in data:
                if byte == repl:
                    if typ == 3: result.extend([a, b, c])
                    else: result.extend([a, b])
                else: result.append(byte)
            data = bytes(result)
        return data

    @staticmethod
    def compress(data, passes=4, num_pairs=48):
        if not data: return b""
        current = data; all_mappings = []
        for _ in range(passes):
            mapping, current = VHCompressor._bpe_build_23(current, num_pairs)
            if not mapping: break
            current = VHCompressor._bpe_apply(current, mapping)
            all_mappings.extend(mapping)
        encoded = VHCompressor._rle_encode(current)
        header = struct.pack("<4sBHI", VHCompressor.MAGIC, VHCompressor.V1, len(all_mappings), len(data))
        mapping_data = b"".join(struct.pack("BBBBB", typ, a, b, c, r) for typ, a, b, c, r in all_mappings)
        return header + mapping_data + encoded

    @staticmethod
    def decompress(data):
        if not data or len(data) < 11: return None
        if data[:4] != VHCompressor.MAGIC: return None
        version = data[4]
        num_mappings = struct.unpack("<H", data[5:7])[0]
        orig_size = struct.unpack("<I", data[7:11])[0]; offset = 11
        mappings = []
        for _ in range(num_mappings):
            if offset + 5 > len(data): return None
            typ, a, b, c, r = struct.unpack("BBBBB", data[offset:offset+5])
            mappings.append((typ, a, b, c, r)); offset += 5
        decoded_rle = VHCompressor._rle_decode(data[offset:])
        return VHCompressor._bpe_reverse(decoded_rle, mappings)[:orig_size]


# ─────────────────────────────────────────────
#  SHARED ARCHIVE LOGIC (v2 with CRC + AES)
# ─────────────────────────────────────────────

def _read_archive_header(data, v1_magic, v2_magic):
    if len(data) >= 9 and data[:4] == v1_magic:
        num_files = data[4]
        return (1, num_files, 0, None, 9)
    if len(data) >= 7 and data[:4] == v2_magic:
        version = data[4]
        num_files = data[5]
        flags = data[6]
        offset = 7
        salt = None
        if flags & 1:
            if len(data) < offset + 16: return None
            salt = data[offset:offset+16]; offset += 16
        return (version, num_files, flags, salt, offset)
    return None

def _read_file_entry_v2(data, offset, flags, key=None):
    if offset + 2 > len(data): return None
    name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
    if offset + name_len > len(data): return None
    name = data[offset:offset+name_len].decode("utf-8"); offset += name_len
    if offset + 8 > len(data): return None
    orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
    crc = 0
    if flags & 2:
        if offset + 4 > len(data): return None
        crc = struct.unpack("<I", data[offset:offset+4])[0]; offset += 4
    nonce, tag = None, None
    if (flags & 1) and key is not None:
        if offset + 32 > len(data): return None
        nonce, tag = data[offset:offset+16], data[offset+16:offset+32]; offset += 32
    if offset + comp_size > len(data): return None
    comp_data = data[offset:offset+comp_size]
    if nonce and key:
        comp_data = _aes_decrypt(comp_data, key, nonce, tag)
    offset += comp_size
    return (name, orig_size, comp_size, crc, comp_data, offset)


# ─────────────────────────────────────────────
#  CMP ARCHIVE
# ─────────────────────────────────────────────

class CMPArchive:
    V2_MAGIC = b"CMP2"

    @staticmethod
    def pack(files, output_path, password=None, use_crc=True, compression_level=5):
        file_entries = []
        all_data = bytearray()
        key, salt = None, None
        if password and _HAS_CRYPTO:
            salt = os.urandom(16)
            key = _derive_key(password, salt)
        passes = max(1, compression_level)
        pairs = max(8, compression_level * 3)
        for name, content in files:
            if isinstance(content, str): content = content.encode("utf-8")
            compressed = CMPCompressor.compress(bytes(content), passes=passes, num_pairs=pairs)
            crc = zlib.crc32(content) & 0xFFFFFFFF if use_crc else 0
            payload = compressed
            nonce_bytes, tag_bytes = b"", b""
            if key:
                payload, nonce_bytes, tag_bytes = _aes_encrypt(compressed, key)
            name_bytes = name.encode("utf-8")
            nlen = len(name_bytes)
            entry = struct.pack("<H", nlen) + name_bytes + struct.pack("<II", len(content), len(payload))
            if use_crc: entry += struct.pack("<I", crc)
            if key: entry += nonce_bytes + tag_bytes
            entry += payload
            file_entries.append(entry)
            all_data.extend(entry)
        header = struct.pack("<4sBBB", CMPArchive.V2_MAGIC, CMPCompressor.V2, len(files),
                             (1 if key else 0) | (2 if use_crc else 0))
        if salt: header += salt
        with open(output_path, "wb") as f:
            f.write(header + bytes(all_data))

    @staticmethod
    def pack_raw(raw_files, output_path, password=None, use_crc=True):
        file_entries = []
        all_data = bytearray()
        key, salt = None, None
        if password and _HAS_CRYPTO:
            salt = os.urandom(16)
            key = _derive_key(password, salt)
        for name, content, compressed in raw_files:
            if isinstance(content, str): content = content.encode("utf-8")
            crc = zlib.crc32(content) & 0xFFFFFFFF if use_crc else 0
            payload = compressed
            nonce_bytes, tag_bytes = b"", b""
            if key:
                payload, nonce_bytes, tag_bytes = _aes_encrypt(compressed, key)
            name_bytes = name.encode("utf-8")
            nlen = len(name_bytes)
            entry = struct.pack("<H", nlen) + name_bytes + struct.pack("<II", len(content), len(payload))
            if use_crc: entry += struct.pack("<I", crc)
            if key: entry += nonce_bytes + tag_bytes
            entry += payload
            file_entries.append(entry)
            all_data.extend(entry)
        header = struct.pack("<4sBBB", CMPArchive.V2_MAGIC, CMPCompressor.V2, len(raw_files),
                             (1 if key else 0) | (2 if use_crc else 0))
        if salt: header += salt
        with open(output_path, "wb") as f:
            f.write(header + bytes(all_data))

    @staticmethod
    def unpack(archive_path, output_dir, password=None, skip_crc=False):
        os.makedirs(output_dir, exist_ok=True)
        with open(archive_path, "rb") as f:
            data = f.read()
        hdr = _read_archive_header(data, CMPCompressor.MAGIC, CMPArchive.V2_MAGIC)
        if not hdr: return None
        version, num_files, flags, salt, offset = hdr
        key = None
        if (flags & 1) and password and _HAS_CRYPTO:
            if not salt: return None
            key = _derive_key(password, salt)
        extracted = []
        for _ in range(num_files):
            if version == 1:
                if offset + 2 > len(data): break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data): break
                name = data[offset:offset+name_len].decode("utf-8"); offset += name_len
                if offset + 8 > len(data): break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                if offset + comp_size > len(data): break
                comp_data = data[offset:offset+comp_size]; offset += comp_size
                dec = CMPCompressor.decompress(comp_data)
                if dec is None: continue
                content = dec[:orig_size]
            else:
                r = _read_file_entry_v2(data, offset, flags, key)
                if not r: break
                name, orig_size, comp_size, crc, comp_data, offset = r
                try:
                    dec = CMPCompressor.decompress(comp_data)
                    if dec is None: continue
                    content = dec[:orig_size]
                    if not skip_crc and crc and zlib.crc32(content) & 0xFFFFFFFF != crc:
                        continue
                except Exception:
                    continue
            filepath = os.path.join(output_dir, name)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "wb") as f2:
                f2.write(content)
            extracted.append(name)
        return extracted

    @staticmethod
    def list_files(archive_path, password=None):
        with open(archive_path, "rb") as f:
            data = f.read()
        hdr = _read_archive_header(data, CMPCompressor.MAGIC, CMPArchive.V2_MAGIC)
        if not hdr: return None
        version, num_files, flags, salt, offset = hdr
        key = None
        if (flags & 1) and password and _HAS_CRYPTO:
            if not salt: return None
            key = _derive_key(password, salt)
        elif flags & 1 and not password:
            pass
        result = []
        for _ in range(num_files):
            if version == 1:
                if offset + 2 > len(data): break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data): break
                name = data[offset:offset+name_len].decode("utf-8"); offset += name_len
                if offset + 8 > len(data): break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                offset += comp_size
                result.append((name, orig_size, comp_size, False, False, 0))
            else:
                if offset + 2 > len(data): break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data): break
                name = data[offset:offset+name_len].decode("utf-8"); offset += name_len
                if offset + 8 > len(data): break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                has_crc = bool(flags & 2)
                is_enc = bool(flags & 1)
                crc = 0
                if has_crc:
                    if offset + 4 > len(data): break
                    crc = struct.unpack("<I", data[offset:offset+4])[0]; offset += 4
                if is_enc:
                    if offset + 32 > len(data): break
                    offset += 32
                offset += comp_size
                result.append((name, orig_size, comp_size, has_crc, is_enc, crc))
        return result


# ─────────────────────────────────────────────
#  VH ARCHIVE
# ─────────────────────────────────────────────

class VHArchive:
    V2_MAGIC = b"VHC2"

    @staticmethod
    def pack(files, output_path, password=None, use_crc=True, compression_level=5):
        file_entries = []
        all_data = bytearray()
        key, salt = None, None
        if password and _HAS_CRYPTO:
            salt = os.urandom(16)
            key = _derive_key(password, salt)
        passes = max(1, compression_level)
        pairs = max(16, compression_level * 5)
        for name, content in files:
            if isinstance(content, str): content = content.encode("utf-8")
            compressed = VHCompressor.compress(bytes(content), passes=passes, num_pairs=pairs)
            crc = zlib.crc32(content) & 0xFFFFFFFF if use_crc else 0
            payload = compressed
            nonce_bytes, tag_bytes = b"", b""
            if key:
                payload, nonce_bytes, tag_bytes = _aes_encrypt(compressed, key)
            name_bytes = name.encode("utf-8")
            nlen = len(name_bytes)
            entry = struct.pack("<H", nlen) + name_bytes + struct.pack("<II", len(content), len(payload))
            if use_crc: entry += struct.pack("<I", crc)
            if key: entry += nonce_bytes + tag_bytes
            entry += payload
            file_entries.append(entry)
            all_data.extend(entry)
        header = struct.pack("<4sBBB", VHArchive.V2_MAGIC, VHCompressor.V2, len(files),
                             (1 if key else 0) | (2 if use_crc else 0))
        if salt: header += salt
        with open(output_path, "wb") as f:
            f.write(header + bytes(all_data))

    @staticmethod
    def pack_raw(raw_files, output_path, password=None, use_crc=True):
        file_entries = []
        all_data = bytearray()
        key, salt = None, None
        if password and _HAS_CRYPTO:
            salt = os.urandom(16)
            key = _derive_key(password, salt)
        for name, content, compressed in raw_files:
            if isinstance(content, str): content = content.encode("utf-8")
            crc = zlib.crc32(content) & 0xFFFFFFFF if use_crc else 0
            payload = compressed
            nonce_bytes, tag_bytes = b"", b""
            if key:
                payload, nonce_bytes, tag_bytes = _aes_encrypt(compressed, key)
            name_bytes = name.encode("utf-8")
            nlen = len(name_bytes)
            entry = struct.pack("<H", nlen) + name_bytes + struct.pack("<II", len(content), len(payload))
            if use_crc: entry += struct.pack("<I", crc)
            if key: entry += nonce_bytes + tag_bytes
            entry += payload
            file_entries.append(entry)
            all_data.extend(entry)
        header = struct.pack("<4sBBB", VHArchive.V2_MAGIC, VHCompressor.V2, len(raw_files),
                             (1 if key else 0) | (2 if use_crc else 0))
        if salt: header += salt
        with open(output_path, "wb") as f:
            f.write(header + bytes(all_data))

    @staticmethod
    def unpack(archive_path, output_dir, password=None, skip_crc=False):
        os.makedirs(output_dir, exist_ok=True)
        with open(archive_path, "rb") as f:
            data = f.read()
        hdr = _read_archive_header(data, VHCompressor.MAGIC, VHArchive.V2_MAGIC)
        if not hdr: return None
        version, num_files, flags, salt, offset = hdr
        key = None
        if (flags & 1) and password and _HAS_CRYPTO:
            if not salt: return None
            key = _derive_key(password, salt)
        extracted = []
        for _ in range(num_files):
            if version == 1:
                if offset + 2 > len(data): break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data): break
                name = data[offset:offset+name_len].decode("utf-8"); offset += name_len
                if offset + 8 > len(data): break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                if offset + comp_size > len(data): break
                comp_data = data[offset:offset+comp_size]; offset += comp_size
                dec = VHCompressor.decompress(comp_data)
                if dec is None: continue
                content = dec[:orig_size]
            else:
                r = _read_file_entry_v2(data, offset, flags, key)
                if not r: break
                name, orig_size, comp_size, crc, comp_data, offset = r
                try:
                    dec = VHCompressor.decompress(comp_data)
                    if dec is None: continue
                    content = dec[:orig_size]
                    if not skip_crc and crc and zlib.crc32(content) & 0xFFFFFFFF != crc:
                        continue
                except Exception:
                    continue
            filepath = os.path.join(output_dir, name)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "wb") as f2:
                f2.write(content)
            extracted.append(name)
        return extracted

    @staticmethod
    def list_files(archive_path, password=None):
        with open(archive_path, "rb") as f:
            data = f.read()
        hdr = _read_archive_header(data, VHCompressor.MAGIC, VHArchive.V2_MAGIC)
        if not hdr: return None
        version, num_files, flags, salt, offset = hdr
        key = None
        if (flags & 1) and password and _HAS_CRYPTO:
            if not salt: return None
            key = _derive_key(password, salt)
        result = []
        for _ in range(num_files):
            if version == 1:
                if offset + 2 > len(data): break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data): break
                name = data[offset:offset+name_len].decode("utf-8"); offset += name_len
                if offset + 8 > len(data): break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                offset += comp_size
                result.append((name, orig_size, comp_size, False, False, 0))
            else:
                if offset + 2 > len(data): break
                name_len = struct.unpack("<H", data[offset:offset+2])[0]; offset += 2
                if offset + name_len > len(data): break
                name = data[offset:offset+name_len].decode("utf-8"); offset += name_len
                if offset + 8 > len(data): break
                orig_size, comp_size = struct.unpack("<II", data[offset:offset+8]); offset += 8
                has_crc = bool(flags & 2)
                is_enc = bool(flags & 1)
                crc = 0
                if has_crc:
                    if offset + 4 > len(data): break
                    crc = struct.unpack("<I", data[offset:offset+4])[0]; offset += 4
                if is_enc:
                    if offset + 32 > len(data): break
                    offset += 32
                offset += comp_size
                result.append((name, orig_size, comp_size, has_crc, is_enc, crc))
        return result
