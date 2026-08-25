#!/usr/bin/env bash
set -euo pipefail

RAW_DIR="/tmp/musica_raw"
PROCESSED_DIR="/tmp/musica_procesada"   # debe coincidir con "directory:" en beets_config.template.yaml
STAGING="/beetstag_staging_tmp"          # carpeta temporal en MEGA, se borra al final
mkdir -p "$RAW_DIR" "$PROCESSED_DIR"

echo "== Iniciando sesion en MEGA =="
mega-login "$MEGA_EMAIL" "$MEGA_PASSWORD"

echo "== Descargando: $MEGA_SOURCE =="
mega-get "$MEGA_SOURCE" "$RAW_DIR"

echo "== Tageando con beets (huella de audio + nombre de archivo) =="
beet -c beets_config.yaml import -q "$RAW_DIR"

echo "== Subiendo resultado tageado a carpeta temporal $STAGING =="
mega-mkdir -p "$STAGING" || true
mega-put -c "$PROCESSED_DIR" "$STAGING"

echo "== Subida a staging confirmada. Borrando rips originales de $MEGA_SOURCE =="
mega-rm -rf "$MEGA_SOURCE"/*

echo "== Moviendo resultado tageado de $STAGING a $MEGA_SOURCE =="
mega-mv "$STAGING"/* "$MEGA_SOURCE"

echo "== Limpiando carpeta temporal $STAGING =="
mega-rm -rf "$STAGING"

echo "== Cerrando sesion =="
mega-logout

echo "Listo. Los rips en $MEGA_SOURCE ya estan tageados."
