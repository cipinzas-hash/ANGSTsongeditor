#!/usr/bin/env python3
"""
Orquestador del procesamiento incremental por lotes.

Reemplaza el flujo anterior (descargar TODA la biblioteca cada vez) por uno
que, en cada corrida:
  1. Lista los archivos en MEGA_SOURCE SIN descargarlos (mega-find).
  2. Toma como maximo BATCH_SIZE de esos archivos.
  3. Por cada uno: descarga, tagea (o si ya tiene tags completos, no llama
     a ninguna API), verifica, y si quedo completo lo sube a MEGA_DEST y
     recien AHI borra el original de MEGA_SOURCE (nunca antes de confirmar
     la subida). Si quedo incompleto, el original NO se toca - queda en la
     fuente para reintentarse en una proxima corrida.

No usa manifest/estado persistente: como el original se borra solo despues
de un exito confirmado, la propia fuente ES el estado ("lo que sigue ahi
es lo que falta procesar"). Esto es mas simple que un manifest y evita que
un manifest quede desincronizado de la realidad.

Requiere estar ya logueado en MEGA (mega-login) antes de correr esto.
Requiere DISCOGS_TOKEN (ver tag_with_discogs.py).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tag_with_discogs import process_file
from verify_tags import check_file

MEGA_SOURCE = os.environ.get("MEGA_SOURCE", "").rstrip("/")
MEGA_DEST = os.environ.get("MEGA_DEST", "/untitledless").rstrip("/")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "100"))
AUDIO_EXTS = {".mp3"}

if not MEGA_SOURCE:
    print("ERROR: falta MEGA_SOURCE en el ambiente (debe ser una ruta INTERNA de tu cuenta, no un link publico).", file=sys.stderr)
    sys.exit(2)


def run_mega(args, check=True):
    """Corre un comando mega-* y devuelve el resultado. No usa shell=True
    para no tener problemas con nombres de archivo con caracteres raros."""
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} fallo (exit {result.returncode}): {result.stderr.strip()}")
    return result


def list_source_files():
    """Lista archivos (rutas remotas completas) bajo MEGA_SOURCE sin
    descargar nada. mega-find sin -l imprime solo rutas, una por linea -
    formato confiable incluso con nombres con espacios (a diferencia de
    mega-ls -l, que es una tabla de columnas)."""
    result = run_mega(["mega-find", MEGA_SOURCE, "--type=f"])
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    # filtra solo audio por extension, por las dudas mega-find devuelva otra cosa
    return sorted(l for l in lines if Path(l).suffix.lower() in AUDIO_EXTS)


def main():
    print(f"== Listando archivos en {MEGA_SOURCE} (sin descargar) ==")
    all_files = list_source_files()
    print(f"Encontrados {len(all_files)} archivo(s) de audio pendientes en la fuente.")

    if not all_files:
        print("Nada que hacer, la fuente esta vacia (o ya todo fue procesado).")
        return

    batch = all_files[:BATCH_SIZE]
    remaining_after = len(all_files) - len(batch)
    print(f"Procesando {len(batch)} en esta corrida (quedan ~{remaining_after} para corridas futuras).\n")

    subidos = 0
    incompletos = 0
    errores = 0

    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = Path(tmp) / "raw"
        processed_dir = Path(tmp) / "processed"
        raw_dir.mkdir()
        processed_dir.mkdir()

        for remote_path in batch:
            rel = remote_path[len(MEGA_SOURCE):].lstrip("/")
            print(f"--- {rel} ---")

            local_target_dir = raw_dir / Path(rel).parent
            local_target_dir.mkdir(parents=True, exist_ok=True)

            try:
                run_mega(["mega-get", remote_path, str(local_target_dir) + "/"])
            except RuntimeError as e:
                print(f"    ERROR descargando, se deja en la fuente para reintentar: {e}")
                errores += 1
                continue

            local_file = local_target_dir / Path(rel).name
            if not local_file.exists():
                print(f"    ERROR: la descarga no dejo el archivo donde se esperaba, se deja en la fuente")
                errores += 1
                continue

            try:
                dest = process_file(local_file, raw_dir=raw_dir, processed_dir=processed_dir)
            except Exception as e:
                print(f"    ERROR inesperado tageando, se deja en la fuente para reintentar: {e}")
                errores += 1
                continue

            verify = check_file(dest)
            if not verify["ok"]:
                print(f"    INCOMPLETO ({verify['reason']}) - el original NO se toca, se reintenta en una proxima corrida")
                incompletos += 1
                continue

            remote_dest_dir = f"{MEGA_DEST}/{Path(rel).parent}".rstrip("/.")
            try:
                run_mega(["mega-mkdir", "-p", remote_dest_dir], check=False)
                run_mega(["mega-put", "-c", str(dest), remote_dest_dir + "/"])
            except RuntimeError as e:
                print(f"    ERROR subiendo a {MEGA_DEST}, el original NO se borra: {e}")
                errores += 1
                continue

            # Solo llegamos aca si la subida fue confirmada exitosa.
            try:
                run_mega(["mega-rm", "-f", remote_path])
                print(f"    OK: subido a {remote_dest_dir}/ y original borrado de la fuente")
                subidos += 1
            except RuntimeError as e:
                # La version tageada ya esta en destino pero no pudimos borrar
                # el original. Preferible dejar un duplicado temporal (se
                # resuelve solo, mega-put con el mismo nombre lo pisa la
                # proxima corrida) antes que perder datos.
                print(f"    AVISO: subido OK pero no se pudo borrar el original ({e}). Va a reprocesarse la proxima corrida.")
                subidos += 1

    print(f"\n== Lote terminado: {subidos} subidos y reemplazados, {incompletos} incompletos (originales intactos), {errores} con error (originales intactos) ==")
    if remaining_after > 0:
        print(f"Quedan aproximadamente {remaining_after} archivo(s) sin procesar en la fuente para la proxima corrida.")


if __name__ == "__main__":
    main()
