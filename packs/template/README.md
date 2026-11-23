# Pack Template

Use this as a starting point for creating a new pAIrtner prompt pack.

## Quick start

```bash
cp -r packs/template packs/my-pack
# Edit the files to match your use case
# Validate, then run eval:
uv run python -m pairtner.pack_cli validate packs/my-pack
uv run python dev/prompt-eval/run_eval.py --pack packs/my-pack --llm ollama
```

## What is a pack?

A pack is a folder of markdown + YAML files that controls how the bot behaves. No code, no Python, no build step. The engine reads the pack and adapts.

A pack defines:
- **Who** the bot is (persona.md)
- **What** the bot returns (contract.md — JSON output schemas per task)
- **How** the bot manages memories (memory.md — aging, consolidation, core vs working)
- **How** the bot speaks (voices/ — cultural/linguistic presets)
- **What** the bot does per task (tasks/ — chat, rewrite, mediate, etc.)
- **Quality bar** (eval/cases.yaml — test cases that travel with the pack)

## Files

| File | Required? | What it does |
|------|-----------|-------------|
| `pack.yaml` | Yes | Manifest: name, version, engine_contract, budgets, file map |
| `persona.md` | Yes | Bot personality, boundaries, rules. First block of every prompt |
| `contract.md` | Yes | JSON output schema per task. The model follows this exactly |
| `memory.md` | Yes | Memory policy: what's core, when to revise, aging rules |
| `blocks/` | Yes | Shared prompt blocks: role.md, not_therapy.md, tone.md |
| `strings.yaml` | Yes | Model-visible labels, headings, canned responses |
| `voices/` | Yes | One .md per voice (casual_en.md is the fallback) |
| `tasks/` | Yes | Per-task instructions: chat.md, blunt.md, keep.md, etc. |
| `eval/cases.yaml` | Recommended | Test cases to verify the pack works |

## Engine contract

The `engine_contract: 1` field in pack.yaml declares which output schema version the pack targets. The engine refuses a pack whose contract version it doesn't support.

## Inheriting from default

`pack.yaml` may declare `inherits: default`, but the loader does not resolve it yet: a pack must be complete on its own. Copy the full default pack and modify what you need, then compare with `uv run python -m pairtner.pack_cli diff packs/default packs/my-pack`. See [CUSTOM_PACKS.md](../CUSTOM_PACKS.md) for the full guide.

## Testing your pack

```bash
# Quick sanity check (fake LLM, no model needed):
uv run python dev/prompt-eval/run_eval.py --pack packs/my-pack --llm fake

# Real model test (needs Ollama running):
uv run python dev/prompt-eval/run_eval.py --pack packs/my-pack --llm ollama

# Production validation (uses Gemini API quota):
uv run python dev/prompt-eval/run_eval.py --pack packs/my-pack --llm gemini --rpm 5
```

## Examples of custom packs

- **Father-son**: asymmetric memory (parent's notes about child are more guarded), different persona ("understanding but firm"), different voice options
- **Roommates**: no romantic context, practical focus (chores, bills, noise), lighter tone
- **Debate partner**: adversarial-but-fair persona, no relay flow, different fact categories
- **Therapy-informed**: stronger boundaries, more structured check-ins, CBT-style reframes
