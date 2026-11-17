# pAIrtner Couples Bot — Multi-stage Docker build
#
# Environment variables (set in .env or docker-compose):
#   TELEGRAM_BOT_TOKEN  — required
#   GEMINI_API_KEY      — required for Gemini provider
#   DB_PATH             — default: couples_bot.db
#   LLM_PROVIDER        — gemini (default) or openai_compat
#   LLM_BASE_URL        — for openai_compat provider
#   LLM_API_KEY         — for openai_compat provider
#   ADAPTER             — telegram (default), websocket, cli, both
#   WS_PORT             — WebSocket port, default 8080
#   PROMPT_PACK         — prompt pack directory, default packs/default
#
# Volumes:
#   /app/data            — persistent storage for DB + backups

# --- Builder stage: install uv + dependencies ---
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Layer-cache: install deps first
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# --- Runtime stage: copy venv + source ---
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY --from=builder /app /app

WORKDIR /app

# Copy application code and config
COPY couples_bot/ couples_bot/
COPY packs/ packs/
COPY settings.yaml ./

# Persistent data volume (DB + backups)
VOLUME /app/data

# WebSocket adapter port
EXPOSE 8080

ENTRYPOINT ["uv", "run", "--no-dev", "python", "-m", "couples_bot"]
