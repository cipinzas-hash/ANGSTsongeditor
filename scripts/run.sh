#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DIR="/tmp/musica_raw"
PROCESSED_DIR="/tmp/musica_procesada"
INCOMPLETE_DIR="/tmp/musica_incompletos"  # incompletos se apartan aca, no se suben, no bloquean
MEGA_DEST="/untitledless"               # carpeta fija de destino, archivos ya tageados
mkdir -p "$RAW_DIR" "$PROCESSED_DIR"

# Pase lo que pase (exito, verificacion fallida, o error), cerrar sesion de MEGA al salir.
trap 'echo "== Cerrando sesion =="; mega-logout || true' EXIT

echo "== Iniciando sesion en MEGA =="
mega-login "$MEGA_EMAIL" "$MEGA_PASSWORD"

echo "== Descargando: $MEGA_SOURCE =="
mega-get "$MEGA_SOURCE" "$RAW_DIR"

echo "== Tageando por texto (artista + titulo del nombre de archivo) via Discogs =="
python3 "$SCRIPT_DIR/tag_with_discogs.py" "$RAW_DIR" "$PROCESSED_DIR"

echo "== Verificando tags reales de lo procesado (incompletos se apartan, no bloquean) =="
python3 "$SCRIPT_DIR/verify_tags.py" "$PROCESSED_DIR" "$INCOMPLETE_DIR"

echo "== Subiendo resultado tageado a $MEGA_DEST =="
mega-mkdir -p "$MEGA_DEST" || true
mega-put -c "$PROCESSED_DIR"/* "$MEGA_DEST"

echo "Listo. Los originales en $MEGA_SOURCE no se tocaron. Resultado tageado en $MEGA_DEST."
if [ -d "$INCOMPLETE_DIR" ] && [ -n "$(ls -A "$INCOMPLETE_DIR" 2>/dev/null)" ]; then
    echo "AVISO: quedaron archivos incompletos sin subir (ver detalle arriba en 'Verificando tags')."
fi
