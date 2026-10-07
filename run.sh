#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "[meru-auto] Kreye venv..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "[meru-auto] Enstale depandans..."
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "[meru-auto] .env kreye - modifye l si nesese."
fi
echo "[meru-auto] Lanse sou http://localhost:8000"
( sleep 2 && ( command -v open >/dev/null && open http://localhost:8000 || true ) ) &
exec uvicorn app.main:app --reload --port 8000 --host 127.0.0.1
