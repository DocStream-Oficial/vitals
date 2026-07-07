#!/bin/bash
# start.sh — arranque dev en Mac
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creando entorno virtual..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

echo "Iniciando Vitals en http://127.0.0.1:8700 ..."
uvicorn main:app --host 127.0.0.1 --port 8700 --reload
