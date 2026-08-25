#!/usr/bin/env python3
"""Lee los ID3 tags reales de cada archivo procesado y los imprime.
No confía en el codigo de salida de beets: confirma directo sobre el archivo.
"""
import sys
from pathlib import Path
from mutagen import File as MutagenFile

PROCESSED_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/musica_procesada")

exts = {".mp3", ".flac", ".m4a", ".ogg", ".wav"}
files = sorted(p for p in PROCESSED_DIR.rglob("*") if p.suffix.lower() in exts)

if not files:
    print(f"ADVERTENCIA: no se encontro ningun archivo de audio en {PROCESSED_DIR}")
    sys.exit(0)

print(f"== Verificando tags de {len(files)} archivo(s) en {PROCESSED_DIR} ==\n")

incompletos = []
for f in files:
    audio = MutagenFile(f, easy=True)
    if audio is None:
        print(f"[NO LEGIBLE] {f}")
        incompletos.append(f)
        continue

    artist = (audio.get("artist") or ["?"])[0]
    album = (audio.get("album") or ["?"])[0]
    title = (audio.get("title") or ["?"])[0]
    track = (audio.get("tracknumber") or ["?"])[0]

    # chequeo de caratula: relee sin easy=True para ver tags binarios (APIC en ID3, etc.)
    raw = MutagenFile(f)
    has_art = False
    if raw is not None:
        if hasattr(raw, "tags") and raw.tags:
            has_art = any(k.startswith("APIC") for k in raw.tags.keys()) or "covr" in raw.tags

    marca = "OK" if artist != "?" and album != "?" and title != "?" else "INCOMPLETO"
    if marca == "INCOMPLETO":
        incompletos.append(f)

    print(f"[{marca}] {f.name}")
    print(f"    artista={artist} | album={album} | titulo={title} | pista={track} | caratula={'si' if has_art else 'no'}")

print(f"\n== Resumen: {len(files)-len(incompletos)}/{len(files)} completos, {len(incompletos)} incompletos ==")
if incompletos:
    print("Incompletos:")
    for f in incompletos:
        print(f"  - {f}")
