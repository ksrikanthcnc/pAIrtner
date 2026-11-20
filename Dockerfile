# pAIrtner Couples Bot
# Default CMD runs the bot (+ REST API on 8080). The dashboard (8765) runs as a
# second compose service from the same image (see docker-compose.yml).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Layer cache: dependencies first
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code and config
COPY pairtner/ pairtner/
COPY packs/ packs/
COPY settings.yaml ./
RUN uv sync --frozen --no-dev

# SQLite DB lives on a shared volume
ENV DB_PATH=/data/pairtner.db
VOLUME /data

# 8080 = REST API, 8765 = admin dashboard
EXPOSE 8080 8765

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('REST_PORT','8080'), timeout=3)" || exit 1

CMD ["python", "-m", "pairtner"]
