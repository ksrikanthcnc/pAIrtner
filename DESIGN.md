# Couples Mediator Telegram Bot: Final Design (scaffold)

## Goal
One Telegram bot, two partners. Each chats privately with the bot. The bot keeps per-person memory, rewrites hard messages politely, and delivers only what the sender approves. Partners do not talk directly through it.

## Decisions
- Channel: Telegram bot, long polling (no public URL). Self-hosted by the user; host TBD (PC, Pi, K8s, free VM).
- Language: Python 3, `python-telegram-bot`, `google-genai` (or plain HTTPS), SQLite, managed with `uv`.
- Memory: SQLite is the memory. Gemini keeps nothing between calls. Include all of the user's facts in the prompt (couple scale, no vector DB). Back up the DB file.
- Operator will not read the DB.
- Rejected: Gem/GPT (cannot update knowledge, cannot ping partner); Make/n8n (hard to enforce approval and privacy tests); shared Google Doc as KB (dumps everything to the model).
- Fallback ready-made app: Parley (voice, paid beyond trial).

## LLM (Gemini API, free tier, one account)
Measured limits (AI Studio, free tier). Limits are per model and per project:
| Model | RPM | TPM | RPD |
|---|---|---|---|
| gemini-3.1-flash-lite | 15 | 250K | 500 |
| gemini-3-flash-preview | 5 | 250K | 20 |
| gemini-3.8-flash | 5 | 250K | 20 |
Pro models: not available on free tier. gemini-2.5-flash may 404 on new projects. Preview models can be withdrawn.

Routing (model names in config, with fallback list):
- Default (chat, fact extraction, rewrite): `gemini-3.1-flash-lite`.
- Heavy (`/mediate`, daily digest, long summaries, `#deep`): `gemini-3-flash-preview`, then `gemini-3.8-flash`. Cap self at ~10 calls/day each.
- Thinking budget 0 for latency (about 1s vs 7s).
- ONE call per message: JSON output `{reply, new_facts[], relay_draft|null}` instead of separate calls.
- On 429, read retryDelay and back off. On 404/503, move down the fallback list.
- Daily self-budget counter; warn user when near the cap instead of failing.

## Data model
- `users(tg_id, name, partner_id, created_at)`
- `facts(id, owner_id, text, visibility['private'|'shared'], created_at)`
- `pending(id, sender_id, draft, status['awaiting_approval'|'sent'|'discarded'], send_after)`
- `messages(id, user_id, role, text, ts)` rolling history per user
- `usage(day, model, count)`

## Controlled sharing rules (hard)
1. Nothing from a person's private facts reaches the partner unless that person marks it shared.
2. Even shared content goes out as a short distilled message, never the raw note.
3. Every outgoing message is shown to the sender first: Approve / Edit / Discard.
4. Prompt for user X contains only X's private facts + shared facts. Never the partner's private facts or chat history.
5. Never quote or confirm what the partner said privately.
6. Soften tone, keep substance.
7. `/mediate` uses only shared facts and views each person has approved.
8. `/whatdoyouknow` shows a user only their own facts, with delete. `/forget` removes them.
9. Show "not therapy" notice and crisis/abuse resources (India helplines) on `/start` and `/help`.

## Flows
1. Pairing: A runs `/start` and gets a code. B runs `/join <code>`.
2. Chat: plain message goes to the LLM with that user's context. Reply plus proposed facts; user confirms facts.
3. Tags: `#keep` saves only. `#send` drafts a relay for approval. No tag: bot asks "save, or pass it on gently?". `#deep` uses the heavy model.
4. Cool-down: approved relays wait ~15 min (configurable, skippable with `#now`) and are merged into one calm update.
5. Daily digest (evening, heavy model): neutral summary of shared facts and approved updates to both.
6. Weekly check-in: three questions each, blame-free summary after both answer.

## Config / secrets
- Env vars: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `MODEL_DEFAULT`, `MODEL_HEAVY` (comma list). `.env` gitignored.
- Do NOT copy keys into the repo. The user's key file is `/Users/srkalyan2401/work/tasks/kiro-ide/t` (two keys; use one). Consider rotating since keys appeared in a chat transcript.

## Testing
- Unit test: partner-private facts never appear in the other user's prompt.
- Unit test: nothing is sent to the partner without an approved `pending` row.
- Mock the LLM; one manual end-to-end test with two Telegram accounts.

## Open items
- Hosting target.
- Which key/account to use; check its AI Studio rate-limit page.
- Languages (English first; Hindi/Telugu later).
- Voice (Gemini Live API) is out of scope.

## Reference
- Test scripts: `tmp-kiro/keytest.py`, `limits.py`, `burst.py` (read keys by regex, `KEYIDX` env selects).
- Research PDF: `/Users/srkalyan2401/Downloads/gemini-cop.pdf`.

## Addendum: transport, hosting, export
- Transport: start with long polling (no public URL). Keep handlers transport-agnostic so a webhook can replace polling if moved to serverless.
- Hosting options (verify current free-tier terms): own machine for testing; always-free VM (e.g. Oracle) for always-on with SQLite; serverless webhook (Cloud Run / Lambda / Workers) needs external DB (Neon/Supabase) plus a scheduler for digest and cool-down delivery. Not on Nielsen AWS/KaaS: personal project only. Host operator can read the DB.
- Knowledge stays in SQLite. No Google Docs API. Add `/export`: sends a user their OWN facts as a text file in Telegram.
