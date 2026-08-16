@echo off
REM Field Intelligence — Windows one-command start
REM Prereqs: Python 3.11+ and Node 18+ on PATH. Run from D:\fieldintel.
cd /d %~dp0

pip install -r requirements.txt
if errorlevel 1 exit /b 1

cd web
call npm ci
if errorlevel 1 exit /b 1
call npm run build
if errorlevel 1 exit /b 1
cd ..

echo Starting Field Intelligence on http://127.0.0.1:8000  (Ctrl+C to stop)
uvicorn server.app:app --host 127.0.0.1 --port 8000
