#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${ACTION:-run}"
export ACTION

if [ "$ACTION" = "selftest" ]; then
  echo "== selftest (Gemini + Discogs, sin tocar MEGA) =="
  python3 "$SCRIPT_DIR/selftest.py"
  exit 0
fi

if [ "$ACTION" = "stop" ]; then
  python3 "$SCRIPT_DIR/job.py"
  exit 0
fi

# Pase lo que pase, cerrar sesion de MEGA al salir.
trap 'echo "== Cerrando sesion =="; mega-logout || true' EXIT

echo "== Iniciando sesion en MEGA =="
timeout 180 mega-login "$MEGA_EMAIL" "$MEGA_PASSWORD"

echo "== Accion: $ACTION =="
python3 "$SCRIPT_DIR/job.py"
