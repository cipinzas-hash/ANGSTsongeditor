#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAW_DIR="/tmp/musica_raw"
PROCESSED_DIR="/tmp/musica_procesada"   # debe coincidir con "directory:" en beets_config.template.yaml
MEGA_DEST="/untitledless"               # carpeta fija de destino, archivos ya tageados
mkdir -p "$RAW_DIR" "$PROCESSED_DIR"

echo "== Iniciando sesion en MEGA =="
mega-login "$MEGA_EMAIL" "$MEGA_PASSWORD"

echo "== Descargando: $MEGA_SOURCE =="
mega-get "$MEGA_SOURCE" "$RAW_DIR"

echo "== Tageando con beets (huella de audio + nombre de archivo) =="
beet -c beets_config.yaml import -q "$RAW_DIR"

echo "== Verificando tags reales de lo procesado =="
python3 "$SCRIPT_DIR/verify_tags.py" "$PROCESSED_DIR"

echo "== Subiendo resultado tageado a $MEGA_DEST =="
mega-mkdir -p "$MEGA_DEST" || true
mega-put -c "$PROCESSED_DIR"/* "$MEGA_DEST"

echo "== Cerrando sesion =="
mega-logout

echo "Listo. Los originales en $MEGA_SOURCE no se tocaron. Resultado tageado en $MEGA_DEST."
