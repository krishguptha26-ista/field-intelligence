#!/usr/bin/env bash
# One-command start: builds the web app if needed, then serves everything on :8000
set -e
cd "$(dirname "$0")"
if [ ! -d web/dist ]; then
  (cd web && npm install && npm run build)
fi
exec uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}"
