# Prompt packs

A prompt pack is a folder of text files that holds every word the model reads. The engine (Python) decides block order, separators and which data may appear in whose prompt. The pack decides the wording. You can write or tune a pack without touching code. This page is enough to do that without reading the source.

The stock pack is `packs/default/`. Start by copying it:

```bash
cp -r packs/default packs/mine        # then edit name and version in packs/mine/pack.yaml
PROMPT_PACK=packs/mine uv run python -m pairtner
```

## Folder layout

```
packs/mine/
├── pack.yaml        manifest
├── persona.md       who the bot is; first block of every system prompt
├── contract.md      the JSON output contract shown to the model
├── blocks/          role.md, not_therapy.md, tone.md (shared system-prompt blocks)
├── strings.yaml     every other model-visible sentence, heading and label
├── voices/          one .md per voice; casual_en.md is required
├── tasks/           nudge.md, appreciation.md, summarize.md, therapy_export.md
├── eval/cases.yaml  evaluation cases (see "Evaluating a pack")
└── unloaded/        old drafts; never read by the engine
```

Files are read as UTF-8 and stripped of leading and trailing whitespace. The engine adds back the exact blank lines it needs, so do not pad files with newlines to change spacing.

## pack.yaml

| Field | Meaning |
|---|---|
| `name`, `version` | Shown in logs, trace tables and eval result folders. Bump `version` when you change wording, so runs stay comparable. |
| `engine_contract` | Must equal the engine's `ENGINE_CONTRACT` (currently `1`). A different value is refused at startup. |
| `languages` | Informational. |
| `files` | Map of `persona`, `contract`, `blocks`, `strings`, `voices`, `tasks` to paths relative to the pack folder. All six are required. |
| `budgets` | Context budgets per section. Data only for now; the engine does not read them yet. |
| `eval` | Path to the eval cases file (must exist if set). |
| `unloaded` | Folder of reference text. Never loaded, not part of the pack hash, and no `files:` entry may point inside it. |

## Validation

At startup the engine checks that the manifest parses, `engine_contract` matches, every mapped file exists and is not empty, `voices/casual_en.md` exists, `blocks/` has `role`, `not_therapy` and `tone`, `tasks/` has the four task files, and every key below is present in `strings.yaml` as a non-empty string that uses only its allowed placeholders. A problem exits the process with code 2 and a message naming the pack, the file and the problem, before the database is opened. Unknown extra keys in `strings.yaml` only produce a warning. Write a literal brace as `{{` or `}}`.

## strings.yaml keys

Every key is required. A placeholder is a `{name}` the engine fills in; a key may only use the placeholders listed for it.

**System-prompt headings**

| Key | Placeholders |
|---|---|
| `style_heading` | none |
| `task_heading` | none |

**Per-mode intro and tail paragraphs (`#send`, `/mediate`, `#keep`, `#blunt`)**

| Key | Placeholders |
|---|---|
| `tell_intro` | none |
| `tell_tail` | none |
| `mediate_intro` | none |
| `mediate_tail` | none |
| `keep_intro` | none |
| `keep_tail` | none |
| `blunt_intro` | none |
| `blunt_tail` | none |

**System text for the digest and the weekly check-in**

| Key | Placeholders |
|---|---|
| `digest_system` | none |
| `checkin_system` | none |

**Data-block labels in the user prompt**

| Key | Placeholders |
|---|---|
| `label_private_notes` | none |
| `label_shared_notes` | none |
| `label_recent_conversation` | none |
| `label_current_message` | none |
| `label_current_message_blunt` | none |
| `label_relay_message` | none |
| `label_keep_message` | none |
| `label_period` | none |
| `label_shared_context` | none |
| `label_impressions` | none |
| `impressions_intro` | none |
| `impressions_of` | `{about}`, `{who}` |
| `impressions_one_sided` | none |
| `label_request` | none |
| `request_text` | none |
| `partner_a_fallback` | none |
| `partner_b_fallback` | none |

**Goals, partner summary, smart context**

| Key | Placeholders |
|---|---|
| `goals_heading` | none |
| `goals_personal` | none |
| `goals_shared` | none |
| `goals_closing` | none |
| `partner_summary_heading` | none |
| `smart_recent` | `{recent_days}` |
| `smart_relevant` | none |
| `smart_monthly` | none |

**History labels**

| Key | Placeholders |
|---|---|
| `history_assistant` | none |
| `history_user` | none |

**Dampening and voice**

| Key | Placeholders |
|---|---|
| `dampening_very_high` | none |
| `dampening_high` | none |
| `dampening_medium` | none |
| `dampening_low` | none |
| `dampening_blunt_bypass` | none |
| `learned_voice` | `{parts}` |

**Digest and check-in summary**

| Key | Placeholders |
|---|---|
| `digest_facts_heading` | none |
| `digest_relays_heading` | none |
| `digest_request` | none |
| `checkin_partner_a_heading` | none |
| `checkin_partner_b_heading` | none |
| `checkin_shared_heading` | none |
| `checkin_request` | none |
| `checkin_goals_heading` | none |
| `checkin_goals_personal` | none |
| `checkin_goals_shared` | none |
| `checkin_trends_heading` | none |
| `trend_strong` | none |
| `trend_tense` | none |
| `trend_counts` | `{neg}`, `{pos}` |
| `trend_horsemen` | none |
| `checkin_conflicts_heading` | none |
| `conflict_resolved` | `{resolved}`, `{total}` |
| `conflict_open` | `{open_themes}` |
| `conflict_all_resolved` | none |
| `conflict_none` | none |

**Nudges and appreciation**

| Key | Placeholders |
|---|---|
| `partner_fallback` | none |
| `nudge_partner` | `{partner_name}` |
| `nudge_language` | `{lang}` |
| `nudge_facts_heading` | none |
| `nudge_request` | none |
| `appreciation_request` | none |

**Monthly summary**

| Key | Placeholders |
|---|---|
| `summarize_heading` | `{year_month}` |
| `summarize_fact_line` | `{category}`, `{text}`, `{visibility}` |
| `summarize_request` | none |

**Therapy export**

| Key | Placeholders |
|---|---|
| `therapy_intro` | none |
| `therapy_privacy` | none |
| `therapy_timeline_heading` | none |
| `therapy_timeline_line` | `{date}`, `{event_type}`, `{summary}` |
| `therapy_counts_heading` | none |
| `therapy_patterns_heading` | none |
| `therapy_pattern_line` | `{frequency}`, `{pattern}`, `{pattern_type}` |
| `therapy_notes_heading` | none |
| `therapy_shared_heading` | none |
| `therapy_goals_heading` | none |
| `therapy_goal_personal` | `{status}`, `{text}` |
| `therapy_goal_shared` | `{status}`, `{text}` |
| `therapy_ratio_heading` | none |
| `therapy_ratio_line` | `{negative}`, `{positive}`, `{ratio}` |
| `therapy_request` | `{user_name}` |

## contract.md and what the engine parses

`contract.md` tells the model what JSON to return. The engine parses that JSON in `pairtner/llm.py` (`parse_llm_response`). It reads `reply`, `new_facts`, `relay_draft`, `detected_mood`, `patterns_observed`, `partner_themes`, `articulation_note`, `horsemen_detected` and `voice_traits`. If the output is not valid JSON, the whole text becomes the reply and nothing else is read. If you rename or drop a field in the contract, the engine does not adapt: the field is ignored and the feature that used it stops working. Adding explanation, examples or rules around the fields is safe. Privacy rules (a model can never publish a fact on its own, partner text is never shown raw) are enforced in code, whatever the pack says.

## What the engine ignores

- Everything under `unloaded/`.
- Keys in `strings.yaml` that are not in the table above.
- `budgets` and `languages` (data only for now).
- Files in the pack folder that `pack.yaml` does not map.

## Hot reload and Docker

The engine checks file modification times on each call to `pack.current()`. An edited pack file applies to the next message without a restart. If the edit leaves the pack invalid (a half-saved file, a missing key), the last good version stays active and one ERROR is logged until the files change again. The same applies when the pack runs in Docker: `docker-compose.yml` mounts `./packs` at `/app/packs` read-only, so edit on the host.

## Tracing

Every LLM call logs one line on logger `pairtner.trace`:
`llm_call task=<name> model=<m> pack=<name>@<version> pack_hash=<12 hex> prompt_sha=<12 hex>`.
A `pack_loaded` line with the sha256 of every file is logged at startup and whenever a reload changes the pack. The database gets the same information in `llm_calls` (task, model, pack_hash, prompt_sha256) and `pack_snapshots` (pack_hash, name, version, per-file hashes). Only hashes are stored: no prompt text and no user id. To find which pack text produced a reply, match the reply time to its `llm_calls` row, then its `pack_hash` to `pack_snapshots`.

## Evaluating a pack

`eval/cases.yaml` lists cases. Each case is judged by a person reading the reply, not by exact match.

| Field | Meaning |
|---|---|
| `id` | Unique. Used for `--only` and the result file name. |
| `area` | `chat`, `rewrite`, `memory`, `onboarding`, `mediate`, `safety` or `voice` (free text; used by `--area`). |
| `tag` | Message tag such as `#send`, `#keep`, `#blunt`, or null. |
| `message` | What user A sends. A case in area `mediate` without a message sends `/mediate`. |
| `context` | Seed data, see below. |
| `expect.must` / `expect.must_not` | What a good reply does and must not do. Echoed in the summary as a checklist. |
| `expect.memory` | Memory changes the case hopes for. |

Context keys, all optional:

| Key | Seeded as |
|---|---|
| `core`, `working` | Private facts of user A. A leading `[12] ` id is stripped (ids are not supported yet). |
| `shared` | Shared facts of user A. |
| `partner_view` | What B has noticed about A (appears in A's prompt). |
| `a_view`, `b_view` | Perceptions A about B, and B about A, confidence 0.8. |
| `voice` | A's learned voice (JSON), with a message count of 10. |
| `unknown` | Recorded as "not yet supported"; the engine has no profile-gap block yet. |

Memory expectations are recorded, never judged. The engine has no `memory_ops`, `pin_core`, `propose_core` or add/update/archive by id yet, so a non-empty `expect.memory` is stored with status `not_yet_supported` next to the rows the engine actually wrote. `memory: []` is stored as `expects_no_change`.

### Running it

```bash
uv run python dev/prompt-eval/run_eval.py --llm fake      # plumbing check, no network, UPPER(input) replies
uv run python dev/prompt-eval/run_eval.py --llm gemini    # real model; key from the main checkout's .env or GEMINI_API_KEY
```

Options: `--pack`, `--model` (or `EVAL_MODEL`), `--only ID[,ID]`, `--area`, `--rpm` (default 12), `--max-calls` (default 150), `--out`, `--resume RUN_DIR`.

The runner starts the engine in-process on a throwaway database behind the REST adapter. It never starts Telegram and never prints or stores the API key. For each case it registers two fresh users, pairs them, seeds the context, and sends one message as user A.

Results go to `dev/prompt-eval/runs/<pack-name>/<pack-version>/<UTC time>_<llm>-<model>/` (git-ignored):

- `summary.md`: header (pack, version, hash, model, date, counts, stop note) and one section per case in `cases.yaml` order: replies with buttons, relay draft, memory rows written, memory expectation status, must/must-not checklist.
- `results.json`: the same data without prompts.
- `cases/<id>.json`: full record with every LLM call (system prompt, user content, parsed JSON, trace hashes).

Compare two runs of different pack versions with `diff -ru runA/summary.md runB/summary.md`.

### Free-tier limits and stopping

Gemini's free tier allows roughly 15 requests per minute and a few hundred per day. The runner spaces calls to `--rpm` and stops before a case once `--max-calls` calls were made. If the engine returns its fallback reply (the provider answered 429, or was unavailable), the runner marks that case and every remaining case `skipped`, writes partial results, prints whether it was quota or availability, and exits with code 3. Continue with `--resume <run dir>`; only cases that did not complete run again.

A baseline run of the stock pack is kept in `packs/default/eval/baselines/`.
