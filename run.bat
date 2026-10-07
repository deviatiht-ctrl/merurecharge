@echo off
cd /d "%~dp0"
if not exist .venv (
  echo [meru-auto] Kreye venv...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
echo [meru-auto] Enstale depandans...
pip install -r requirements.txt
if not exist .env (
  copy .env.example .env
  echo [meru-auto] .env kreye - modifye l si nesese.
)
echo [meru-auto] Lanse sou http://localhost:8000
start "" http://localhost:8000
uvicorn app.main:app --reload --port 8000 --host 127.0.0.1
