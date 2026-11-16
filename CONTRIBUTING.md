# Contributing to pAIrtner

Thanks for your interest in improving pAIrtner!

## Setup

```bash
# Clone the repo
git clone https://github.com/ksrujankanth/pAIrtner.git
cd pAIrtner

# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Set up the project
uv sync

# Run tests
uv run python -m pytest
```

## Development

- **Python 3.13+** required
- All dependencies managed with `uv`
- Tests: `uv run python -m pytest` (all 668+ must pass before submitting)
- Code style: type hints everywhere, docstrings on public functions

## Project structure

```
couples_bot/           # Main package
├── adapters/          # Transport layer (Telegram, WebSocket, CLI)
├── handlers/          # Command handlers (business logic)
├── config.py          # YAML + env config
├── db.py              # SQLite schema and CRUD
├── llm.py             # LLM client (Gemini + OpenAI-compat)
├── prompts.py         # Prompt assembly (loads from prompts/)
└── ...                # Other modules

prompts/               # Editable prompt files (no code changes needed)
├── persona.md         # Bot personality
├── voices/            # Cultural voice presets
└── tasks/             # Task-specific prompts

tests/                 # Test suite
settings.yaml          # All configurable settings
```

## Adding features

1. **New command (simple)**: Add a handler method in the appropriate `handlers/` file, register it in `__main__.py`, add i18n keys for en + te, add tests.

2. **New prompt**: Add a `.md` file in `prompts/tasks/`, reference it in `prompts.py`. No code changes needed if it follows existing patterns.

3. **New voice/vibe**: Add a `.md` file in `prompts/voices/`. The voice system picks it up automatically.

## Rules

- All tests must pass before submitting a PR
- Maintain en + te i18n parity (every English string needs a Telugu counterpart)
- Never hardcode API keys or secrets
- Privacy is structural: partner's raw data must NEVER appear in the other user's prompt
- Keep modules decoupled: adapters don't know about each other, handlers are transport-agnostic

## Testing

```bash
# Run all tests
uv run python -m pytest

# Run specific test file
uv run python -m pytest tests/test_privacy.py

# Run with verbose output
uv run python -m pytest -v
```

Privacy tests (`test_privacy.py`) are the most critical — they verify the core safety guarantee.

## License

MIT — see [LICENSE](LICENSE).
