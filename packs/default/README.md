# default pack

The stock prompt pack for the pAIrtner engine (v0.1.0 wording).

| Path | What it is |
|------|------------|
| `pack.yaml` | Manifest: name, version, `engine_contract`, file map, budgets |
| `persona.md` | Who the bot is; first block of every system prompt |
| `contract.md` | The JSON output contract the model must follow |
| `blocks/` | `role.md`, `not_therapy.md`, `tone.md`; shared system-prompt blocks |
| `strings.yaml` | Every other model-visible sentence, heading and label |
| `voices/` | One file per voice (`casual_en.md` is the required fallback) |
| `tasks/` | Task prompts for nudges, appreciation, monthly summary, therapy export |
| `eval/` | Test cases for tuning this pack |
| `unloaded/` | Old drafts kept for reference. NOT LOADED by the engine |

Edits to loaded files take effect on the next message without a restart.
A broken edit is ignored (the last good version stays active) and logged.
