# VEXARCHIVE

Narzędzie do archiwizacji plików z szyfrowaniem AES-256 i kompresją BPE+RLE.

## Pobieranie

[➡ Pobierz najnowszą wersję (exe)](https://github.com/wk12100lol-prog/vexhack.vh/releases/latest)

Nie wymaga Pythona. Wypakuj i uruchom `VEXARCHIVE.exe`.

## Menu kontekstowe

1. Pobierz `setup_context_menu.bat` z [release](https://github.com/wk12100lol-prog/vexhack.vh/releases/latest)
2. Uruchom jako **Administrator**
3. Gotowe! Prawy klik na plik/folder → **VEXARCHIVE** → Pakuj jako CMP / VH

## Formaty

| Format | Rozszerzenie | Opis |
|--------|-------------|------|
| CMP | `.cmp` | Standardowy, domyślny |
| VH | `.vh` | Wyższy poziom kompresji (Very High) |

## Funkcje

- Pakowanie i rozpakowywanie (CMP / VH)
- Szyfrowanie AES-256
- Kompresja BPE + RLE z poziomem 1–10
- CRC32 – weryfikacja spójności
- Podgląd zawartości archiwum
- Naprawa uszkodzonych archiwów
- Porównywanie dwóch archiwów
- Skaner dysku w poszukiwaniu `.cmp` / `.vh`
- Statystyki z wykresem
- Drag & drop
- Samodzielny .exe (nie wymaga Pythona)
- Auto-update

## Budowa z źródła

```bash
pip install PyQt6 cryptography pyinstaller
pyinstaller --onefile --windowed --icon=logo.png --add-data "logo.png;." --name "VEXARCHIVE" main.py
```

## Licencja

MIT — v0idvex
