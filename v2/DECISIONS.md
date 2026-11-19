# v2 Design Decisions — Conversation Log

All decisions from the design discussion session (2026-11-18/19).
This is the authoritative reference. The other v2/*.md files should be updated to match.

---

## Terminology

| Term | Definition |
|---|---|
| **Fact** | A single piece of knowledge about a user. One sentence, one idea. E.g. "She's 34", "Hates mornings", "Stressed about interview Friday." |
| **Knowledge** | The curated collection of facts for a user. Stored in the `knowledge` table. Managed by the LLM + engine, never by user input directly. |
| **Raw log** | The `messages` table. Every message (user + bot) appended, never modified. Source of truth for rebuilds. |
| **Core fact** | Identity-level, durable fact. Always included in LLM prompt. No time decay. CAN be revised ("married 6y" → "married 7y") but never auto-archived. |
| **Working fact** | Current situation, temporary. Decays over time (21-day halflife). Gets archived when resolved/outdated. |
| **Episode** | A compressed summary of older facts/events. Created by weekly consolidation job. Replaces the working facts it summarizes. |
| **Citation** | When a fact is included in the LLM's prompt window, that's a citation. `citation_count++` on each inclusion. Creates feedback loop: useful facts score higher → get cited more → stay relevant. |
| **Scoring** | Formula that ranks facts for prompt inclusion: `kind_weight × decay(age) × state_weight × citation_boost`. Precomputed on every write and citation event. Context builder does `ORDER BY relevance_score DESC LIMIT N`. |
| **Memory op** | A structured JSON instruction from the LLM to modify knowledge: add, revise, state change, promote. Validated by the engine before applying. |
| **Persona** | An MD file that defines the bot's identity and tone for a specific relationship type (couple, parent-child, friends). |
| **Prompt** | An MD file with task-specific LLM instructions (chat, mediate, extract, rewrite). Shared across all relationship types. |
| **Contract** | A YAML file defining what JSON the LLM sends/receives. The engine validates against it. |

---

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
    - `kind_weight`: core=1.0, episode=0.7, working=0.5, partner=0.4
    - `decay(age)`: `e^(-age_days / halflife)` — core=∞ (no decay), episode=180d, working=21d
    - `state_weight`: active=1.0, improving=0.9, resolved=0.3, outdated=0.1
    - `citation_boost`: `min(1.0 + 0.1 × citation_count, 1.5)` — capped at 1.5x
    - Core facts always score 1.0 (kind_weight=1.0 × no decay × active=1.0).
    - A 3-month-old working fact with no citations: 0.5 × 0.003 × 1.0 × 1.0 ≈ **0.001** (effectively invisible).
    - An episode cited 5 times, 2 months old: 0.7 × 0.65 × 1.0 × 1.5 ≈ **0.68** (still prominent).
12. **Weights are developer knobs, not user-facing.** Start with v1 values. Tune based on testing. The ordering matters more than exact numbers (core > episode > working).
13. **Citation = fact was included in LLM prompt.** Automatic. Each time the context builder picks a fact for the prompt window, `citation_count++` and `last_cited_at` updated. Creates natural feedback loop: useful facts stay high, stale facts drop.
14. **Score is precomputed** on writes and citation events. Context builder just does `ORDER BY relevance_score DESC LIMIT N`. One indexed query.
15. **v1 scoring is good enough to start.** Can add `intensity` (user-set weight) later. Don't over-tune before real usage data.
16. **All scoring knobs live in `contracts/scoring.yaml`** with descriptions. Single file, easy to tune. Engine reads at startup.

## LLM Strategy

17. **Gemini Flash Lite for everything initially.** 500 RPD is enough for a couple (30-50 messages/day + 5-10 extraction calls). Switchable via config to any OpenAI-compatible endpoint.
18. **Start with accuracy on Gemini, then shrink to local.** Gemini first = isolate the prompt quality. Then test same prompts on smaller models.
19. **Qwen3:8B test results:** Valid JSON 3/3 with thinking OFF. But misses contradictions (~66%) and fact updates (~66%). Fine for simple extraction, not reliable for reasoning tasks like dedup/contradiction. Confirmed: Gemini for reasoning, local for simple extraction (future optimization).
20. **Bigger model (Flash, not Lite) for weekly summaries.** More reasoning needed for consolidation. Mentioned to users that summary uses more quota.
21. **Single LLM call per message:** returns both `reply` (sent to user) AND `memory_ops` (applied to knowledge). Not two separate calls.
22. **No fast filter for dedup.** Send candidates directly to LLM. At <500 facts per user, this is feasible. Add fast filter later if needed (trivial: just a WHERE clause before the LLM call).
23. **Dedup/contradictions handled naturally by LLM.** Include existing facts in prompt + one instruction line: "If you see duplicates or contradictions, include state_changes to resolve them." No special system.

## Context Window (What LLM Sees)

24. **Knowledge:** Core facts (always) + top-K working by score + top-K episodes + FTS5 matches for current message. ~1200-1600 words total.
25. **Recent messages:** Last 10-20 raw messages (both user and bot). Gives conversational coherence.
26. **Time-aware vs blind message cutoff:** Start with blind (last 20). Add session boundaries later if cross-session bleeding is a problem. **TODO: revisit after testing.**
27. **Casual messages ("what's up"):** Same knowledge context. Trust the LLM to respond casually without referencing heavy facts. No different scoring mode needed. Casual messages don't inflate the knowledge table — the LLM simply returns empty `memory_ops` when there's nothing to learn.
28. **Context injection for time-sensitive facts:** Date-based facts (birthdays, deadlines, goals) are injected into the context when their date approaches, REGARDLESS of their score. This is rule-based, not score-based: a background job marks approaching-date facts with a `context_inject: true` flag, and the context builder includes them unconditionally. Example: "Partner's birthday in 3 days" appears in context even if its score is low. This also means the LLM might naturally weave it into conversation ("By the way, isn't your anniversary coming up?") without being explicitly asked.

## Summary & Consolidation

29. **No tags.** FTS5 search + scoring + `category` column handle discovery. Tags are manual curation burden nobody maintains at this scale.
30. **Episodes grouped by topic, not time.** The LLM decides thematic grouping when creating episodes. "Kitchen conflict during in-law visit" is one episode regardless of whether it spanned 1 day or 3 weeks. Time-based bucketing ("Nov week 3") is meaningless for personal facts.
31. **No separate topic/index table.** FTS5 IS the index. A topic table needs its own maintenance, dedup, staleness — the 27-table path again.
32. **Summary cadence:**
    - **Per-message:** LLM extracts facts inline (real-time, part of the reply call).
    - **Daily:** No fixed schedule. Instead, trigger consolidation when active fact count exceeds a threshold (e.g. 50). Event-driven, not calendar-driven.
    - **Weekly:** The primary maintenance job. Compresses and organizes the knowledge base.
    - **Monthly/Quarterly:** Consolidate weekly episodes into bigger episodes. Same mechanism, wider scope. Start with weekly, add layers when needed. This is the cascading summary idea — implemented as stacking consolidation passes, not as a separate system.
33. **Weekly summary flow:**
    1. Gather working facts with state `resolved` or `outdated` (NOT active — those are current situations, not ready for summarization)
    2. LLM groups by theme/category (not us — the LLM decides what goes together)
    3. For each group of 3+ facts → LLM summarizes into one episode (groups <3 stay as working facts — not worth an episode, they decay or get cited naturally)
    4. Episode stored: `kind: episode`, `source_ids: [raw msg IDs from all source facts]`
    5. Source facts → `archived_at` set
    6. Uses bigger model (Flash, not Lite) for better reasoning
    7. LLM returns array of episodes — one call can produce multiple topic-grouped episodes
    - One LLM call per topic group. Episodes persist with scores, get cited like any fact. Old episodes with high citations stay prominent, uncited episodes fade via normal decay.
34. **Cascading summaries (1d, 3d, 7d... rolling):** PARKED. Currently handled by scoring. If scoring isn't enough, this is the upgrade path. Architecture supports it. **NOTE: Keep pluggable.**
35. **Episodes point to raw message IDs via `source_ids`.** Can be rebuilt from raw if extraction logic changes.

## Extensibility

32. **Relationship types = persona + prompt override files.**

    Structure:
    ```
    personas/
    ├── _base.md                # shared identity rules (tone, safety, memory policy)
    ├── couple.md               # imports _base.md + couple-specific tone/rules
    ├── parent_child.md         # imports _base.md + parent-child specifics
    └── friends.md              # imports _base.md + friend specifics

    prompts/
    ├── _base_chat.md           # shared chat instructions
    ├── _base_mediate.md        # shared mediation instructions
    ├── couple/
    │   ├── chat.md             # couple chat (imports _base_chat + couple overrides)
    │   └── mediate.md          # couple mediation (romantic conflict style)
    ├── parent_child/
    │   ├── chat.md             # parent-child chat (respect authority dynamics)
    │   └── mediate.md          # parent-child mediation (generational gaps)
    └── friends/
        ├── chat.md
        └── mediate.md
    ```

    **How it works:**
    - `_base.md` files define shared rules (safety, memory policy, JSON contract).
    - Relationship-specific files import the base and add/override rules.
    - Code loads `personas/{type}.md` + `prompts/{type}/chat.md` at runtime.
    - The memory engine, scoring, relay — all identical regardless of type.
    - Adding a new relationship type = create a persona file + prompt folder. Copy an existing one, tweak the tone.

    **Example difference — couple vs parent-child mediation:**
    - `couple/mediate.md`: "Both partners are equals. Never take sides. Focus on emotional needs and compromise."
    - `parent_child/mediate.md`: "Respect the generational difference. Help the parent understand modern perspectives. Help the child understand the parent's concerns come from care. Never undermine parental authority."

    **Import/hierarchy:** Simple string concatenation at load time. `couple/chat.md` starts with `<!-- import: ../_base_chat.md -->`. The loader reads the base, appends the override. Not a framework — just file concatenation.

33. **Changing an MD file immediately affects all future LLM calls.** For rewrites/re-extraction, run a rebuild job with the updated prompts.

34. **Group support:** Same engine, same knowledge table. Query all user_ids in the group. Persona file says "you're mediating a group." Relay becomes "draft for the group."

35. **Contract YAML defines what LLM sends/receives.** Not a code generator. Schema changes = SQL migrations (hand-written, 3 lines). New LLM output fields → `metadata` JSON column first, promote to real column when proven valuable.

## Features

36. **DOB/date storage:** Store dates in ISO format as core facts: `"DOB: 1992-03-15"`, `"Anniversary: 2020-06-15"`, `"Goal deadline: 2027-01-15"`. Background job scans core facts daily for date patterns, injects approaching dates into context (see decision 28). This enables:
    - LLM naturally mentioning upcoming events in conversation
    - Partner being aware that the other might be thinking about an event
    - Goal deadline awareness without explicit reminder commands

37. **Archiving flow:** `active → outdated → archived`. Archived = `archived_at` set, excluded from all queries except admin. Triggered by: aging job (uncited 30d), LLM state_change, user delete, or weekly summary.

38. **LLM can dig into sources:** When unsure about a fact, LLM requests raw messages via `source_ids`. Engine fetches and includes in follow-up prompt. Tool/function call pattern.

## Configuration

39. **All tunable knobs in `contracts/scoring.yaml`** with descriptions:
    ```yaml
    # All scoring and context parameters. Engine reads at startup.
    # Developer knobs — not user-facing.

    scoring:
      kind_weight:
        core: 1.0        # identity facts, always in prompt
        episode: 0.7     # compressed summaries of past periods
        working: 0.5     # current situations, temporary
        partner: 0.4     # unverified impressions about partner
      halflife_days:
        core: null        # infinite — no decay
        episode: 180      # slow fade over 6 months
        working: 21       # fast fade over 3 weeks
        partner: 30       # moderate fade
      state_weight:
        active: 1.0
        improving: 0.9
        resolved: 0.3
        outdated: 0.1
      citation_boost_per: 0.1    # +0.1 per citation
      citation_boost_max: 1.5    # cap at 1.5x
      score_cap: 1.0             # max score

    context:
      budget_words:
        core: 300
        working: 400
        episodes: 200
        fts_matches: 200
        recent_messages: 500
        total: 1600
      recent_message_count: 20   # last N messages included
      fts_match_limit: 5         # top N FTS5 results per message

    aging:
      uncited_outdated_days: 30  # mark as outdated after N days without citation
      outdated_archive_days: 14  # archive N days after becoming outdated
      transient_archive_days: 14 # auto-archive transient facts after N days

    consolidation:
      interval_days: 7           # run weekly
      min_facts_for_episode: 3   # need at least N facts to consolidate
      max_facts_per_episode: 10  # summarize up to N facts into one episode

    date_injection:
      lookahead_days: 7          # inject date facts N days before they occur
      scan_interval_hours: 24    # how often to scan for approaching dates
    ```

## Approach

40. **Enhance v1, not full rewrite.** Keep adapters, PWA, relay, tests. Rewrite only: knowledge table + extraction prompt + context builder. ~500 lines new replacing ~2000 lines of over-engineered storage modules.
41. **No v1/v2 engine switch.** Tag v1 (done: `v1.0`), move forward on master. No dual maintenance.
42. **Admin features preserved:** Merged chat view (both users + LLM output), read-only SQLite explorer, runtime config editor.

## Testing

43. **Qwen3:8B local test completed.** Results in `tmp-kiro/qwen-json-test/`. Valid JSON with thinking OFF, unreliable reasoning.
44. **Use `duo_keep` DB for real conversation data.** Read-only reference for extraction prompt testing. Don't overwrite raw messages.
45. **Next step: extraction prompt → test on Gemini Flash Lite → establish accuracy baseline.**

## Safety & Operations

49. **NEVER push to GitHub from Mac.** Remote removed. All pushes done manually by user from personal machine. Nielsen security flagged the GitHub remote URL config — the push that triggered the alert was from WSL, not Mac. Mac only uses `git bundle` + `scp` for sync.
50. **Knowledge is rebuildable from raw.** To switch LLM or fix bad extraction: archive all knowledge → process raw messages in chronological batches of 50 → extract facts per batch → after all batches, run one consolidation pass. No need to simulate daily/weekly cadence — batch extraction + final consolidation produces equivalent results. Build a `rebuild` command for this.
51. **Raw messages are NEVER archived or deleted.** They're the rebuild source. Episodes archive working facts (knowledge), never raw messages. This is the whole point of the two-table design.
52. **Drop `category` column.** LLM categorizes inconsistently ("household" vs "home" vs "domestic"). FTS5 + scoring handles retrieval. Weekly summary LLM groups thematically at consolidation time — no pre-assigned category needed. One less field for LLM to fill wrong.

## Proactive Context Injection (decision 28, broadened)

46. **`context_inject` is a general mechanism, not just dates.** Any fact the LLM should see regardless of its score. Use cases found so far:
    - Approaching dates (birthdays, deadlines, anniversaries)
    - Unresolved conflicts older than N days
    - Goals with approaching deadlines
    - Partner inactivity (hasn't messaged in N days — context, not notification)
    - Build the injection mechanism once, add scan rules incrementally as we find more use cases.

47. **Scheduled proactive messages are intelligent, not blind cron.** Before firing a check-in or follow-up, the engine checks recent messages. If the topic was already discussed organically, skip. Implementation: the scheduled job includes recent messages in its LLM call; the LLM decides whether to send or skip. Not a dumb timer.

48. **LLM-set timers (future, parked for MVP).**
    - LLM proposes: `"timer": {"check_in": "3 days", "topic": "how did the conversation with mom go?"}`
    - Engine stores in a `timers` table, fires as a proactive message when due.
    - **Cancellation/reset:** If the user addresses the topic before the timer fires (e.g. LLM set a "check back about interview" timer but user says "interview went great" the next day), the timer should be cancelled. Two approaches:
      - **(a) Include active timers in context:** Add pending timers to the LLM prompt. The LLM can then return `"cancel_timer": <id>` when the topic is resolved naturally. Requires increased context budget for timers.
      - **(b) Timer scan at fire time:** When a timer is about to fire, the engine checks if recent messages already cover the topic (keyword/FTS match). If yes, auto-cancel without firing.
    - Likely: both. (a) for explicit cancellation, (b) as a safety net.
    - **NOTE:** This is a v2.1 feature. For MVP, engine-driven schedules only. But the `timers` table and the cancellation design should be noted so we don't paint ourselves into a corner.

## Build Order

1. Write extraction contract + prompt (30 min)
2. Test on Gemini Flash Lite with real conversation data (30 min)
3. Knowledge table + Memory API v2 (1 session)
4. Wire into existing bot (1 session)
5. Ship. Iterate based on real usage.


## Temporal Awareness (Decision 53-57)

53. **Timestamps in context.** `get_recent_messages` now returns `created_at`. The prompt formatter shows relative times (`[5 min ago]`, `[6h ago]`) and gap markers (`--- 6 hours gap ---`) when >2 hours pass between messages. Fixes the duo_keep bug where "hows she doing recently?" after a 6-hour gap was misinterpreted as a continuation of the previous emotional thread.

54. **Current time in system prompt.** `build_system_prompt` includes `Current time: YYYY-MM-DD HH:MM UTC` so the LLM has temporal grounding. Enables time-aware responses ("it's been a while since we talked").

55. **All storage is UTC.** Display and date comparisons (e.g. proactive birthday checks) use local time. Consistent throughout.

56. **Date regex is production code** (`core/proactive.py`), not test-only. Scoped to proactive injection — scans core facts for date keywords (birthday, anniversary, deadline, DOB). Not on the hot path.

57. **PWA memory panel** — not built yet. Listed as future work. The REST API endpoints for it already exist from v1.

## Session Gap Awareness

The screenshot showed the critical problem: after a 6-hour gap, the bot treated "hows she doing recently?" (asking about the partner) as if the user was expressing sadness. Root causes:
- No timestamps → LLM sees flat message list with no temporal markers
- No "current time" → LLM can't compute "recently" relative to anything
- Recent messages dominated by emotional thread → LLM continued that thread

Fix: timestamps + gap markers + current time = LLM sees the conversation as two distinct sessions separated by hours, not one continuous thread.


## LLM-Driven Dates, Absolute Timestamps, LAN Access (Decision 58-62)

58. **No regex for dates.** Date detection is fully LLM-driven. The extraction contract includes `date` (YYYY-MM-DD) and `recurring` (boolean) fields. The LLM sets them during fact extraction. Proactive injection queries the structured `event_date` column directly — no parsing of prose text. Eliminates false positives and makes date handling extensible (any date the LLM can identify, not just regex patterns).

59. **Absolute timestamps in prompt.** Changed from relative (`[5h ago]`) to absolute (`[2026-11-19 08:30]`). Relative timestamps go stale if the prompt is cached, logged, or reused. The LLM has `Current time:` in the system prompt and can compute recency itself.

60. **LAN access via `--lan` flag.** `uv run python dev/localtest/run_local.py --lan` binds to `0.0.0.0` instead of `127.0.0.1`. Prints the LAN IP for easy Android access. Default remains localhost-only for security.

61. **"Save privately" button** is the pAIrtner bot's own UI: saves the bot's response as a private fact (only the user sees it). "Pass it on gently" drafts a relay. This is the v1 memory system. Auto-save (append-only raw log) is separate and always on.

62. **Multiple iterations for weaker models.** For local models that miss extraction on first pass, a verification/refinement loop: extract → pass results back → "did you miss anything?" Cheap second pass. Not built yet, noted for when we move extraction to local Qwen.


## Memory Growth Analysis & Cascading Consolidation (Decision 63-65)

63. **Episode consolidation is needed.** After a year, ~100+ weekly episodes compete for prompt space. Without cascading, the context fills with moderately-scored summaries. Fix: Level 0 (working) → Level 1 (weekly) → Level 2 (monthly, after 30d) → Level 3 (quarterly, after 90d). Source episodes archived after consolidation. Growth stabilizes at ~66-82 facts regardless of age.

64. **"Save privately" button** is ONLY about the relay draft — dismissing the suggestion to forward something to the partner. Facts are auto-saved by memory_ops on every message. The user never manually saves facts. Clarified in DECISIONS.md for future agents.

65. **Docker always binds 0.0.0.0** inside the container (container networking requires it). Host-side binding is controlled by the `ports:` mapping in docker-compose. REST_BIND variable controls the host interface.


## Relay Flow, Prompt Timing, Cascading Strategy (Decision 66-74)

66. **Relay flow clarified.** LLM generates `relay_draft` in same response as `reply` + `memory_ops`. Buttons shown: "Share with partner" (schedules relay) / "Keep to myself" (dismisses draft). No second LLM call needed. LLM can also return empty `relay_draft` for casual messages where relay makes no sense.

67. **LLM controls relay suggestion, never auto-sends.** The LLM decides whether to propose a relay and writes the draft, but the user ALWAYS approves before delivery. The LLM can explicitly say "no relay" by returning empty relay_draft.

68. **Relative time OK in prompts for token efficiency.** Store absolute (ISO) in DB. Show relative (`[3h ago]`, `[2d ago]`) in the prompt since `Current time:` is already in the system prompt as absolute reference. Saves the LLM from computing time differences. Reverting decision 59 (absolute in prompt) back to relative.

69. **Paging is score-based, not offset-based.** Context builder picks top-K facts by relevance_score. That IS paging — the LLM gets a window into the knowledge base, ranked by importance. For explicit search (v2.1 "search my memory" tool), that would be offset/limit paging with a query.

70. **Without episodes, many memory calls needed.** 500 individual working facts competing for 30 context slots = poor coverage. Episodes compress 10-20 facts into 1, so 30 slots cover 300-600 facts worth of information. Fewer LLM calls, better coverage, lower cost.

71. **Our architecture IS agentic memory.** Scoring + episodes + archival = the same tier architecture as MemGPT/Letta. The difference: we do pre-conversation context building (engine selects facts before LLM sees the message). Adding FTS5 search tool (v2.1) makes us fully agentic (LLM can search on-demand mid-conversation).

72. **Cascading consolidation strategy (concrete).** NOT refresh-all-weekly. Age-band based:
    - Day 1-7: raw working facts (no consolidation)
    - Day 8-30: weekly episodes (consolidate resolved facts older than 7d)
    - Day 31-90: monthly episodes (consolidate weekly episodes older than 30d)
    - Day 91+: quarterly episodes (consolidate monthly episodes older than 90d)
    Each level only touches its own tier's old entries. Jobs run weekly but act on different age bands. A 1-year-old important fact lives as a quarterly episode. Today's stuff stays as raw working facts.

73. **Any fact should be retrievable with enough effort.** Two retrieval paths: (1) scoring-based pre-loaded context, (2) FTS5 search on-demand. Combined = safety net. Test plan: plant facts at various ages, verify retrieval via both paths independently and combined.

74. **Stress test before cascading.** Build a stress test with 6-12 months of simulated data. Test weekly consolidation alone first. If context quality degrades at scale, THEN implement monthly/quarterly layers. Don't build cascading speculatively.


## Stress Test Results & Cascading Implementation (Decision 75-76)

75. **Stress test results (1 year, no cascading):**
    - 1835 working facts seeded → 362 weekly episodes after consolidation
    - Context window (1200 words) fits 187/402 facts = 46% coverage
    - 55% of episodes invisible to LLM = information loss confirmed
    - Scoring works correctly: old uncited facts score near 0, core always 1.0
    - Context build stays <100ms even at scale
    - **Conclusion: cascading consolidation is needed, not optional.**

76. **Cascading consolidation implemented and wired:**
    - `run_cascading_consolidation()` in consolidation.py
    - Monthly: consolidates weekly episodes >30d old → fewer monthly episodes
    - Quarterly: consolidates monthly episodes >90d old → broad overviews
    - Episodes tagged with metadata `{"level": "weekly|monthly|quarterly"}`
    - Maintenance loop runs cascading monthly (30d interval via user_meta)
    - Tested: 88 weekly episodes → 18 monthly episodes (5x compression)


## LLM Comparison, Paging, Button Rename (Decision 77-82)

77. **Paging for LLM search (v2.1).** When the LLM uses FTS5 "search my memory" tool, results should be paginated: `search_facts(query, limit=5, offset=0)`. LLM asks for page 1, reads, asks for page 2 if needed. Separate from pre-loaded scored context. FTS5 infrastructure is ready.

78. **Consolidation bound architecture clarified:**
    - Weekly: FACT lifecycle-based. Compresses ANY resolved/outdated working fact → weekly episode. No age upper bound (facts get resolved organically week by week).
    - Monthly: EPISODE age-based. Weekly episodes aged 30-90d → monthly episodes.
    - Quarterly: Monthly episodes aged 90d+ → quarterly episodes.
    - Bounds are on the cascading layer, not the weekly layer.

79. **Button rename:** "Save privately" → "🤫 Keep to myself", "Pass it on gently" → "💬 Share with partner". Clearer what each action does.

80. **gemini-2.0-flash-lite deprecated.** Google returns 404, recommends `gemini-3.5-flash-lite`. Update all model references.

81. **LLM comparison results (same 7 messages, no existing knowledge):**
    | | Gemini 3.5 Flash Lite | Qwen3 8B (local) |
    |---|---|---|
    | Facts extracted | 4 | 7 |
    | Valid JSON | ✅ | ✅ |
    | Key themes hit | 4/6 | 4-5/6 |
    | Intensity range | 0.7-0.9 | 0.6-0.9 |
    | Granularity | Coarser (merged "lost" + "communication") | Finer (separate facts per person) |
    | Missing | Kids asking for Bob, Bob trying to provide | Varies per run |

    **Assessment:** Qwen3 8B is comparable to Gemini for extraction. It's more granular (7 vs 4 facts) which may be better for knowledge building. Both produce valid JSON with reasonable intensity scores. The quality gap is smaller than expected — local models are viable for extraction.

82. **Two-iteration refinement for local models (parked).** Qwen3 8B does well enough on first pass. If we see quality issues in real usage, add a verification pass. Not needed now.


## Relay UX, Quality Checks, Timeline, Paging (Decision 83-89)

83. **Single relay button.** Removed "Keep to myself" — the default is don't share (just dismiss). Only one button: "💬 Share with partner". If the user does nothing, the draft dies. Simpler UX.

84. **Consolidation quality validation.** Two checks: (1) Episodes shorter than 8 words are rejected — prevents meaningless one-liner summaries. (2) Coverage logging: if <50% of input facts are covered by episodes, warns in logs. Quality check via second LLM call parked for now — start with these structural checks.

85. **Fixed-window consolidation schedule.** Weekly runs once per 7d (tracks last run in user_meta). Monthly runs once per 30d. Quarterly once per 90d. No overlap, no reprocessing. The age-based filtering in `_find_episode_candidates` (30-90d for monthly, 90d+ for quarterly) naturally enforces window boundaries.

86. **Dynamic pagination for LLM search.** Search tool accepts `limit` param from the LLM. Default `limit=5`, LLM can ask for `limit=10`, `limit=20` on subsequent pages. Not over-engineering — it's one parameter on an existing function. FTS5 already returns ordered results.

87. **Episodes are always multi-row, topic-grouped.** The consolidation prompt says "group by theme, NOT by time" and returns an array. 20 resolved facts → 3-5 episodes (one per topic). Each episode links to its specific source fact IDs via `source_ids`. Cross-topic connections are the LLM's job at retrieval time, not at storage time. Confirmed: this is already the implementation.

88. **Gemini 2.0 Flash Lite deprecated.** Google returns 404 with redirect to gemini-3.5-flash-lite. Update all model references.

89. **LLM comparison validated.** Qwen3 8B (local) produces 7 facts vs Gemini 3.5 Flash Lite's 4 facts on same input. Both valid JSON, both reasonable. Local models are viable. Quality gap smaller than expected.


## Benchmark, Boundaries, Debug Mode, Privacy (Decision 90-99)

90. **Benchmark framework built.** `dev/benchmark/seed_db.py` creates a hypothetical 3-month DB (42 messages, ground truth). `run_benchmark.py` tests any model and scores against ground truth. Results saved as JSON.

91. **Benchmark results:**
    | Model | Alice accuracy | Bob accuracy | Latency |
    |---|---|---|---|
    | Qwen3 8B (local) | 73% (11/15) | 29% (4/14) | 28-52s |
    | Gemini 3.5 Flash Lite | 80% (12/15) | 57% (8/14) | 7-9s |
    Key finding: both models miss cross-user facts. Bob's extraction doesn't find "Alice works as PM" because it only sees Bob's messages. This is by design (privacy), but means the knowledge is per-user.

92. **Consolidation boundaries are creation-date based, not content-date.** A weekly episode created on day 28 becomes monthly-eligible on day 58. No content-date overlap. Clean separation.

93. **Consolidation schedule is explicit, not heuristic.** Weekly = `days_since_last >= 7` from user_meta. Monthly = `days_since_last >= 30`. Quarterly = monthly episodes aged 90+. Logged in maintenance loop. No "lucky hunch."

94. **Quality checks: keep structural, warn on subjective.** Structural: min 8 words per episode, source_ids coverage check. Subjective: warn in logs, don't auto-reject. User can inspect via debug mode.

95. **Debug mode for users.** Add `/debug` command showing: fact count by kind/status, top-5 scored facts, recent episodes, FTS5 search test. Helps users understand what's stored without reading raw DB.

96. **Privacy model confirmed solid.** Facts default to `private` visibility. Private facts NEVER sent to partner's prompt. Verified by 8+ tests: `test_prompt_excludes_partner_private_facts`, `test_partner_private_facts_never_leak_in_any_prompt_type`, `test_whatdoyouknow_shows_only_own_facts`, etc. Sensitive scenario (wanting partner to exercise) is handled: extracted as private fact, partner never sees it.

97. **Dynamic pagination for search.** LLM can pass `limit` to search. Start with 5, ask for 10/20 next page. One parameter on existing FTS5.

98. **Episodes are always multi-row per consolidation run.** 20 facts → 3-5 topic-grouped episodes, each with own source_ids. A 1-year mega-summary in one row would make raw msg retrieval impossible. Cross-topic connections handled by LLM at retrieval time.

99. **No lighter models available to test.** All local Ollama models are 5-18GB (8B+ params). Need to pull a smaller model (qwen3:1.7b or gemma3:4b) for floor testing. Parked — not urgent since Qwen3 8B works well enough.


## Sensitive Facts, Archival Chain, Docs (Decision 100-106)

100. **Sensitive facts — indirect hinting.** When Alice says "I wish Bob would exercise," the LLM knows this as Alice's private fact. Bob's prompt NEVER contains it (structural guarantee). The LLM can hint indirectly when talking to Alice: "Have you two thought about doing something active together?" — framed as a couple suggestion, not revealing. The tact is the LLM's job; the privacy is the engine's job.

101. **Monthly window is 30-90d.** The 30d minimum prevents monthly from touching recent weekly episodes that are still useful individually. The 90d upper bound hands off to quarterly. The window is 60 days wide. This is a config knob in `CASCADING_CONFIG` — adjustable.

102. **Archival chain confirmed:** raw facts → weekly episodes (archives raw facts) → monthly episodes (archives weekly) → quarterly episodes (archives monthly). Each level archives its inputs. Raw messages table NEVER touched — immutable rebuild source.

103. **Explicit schedule is the `days_since_last` check in user_meta.** Weekly runs if 7+ days since `last_consolidation`. Monthly runs if 30+ days since `last_cascade`. The maintenance loop runs hourly but the timestamp gate makes it weekly/monthly. Deterministic, logged, inspectable. Not a heuristic.

104. **Debug mode should show quality drops.** When building `/debug`, include: fact count by kind/status, dropped episodes (too short, low coverage), recent consolidation results, FTS5 search test.

105. **HOW_IT_WORKS.md created.** Complete system reference covering: two-table architecture, message flow, fact visibility, tags, relay flow, scoring, consolidation chain, proactive injection, commands, architecture. Located at `v2/HOW_IT_WORKS.md`.

106. **Lighter model test: gemma3:4b pulling.** 2.3GB, 4B params. Will benchmark when ready to find the quality floor.


## Simplification, Splitting, Telegram (Decision 107-116)

107. **Simplify tiers: remove weekly accumulation.** Each job processes ONE fixed window, no rolling. Weekly job (Mon) → previous week's resolved facts only. Monthly job (1st) → that month's weekly episodes only. Quarterly (Jan/Apr/Jul/Oct 1st) → that quarter's monthlies only. No overlap, no reprocessing, no leftover accumulation.

108. **Hard monthly for large batches.** If monthly finds >20 weekly episodes, split into 2+ consolidation calls (~10 each) instead of one big prompt. Keeps episode quality consistent and bounded LLM input size.

109. **"Lucky hunch" = define the frame.** Each consolidation job has a fixed, explicit time frame. No "grab whatever's unprocessed" rolling semantics. The schedule defines the window, the job processes exactly that window.

110. **LLM output pagination for search tool.** The FTS5 search tool (v2.1) will accept `limit` from the LLM (default 5, LLM can request 10/20). Bigger models can handle bigger pages. Not over-engineering — it's one parameter.

111. **Telegram message splitting added.** `_split_message()` splits at paragraph > newline > sentence > hard-cut boundaries. Buttons attach to last chunk only. Tested with 8 unit tests.

112. **Telegram offline behavior: messages are buffered.** Long-polling means Telegram queues user messages (up to ~24h). Bot receives them on reconnect. Bot replies that failed are lost — user sees no response for that message.

113. **Explicit schedule logging.** Maintenance loop now logs days-since-last and skip/run decisions at DEBUG/INFO level. Makes schedule behavior inspectable without guessing.

114. **"Keep to myself" button fully removed (prior turn).** Only "💬 Share with partner" remains. Dismiss = default (already kept). No confusing second button.

115. **`/debug` = full level, dropped episodes = `/warnings` level.** Two separate commands: `/debug` shows complete knowledge state, `/warnings` shows only quality issues (dropped episodes, coverage warnings, schedule skips).

116. **Privacy isolation for sensitive facts confirmed.** Every fact defaults to `private`. 8+ tests verify Alice's private facts NEVER appear in Bob's prompt across all prompt types. Sensitive info (e.g., "wish partner would exercise") stays private and the LLM uses indirect hints only.


## Retry, Benchmarks, Style, Server Recovery (Decision 117-126)

117. **Tier simplification confirmed — fixed calendar windows, not "since last run."** If server was down 2 months: run 2 separate monthly passes (month 1 data, then month 2 data), NOT one giant pass grabbing everything since last run. Each pass produces similarly-sized episodes. No accumulation, no "grab whatever's left."

118. **Sparse data: skip if below threshold.** If a weekly window has <3 resolved facts, skip consolidation for that window. Those facts carry into the next window. Prevents creating micro-episodes from thin data.

119. **Dense data: batch within a window.** If a weekly window has >20 resolved facts, split into 2+ LLM calls (~10 each). Each batch produces its own episodes. Consistent quality regardless of activity level.

120. **Message splitting is Telegram-specific only.** `_split_message()` lives in `telegram_adapter.py`, not a shared utility. REST and WebSocket adapters have no message size limit.

121. **gemma3:4b (4B params) = 0% extraction.** Can't produce valid JSON for our contract at all. Empty response. Quality floor established: **minimum 8B params** (Qwen3 8B = 73%, Gemini Flash Lite = 80%).

122. **User style/tone handled via `/style` command.** Mood presets (stressed/happy/angry/reflective) control `response_length` (short/normal/long) and dampening intensity. Voice presets control communication style. Already built — no change needed.

123. **Telegram retry on failed sends.** `_retry_send()` wraps all `bot.send_message` and `bot.send_document` calls. 3 attempts with exponential backoff (1s, 2s, 4s). Handles `RetryAfter` (rate limit) with server-specified wait. After 3 failures: log error, move on.

124. **Telegram offline: user msgs buffered ~24h, bot replies retried 3x then lost.** Long-polling means Telegram queues user messages while bot is offline. On reconnect, bot processes all buffered messages and generates responses. If a response send fails after 3 retries, it's logged and lost — the user can re-send their message.

125. **Dead-letter queue for permanently failed sends: parked.** A proper retry queue (persist failed responses to DB, retry on next startup) is a bigger feature. Current behavior is acceptable for MVP — transient failures are retried, permanent ones are logged.

126. **Server recovery with fixed-window consolidation.** After 2 months offline, the maintenance loop runs immediately. The weekly job detects weeks 1-8 are all unprocessed. It runs 8 separate weekly passes (one per week), then monthly runs 2 passes (one per month). Each pass is bounded and produces consistent-size episodes.
