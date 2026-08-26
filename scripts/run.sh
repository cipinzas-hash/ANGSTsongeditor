#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MEGA_DEST="${MEGA_DEST:-/untitledless}"
export BATCH_SIZE="${BATCH_SIZE:-100}"

# Pase lo que pase, cerrar sesion de MEGA al salir.
trap 'echo "== Cerrando sesion =="; mega-logout || true' EXIT

echo "== Iniciando sesion en MEGA =="
mega-login "$MEGA_EMAIL" "$MEGA_PASSWORD"

echo "== Procesando lote (maximo $BATCH_SIZE archivos) desde $MEGA_SOURCE hacia $MEGA_DEST =="
python3 "$SCRIPT_DIR/batch_run.py"
