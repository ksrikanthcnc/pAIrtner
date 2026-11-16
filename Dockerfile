FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Layer-cache: install deps first
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code and config
COPY couples_bot/ couples_bot/
COPY prompts/ prompts/
COPY settings.yaml ./

ENTRYPOINT ["uv", "run", "--no-dev", "python", "-m", "couples_bot"]
