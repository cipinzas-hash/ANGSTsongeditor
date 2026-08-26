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
from mutagen import File as MutagenFile


def check_file(f: Path) -> dict:
    """Devuelve dict con artista/album/titulo/pista/caratula/ok para un
    archivo puntual. No lanza excepcion: un archivo no legible se reporta
    como incompleto, no rompe al caller."""
    try:
        audio = MutagenFile(f, easy=True)
    except Exception:
        audio = None

    if audio is None:
        return {"ok": False, "artist": "?", "album": "?", "title": "?",
                "track": "?", "has_art": False, "reason": "no legible"}

    artist = (audio.get("artist") or ["?"])[0]
    album = (audio.get("album") or ["?"])[0]
    title = (audio.get("title") or ["?"])[0]
    track = (audio.get("tracknumber") or ["?"])[0]

    has_art = False
    try:
        raw = MutagenFile(f)
        if raw is not None and hasattr(raw, "tags") and raw.tags:
            has_art = any(k.startswith("APIC") for k in raw.tags.keys()) or "covr" in raw.tags
    except Exception:
        pass

    ok = artist != "?" and album != "?" and title != "?"
    return {"ok": ok, "artist": artist, "album": album, "title": title,
            "track": track, "has_art": has_art, "reason": None if ok else "tags incompletos"}


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
        print(f"    artista={r['artist']} | album={r['album']} | titulo={r['title']} | pista={r['track']} | caratula={'si' if r['has_art'] else 'no'}")

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
