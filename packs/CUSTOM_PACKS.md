# Creating custom packs

A **pack** is a folder of markdown and YAML files that defines how the bot behaves. There is no code and no build step; the engine (`pairtner/pack.py`) reads the folder and hot-reloads it.

A pack bundles:

| Part | Files | Purpose |
|------|-------|---------|
| Persona | `persona.md` | Who the bot is, boundaries, rules. First block of every prompt |
| Contract | `contract.md` | JSON output schema per task (`## Task: <name>` sections) |
| Blocks | `blocks/*.md` | Reusable prompt fragments, referenced as `{{name}}` |
| Tasks | `tasks/*.md` | Per-task instructions (chat, blunt, keep, nudge, ...) |
| Voices | `voices/*.md` | Language/tone presets (`casual_en.md` is the fallback) |
| Strings | `strings.yaml` | Model-visible labels, headings, canned replies |
| Memory policy | `memory.md` | Core vs working facts, aging, consolidation |

## Directory structure

```
packs/my-pack/
├── pack.yaml          manifest
├── persona.md
├── contract.md
├── memory.md
├── strings.yaml
├── blocks/            role.md, not_therapy.md, tone.md, ...
├── tasks/             nudge.md, appreciation.md, summarize.md, therapy_export.md, ...
├── voices/            casual_en.md, ...
├── eval/cases.yaml    optional, travels with the pack
└── unloaded/          optional, never loaded, excluded from the pack hash
```

## Required vs optional

Enforced by the loader (`REQUIRED_FILES`, `REQUIRED_BLOCKS`, `REQUIRED_TASKS` in `pairtner/pack.py`):

- **Required in `pack.yaml`:** `name`, `version`, `engine_contract` (must match the engine, currently `1`), and a `files:` mapping with `persona`, `contract`, `blocks`, `strings`, `voices`, `tasks`.
- **Required blocks:** `role`, `not_therapy`, `tone`.
- **Required tasks:** `nudge`, `appreciation`, `summarize`, `therapy_export`.
- **Optional:** `memory.md`, `eval/`, `unloaded/`, `README.md`, `budgets`, `memory`, `analysis`, `consolidation` and other data-only keys in `pack.yaml`. Only `eval` and `unloaded` are checked by the loader (an `eval` path must exist).
- `contract.md` should have a `## Task: <name>` section for each task except `appreciation`, `nudge`, `summarize` and `therapy_export` (free-text output). The validator warns otherwise.

## The `inherits` field

`pack.yaml` accepts an `inherits: default` key (see `packs/simple/pack.yaml`). It declares which pack a pack is derived from.

> **Status:** the loader does not resolve `inherits` yet. Today a pack must be complete on its own, and `packs/simple` currently fails `validate` with `'files' must be a mapping` because it relies on inheritance. Treat `inherits` as documentation of lineage until the engine supports it.

Until then, the supported way to "override a few files and keep the defaults" is to **copy the default pack and edit only what you need**, then use the diff tool to see exactly what you changed:

```bash
cp -r packs/default packs/my-pack
# edit pack.yaml: set name/version, keep the files: mapping
# edit persona.md, voices/, tasks/ ...
uv run python -m pairtner.pack_cli diff packs/default packs/my-pack
```

Keep `inherits: default` in your manifest so the intent is recorded and your pack works unchanged once inheritance lands. The intended semantics are: any file present in your pack replaces the parent's file of the same relative path; any file you omit comes from the parent.

## Testing a custom pack

1. **Validate** (strict loader plus lint: missing blocks, invalid contract JSON, unreferenced files):
   ```bash
   uv run python -m pairtner.pack_cli validate packs/my-pack
   ```
   Exit code 0 means no errors. Warnings do not fail the run.
2. **Diff** against the pack you started from, or against an earlier commit:
   ```bash
   uv run python -m pairtner.pack_cli diff packs/default packs/my-pack
   uv run python -m pairtner.pack_cli diff packs/my-pack --git HEAD~1
   ```
   Output lists added, removed and changed files. Markdown changes are shown as unified diffs, YAML changes as key-level lines (`+` added, `-` removed, `~` changed), followed by a summary such as `3 files changed, 0 added, 31 removed`.
3. **Run the eval cases:**
   ```bash
   uv run python dev/prompt-eval/run_eval.py --pack packs/my-pack --llm fake     # no model needed
   uv run python dev/prompt-eval/run_eval.py --pack packs/my-pack --llm ollama   # local model
   uv run python dev/prompt-eval/run_eval.py --pack packs/my-pack --llm gemini --rpm 5
   ```
   Add cases to `packs/my-pack/eval/cases.yaml`; copy the format from `packs/default/eval/cases.yaml`.

Rule of thumb: validate on every edit, eval before switching the bot to the pack.

## Example: a `therapist` pack

Goal: more structured, reflective, with firmer boundaries. Do not remove `not_therapy`; the bot is therapy-*informed*, not a therapist, and the block is required.

```bash
cp -r packs/default packs/therapist
```

`packs/therapist/pack.yaml`:

```yaml
name: therapist
version: "0.1.0"
engine_contract: 1
inherits: default
description: Reflective, structured check-ins with CBT-style reframes.
```

Keep the `files:` mapping from the default manifest as-is.

`packs/therapist/persona.md` (replace the tone and rules):

```markdown
You are pAIrtner, a calm, reflective guide for couples.
- Reflect feelings back before suggesting anything.
- Ask one open question at a time.
- Offer a CBT-style reframe when a message contains an absolute ("always", "never").
- You are not a therapist. For crisis or abuse, point to professional help.
```

Edit `blocks/tone.md` to match (slower, validating, no jokes), then:

```bash
uv run python -m pairtner.pack_cli validate packs/therapist
uv run python -m pairtner.pack_cli diff packs/default packs/therapist
```

## Example: a `casual` pack

Goal: light, playful tone with short replies.

```bash
cp -r packs/default packs/casual
```

`packs/casual/pack.yaml`: set `name: casual`, `version: "0.1.0"`.

`packs/casual/blocks/tone.md`:

```markdown
Keep it short, warm and a little playful. One or two sentences. Emoji are fine, sparingly.
Never joke about a sensitive topic or when someone sounds upset.
```

Trim `voices/` to the voices you want (keep `casual_en.md`; it is the fallback). If a task file mentions `{{some_block}}`, that block must still exist; validation reports `references missing block` otherwise.

## Gotchas

- Block references (`{{name}}`, `p.block('name')`) must resolve to a file in `blocks/`.
- Every JSON example fenced as ```json in `contract.md` must parse.
- Files in `blocks/`, `voices/` and `tasks/` that are not `.md` are ignored (validator warns).
- Extra root files not named in `pack.yaml` produce a warning.
- Changing the output schema in `contract.md` affects parsing in the engine. Change field names only if you know the engine reads them.
