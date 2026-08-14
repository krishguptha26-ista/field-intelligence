@echo off
REM Field Intelligence — Windows one-command start
REM Prereqs: Python 3.11+ and Node 18+ on PATH. Run from D:\fieldintel.
cd /d %~dp0

pip install fastapi uvicorn sqlalchemy pydantic httpx python-dotenv google-genai >nul 2>&1

if not exist web\dist (
  cd web
  call npm install
  call npm run build
  cd ..
)

echo Starting Field Intelligence on http://127.0.0.1:8000  (Ctrl+C to stop)
uvicorn server.app:app --host 127.0.0.1 --port 8000
