#!/usr/bin/env python3
"""Lee los ID3 tags reales de un archivo. No confia en el codigo de salida
del tageador: confirma directo sobre el archivo con mutagen.

Uso CLI (modo lote, para el flujo manual/completo):
  verify_tags.py PROCESSED_DIR INCOMPLETE_DIR
Mueve los incompletos fuera de PROCESSED_DIR para que no bloqueen la subida
del resto.

check_file() es reutilizable desde otros scripts (batch_run.py) para
verificar un archivo puntual sin pasar por la CLI.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from tagio import effective, read_tags


def check_file(f: Path) -> dict:
    """Devuelve dict con artista/album/titulo/pista/caratula/ok para un
    archivo puntual. No lanza excepcion: un archivo no legible se reporta
    como incompleto, no rompe al caller.

    ok = artista y titulo REALES (los placeholders -- Unknown Artist,
    Track 01, etc. -- cuentan como ausentes). El album puede faltar: en ese
    caso 'album' es None y el archivo va a la carpeta 'Untitled album'."""
    tags = read_tags(f)
    if not tags["readable"]:
        return {"ok": False, "artist": None, "album": None, "title": None,
                "track": None, "has_art": False, "reason": "no legible"}
    artist = effective(tags, "artist")
    album = effective(tags, "album")
    title = effective(tags, "title")
    ok = bool(artist) and bool(title)
    return {"ok": ok, "artist": artist, "album": album, "title": title,
            "track": tags.get("track"), "has_art": tags["has_cover"],
            "reason": None if ok else "faltan artista/titulo reales"}


def _main_cli():
    PROCESSED_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/musica_procesada")
    INCOMPLETE_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/musica_incompletos")

    exts = {".mp3", ".flac", ".m4a", ".ogg", ".wav"}
    files = sorted(p for p in PROCESSED_DIR.rglob("*") if p.suffix.lower() in exts)

    if not files:
        print(f"ADVERTENCIA: no se encontro ningun archivo de audio en {PROCESSED_DIR}")
        return

    print(f"== Verificando tags de {len(files)} archivo(s) en {PROCESSED_DIR} ==\n")

    incompletos = []
    for f in files:
        r = check_file(f)
        marca = "OK" if r["ok"] else "INCOMPLETO"
        if not r["ok"]:
            incompletos.append(f)
        print(f"[{marca}] {f.name}")
        print(f"    artista={r['artist'] or '?'} | album={r['album'] or '?'} | titulo={r['title'] or '?'} | pista={r['track'] or '?'} | caratula={'si' if r['has_art'] else 'no'}")

    print(f"\n== Resumen: {len(files)-len(incompletos)}/{len(files)} completos, {len(incompletos)} incompletos ==")

    if incompletos:
        print(f"Incompletos (se mueven a {INCOMPLETE_DIR}, NO se suben a MEGA):")
        for f in incompletos:
            rel = f.relative_to(PROCESSED_DIR)
            dest = INCOMPLETE_DIR / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.rename(dest)
            print(f"  - {rel}")

    print("\n(el resto, completo, sigue en su lugar y se sube normalmente)")


if __name__ == "__main__":
    _main_cli()
