# Multi-stage: build the React app with Node, serve it from the Python image.
#
# The web build is a build-time concern only — the runtime image has no Node in
# it, and web/dist is gitignored precisely so that the deployed bundle is always
# built from the committed source rather than from whatever happened to be on a
# laptop.

# ---- stage 1: build the front end ----
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- stage 2: runtime ----
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY prompts/ ./prompts/
COPY data/ ./data/
COPY scripts/build_eval_artifact.py ./scripts/build_eval_artifact.py
COPY --from=web /web/dist ./web/dist

# var/ holds the SQLite file, uploaded photos and the scrape cache. On a host
# with an ephemeral filesystem this resets on every deploy, which is correct for
# a demo: the seed is the demo, and /api/demo-reset restores it mid-call.
RUN mkdir -p var/uploads var/cache

# The deployed Eval Lab must prove the source inside this image, not display a
# stale laptop artifact. Run the deterministic fixture pipeline during the
# build, fail the image if its executable cases or release gate fail, and then
# remove the temporary evaluation database/captures.
RUN APP_ENV=development \
    LLM_PROVIDER=fixture \
    DATABASE_URL=sqlite:////tmp/fieldintel-build-eval.db \
    DEMO_USERNAME=demo-user \
    DEMO_PASSWORD=Broadpeak-demo-user \
    SESSION_SECRET=build-only-eval-session-secret-not-used-in-runtime \
    python scripts/build_eval_artifact.py \
    && rm -f /tmp/fieldintel-build-eval.db* \
    && rm -rf var \
    && mkdir -p var/uploads var/cache

EXPOSE 8000
# $PORT is set by the host (Render, Fly, Cloud Run); 8000 is the local default.
CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
