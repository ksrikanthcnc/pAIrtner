# v2 Design Decisions — Conversation Log

All decisions from the design discussion session (2026-11-18/19).
This is the authoritative reference. The other v2/*.md files should be updated to match.

## Core Architecture

1. **Two core tables: `messages` (raw log) + `knowledge` (LLM-curated facts).** Everything else derives from these. Down from 27 tables in v1.
2. **Support tables kept:** `users`, `user_profiles`, `couple_config`, `auth_tokens`, `usage`, `pending` (relay). ~8 total.
3. **Raw messages include bot replies.** Both user and assistant messages logged. Needed for admin chat view and knowledge worker context.
4. **Knowledge table replaces `facts` table.** Same concept, evolved columns. No separate profile table — user profile data (age, DOB, etc.) stored as core facts.

## Knowledge Table Schema

5. **`source_ids` column is JSON array** (not single int). Points to raw message IDs only, never to other knowledge IDs. Summaries source from multiple messages: `[1,2,5,12]`.
6. **No recursive summary chains.** Summaries always point to raw message IDs. If a summary needs rebuilding, re-read the raw messages. Flat, not chained.
7. **`metadata` JSON column** for extensibility. Any new LLM output field (mood, confidence, etc.) stored here until promoted to a dedicated column. No migration needed for new fields.
8. **`model` column** tracks which model created the knowledge entry. Useful for debugging and future model comparison.
9. **Kinds:** `core` (identity, permanent, always in prompt), `working` (current situations, decays), `episode` (compressed summaries). All weekly summaries become episodes.
10. **Core facts CAN be revised** (e.g. "married 6 years" → "married 7 years"). Core means "always in prompt + no decay", not "immutable."

## Scoring System

11. **Formula:** `score = kind_weight × decay(age) × state_weight × citation_boost`
12. **Weights are developer knobs, not user-facing.** Start with v1 values. Tune based on testing. The ordering matters more than exact numbers (core > episode > working).
13. **Citation = fact was included in LLM prompt.** Automatic. Each time the context builder picks a fact for the prompt window, `citation_count++` and `last_cited_at` updated. Creates natural feedback loop: useful facts stay high, stale facts drop.
14. **Score is precomputed** on writes and citation events. Context builder just does `ORDER BY relevance_score DESC LIMIT N`. One indexed query.
15. **v1 scoring is good enough to start.** Can add `intensity` (user-set weight) later. Don't over-tune before real usage data.

## LLM Strategy

16. **Gemini Flash Lite for everything initially.** 500 RPD is enough for a couple (30-50 messages/day + 5-10 extraction calls). Switchable via config to any OpenAI-compatible endpoint.
17. **Start with accuracy on Gemini, then shrink to local.** Gemini first = isolate the prompt quality. Then test same prompts on smaller models.
18. **Qwen3:8B test results:** Valid JSON 3/3 with thinking OFF. But misses contradictions (~66%) and fact updates (~66%). Fine for simple extraction, not reliable for reasoning tasks like dedup/contradiction. Confirmed: Gemini for reasoning, local for simple extraction (future optimization).
19. **Bigger model (Flash, not Lite) for weekly summaries.** More reasoning needed for consolidation. Mentioned to users that summary uses more quota.
20. **Single LLM call per message:** returns both `reply` (sent to user) AND `memory_ops` (applied to knowledge). Not two separate calls.
21. **No fast filter for dedup.** Send candidates directly to LLM. At <500 facts per user, this is feasible. Add fast filter later if needed (trivial: just a WHERE clause before the LLM call).
22. **Dedup/contradictions handled naturally by LLM.** Include existing facts in prompt + one instruction line: "If you see duplicates or contradictions, include state_changes to resolve them." No special system.

## Context Window (What LLM Sees)

23. **Knowledge:** Core facts (always) + top-K working by score + top-K episodes + FTS5 matches for current message. ~1200-1600 words total.
24. **Recent messages:** Last 10-20 raw messages (both user and bot). Gives conversational coherence.
25. **Time-aware vs blind message cutoff:** Start with blind (last 20). Add session boundaries later if cross-session bleeding is a problem. **TODO: revisit after testing.**
26. **Casual messages ("what's up"):** Same knowledge context. Trust the LLM to respond casually without referencing heavy facts. No different scoring mode needed.

## Summary & Consolidation

27. **Weekly summary job:** Reads raw messages + existing knowledge → produces episode entries. Source facts can be archived after episode creation.
28. **Cascading summaries (1d, 3d, 7d... rolling):** PARKED. Interesting idea for keeping old important things alive in compressed form. Currently handled by scoring (cited things stay high regardless of age). If scoring isn't enough, this is the upgrade path. Architecture supports it (just another background job writing episodes). **NOTE: Keep pluggable — should be easy to add later.**
29. **Episodes point to raw message IDs via `source_ids`.** Can be rebuilt from raw if extraction logic changes.

## Extensibility

30. **Relationship types = persona files, not schema changes.**
    ```
    personas/couple.md          # relationship-specific identity + tone
    personas/parent_child.md
    personas/friends.md
    prompts/chat.md             # task-specific, shared across relationships
    prompts/mediate.md
    prompts/extract.md
    prompts/rewrite.md
    ```
    Adding a new relationship = adding one `.md` file. Zero code changes.

31. **Changing an MD file immediately affects all future LLM calls.** For rewrites/re-extraction, run a rebuild job with the updated prompts.

32. **Group support:** Same engine, same knowledge table. Query all user_ids in the group. Persona file says "you're mediating a group." Relay becomes "draft for the group."

33. **Contract YAML defines what LLM sends/receives.** Not a code generator. Schema changes = SQL migrations (hand-written, 3 lines). New LLM output fields → `metadata` JSON column first, promote to real column when proven valuable.

## Features

34. **DOB/date storage:** Store as "DOB: 1992-03-15" core fact. Background job checks date-approaching facts daily, injects reminders into conversation context. Same pattern for goal deadlines. **New background job needed.**

35. **Archiving flow:** `active → outdated → archived`. Archived = `archived_at` set, excluded from all queries except admin. Triggered by: aging job (uncited 30d), LLM state_change, user delete, or weekly summary.

36. **LLM can dig into sources:** When unsure about a fact, LLM requests raw messages via `source_ids`. Engine fetches and includes in follow-up prompt. Tool/function call pattern.

## Approach

37. **Enhance v1, not full rewrite.** Keep adapters, PWA, relay, tests. Rewrite only: knowledge table + extraction prompt + context builder. ~500 lines new replacing ~2000 lines of over-engineered storage modules.
38. **No v1/v2 engine switch.** Tag v1 (done: `v1.0`), move forward on master. No dual maintenance.
39. **Admin features preserved:** Merged chat view (both users + LLM output), read-only SQLite explorer, runtime config editor.

## Testing

40. **Qwen3:8B local test completed.** Results in `tmp-kiro/qwen-json-test/`. Valid JSON with thinking OFF, unreliable reasoning.
41. **Use `duo_keep` DB for real conversation data.** Read-only reference for extraction prompt testing. Don't overwrite raw messages.
42. **Next step: extraction prompt → test on Gemini Flash Lite → establish accuracy baseline.**

## Build Order

1. Write extraction contract + prompt (30 min)
2. Test on Gemini Flash Lite with real conversation data (30 min)
3. Knowledge table + Memory API v2 (1 session)
4. Wire into existing bot (1 session)
5. Ship. Iterate based on real usage.
