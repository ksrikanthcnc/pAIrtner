# Contributing to pAIrtner

Thanks for your interest in improving pAIrtner!

## Setup

```bash
# Clone the repo
git clone https://github.com/ksrikanthcnc/pAIrtner.git
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
├── prompts.py         # Prompt assembly (text comes from the active pack)
└── ...                # Other modules

packs/default/         # Editable prompt pack (no code changes needed)
├── pack.yaml          # Name, version, engine contract, file map
├── persona.md         # Bot personality
├── contract.md        # JSON output explanation
├── blocks/            # Role, not-therapy, tone
├── strings.yaml       # Other model-visible sentences and labels
├── voices/            # Cultural voice presets
├── tasks/             # Task-specific prompts
└── eval/cases.yaml    # Evaluation cases

tests/                 # Test suite
settings.yaml          # All configurable settings
```

## Adding features

1. **New command (simple)**: Add a handler method in the appropriate `handlers/` file, register it in `__main__.py`, add i18n keys for en + te, add tests.

2. **Change prompt wording**: Edit the pack file (`packs/default/`); it is picked up on the next message. A new task prompt needs a `.md` in `tasks/` and a call site that loads it with `load_task_prompt`. New model-visible text belongs in the pack (`strings.yaml`), never in Python; add the key to `STRING_KEYS` in `pack.py`.

2b. **Evaluate a pack change**: `uv run python dev/prompt-eval/run_eval.py --llm gemini` and compare summaries; see `docs/PACKS.md`.

3. **New voice/vibe**: Add a `.md` file in `packs/default/voices/`. The voice system picks it up automatically.

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
