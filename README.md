# pAIrtner

A private Telegram bot that helps couples communicate more gently. Each partner chats privately with the bot — it softens messages and only passes along what you approve.

## Setup

1. Copy `.env.example` to `.env` and fill in your tokens
2. Install dependencies: `uv sync`
3. Run: `uv run python -m couples_bot`

## Commands

- `/start` — Get a pairing code
- `/join <code>` — Pair with your partner
- `/tell <message>` — Draft a gentle message for your partner
- `/mediate` — Get a neutral perspective on shared issues
- `/whatdoyouknow` — See what the bot remembers about you
- `/forget <id|all>` — Delete your facts
- `/export` — Download your facts as a file
- `/lang <en|te>` — Switch language (English or Telugu)
- `/help` — Show available commands

## Tags

- `#keep` — Save only, no relay
- `#send` — Draft a relay for your partner
- `#deep` — Use a more thoughtful model
- `#now` — Skip cooldown on an approved relay

## LLM Providers

pAIrtner supports two LLM backends:

- **Gemini** (default) — set `GEMINI_API_KEY` in `.env`
- **OpenAI-compatible** — set `LLM_PROVIDER=openai_compat` with `LLM_BASE_URL` and `LLM_API_KEY` (works with Groq, Together, etc.)

See `.env.example` for all configuration options.
