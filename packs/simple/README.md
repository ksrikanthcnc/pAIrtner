# Simple Pack

Simplified extraction contract for **small local models** (4B parameters):
- Gemma 3 4B (`gemma3:4b`)
- Qwen 3.5 4B (`qwen3.5:4b`)

## Why

Small models struggle with the full contract's 10+ JSON fields. This pack
strips it to just `reply` + `memory_ops` — the minimum viable extraction.

## What's different from default

| Feature | Default | Simple |
|---------|---------|--------|
| JSON fields | 7-10 per response | 2 (reply, memory_ops) |
| Relay drafting | ✅ | ❌ |
| Mood detection | ✅ | ❌ |
| Articulation notes | ✅ | ❌ |
| Horsemen detection | ✅ | ❌ |
| Voice traits | ✅ | ❌ |
| Memory ops | add, revise, archive, pin_core | add, revise, archive |

## Usage

```bash
# In .env or config.yaml:
PACK=simple

# Or via CLI:
uv run python dev/localtest/run_local.py --llm ollama --keep --pack simple
```

## Inherits

Inherits persona, blocks, voices, and tasks from `default`. Only overrides
the contract and budgets.
