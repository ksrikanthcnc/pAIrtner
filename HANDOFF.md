# Handoff prompt for the new session

Read `DESIGN.md` in this folder first. It is the source of truth. Then build the MVP.

Scope:
1. uv project: `uv init`, `uv add python-telegram-bot python-dotenv` plus a Gemini client (`google-genai`) or plain HTTPS.
2. SQLite schema from DESIGN.md, with a migration/init function.
3. Commands: `/start`, `/join`, `/tell`, `/mediate`, `/whatdoyouknow`, `/forget`, `/export`, `/help`. Use long polling; keep handlers transport-agnostic.
4. Tags: `#keep`, `#send`, `#deep`, `#now`. No tag: bot asks.
5. One LLM module: config-driven model list (default Flash-Lite, heavy = Flash), thinking budget 0, JSON output `{reply, new_facts, relay_draft}`, 429 backoff using retryDelay, fallback on 404/503, daily usage counter.
6. Approval flow with inline buttons (Approve / Edit / Discard) before anything reaches the partner. Cool-down delay before delivery.
7. Prompt builder per user: that user's private facts + shared facts only.
8. Tests (mock LLM): partner-private facts never appear in the other user's prompt; nothing is sent without an approved pending row.
9. Config from env vars / gitignored `.env`; never hardcode or print keys.

Out of scope: voice, weekly check-in scheduler, daily digest (add after MVP), multi-language.

Rules: ask before choosing a hosting target. Commit only once tests pass. Do not read the DB contents when testing with real users.

Keys: ask the user for the Gemini key and put it in `.env`; never commit it.
