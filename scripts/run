#!/usr/bin/env bash
set -euo pipefail

RAW_DIR="/tmp/musica_raw"
PROCESSED_DIR="/tmp/musica_procesada"   # debe coincidir con "directory:" en beets_config.template.yaml
mkdir -p "$RAW_DIR" "$PROCESSED_DIR"

echo "== Iniciando sesion en MEGA =="
mega-login "$MEGA_EMAIL" "$MEGA_PASSWORD"

echo "== Descargando: $MEGA_SOURCE =="
mega-get "$MEGA_SOURCE" "$RAW_DIR"

echo "== Tageando con beets (huella de audio + nombre de archivo) =="
beet -c beets_config.yaml import -q "$RAW_DIR"

echo "== Subiendo resultado a $MEGA_DEST =="
mega-mkdir -p "$MEGA_DEST" || true
mega-put -c "$PROCESSED_DIR" "$MEGA_DEST"

echo "== Cerrando sesion =="
mega-logout

echo "Listo. Revisa $MEGA_DEST en tu MEGA."
