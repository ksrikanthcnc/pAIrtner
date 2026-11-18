# Memory Engine v1 — Design

The memory engine is the core of pAIrtner. Everything else (relay, mediation, commands) is a delivery mechanism for what the memory engine learns and retrieves.

## Problem

Memory v0 works but is naive:
- No time decay — a 3-month-old working fact weighs the same as yesterday's
- Dedup is string-similarity only — "likes pizza" and "loves pizza" both stored
- Contradictions go undetected — "hates mornings" can coexist with "is a morning person"
- Facts can't be traced back to the message that created them
- Consolidation is a stub — never runs
- Aging is defined but never scheduled
- Context builder does N separate queries, no ranking beyond kind-order
- No unified API — LLM uses memory_ops, PWA uses ad-hoc REST, commands use db directly

## Design Principles

1. **One interface, many consumers.** The Memory API is the single gate for ALL memory operations — LLM, PWA, commands, background jobs. No bypassing it.
2. **Score everything.** Every fact gets a `relevance_score` computed from recency, citation count, kind, and status. The context builder uses this score, not ad-hoc sorting.
3. **Facts are immutable history.** Never truly delete — archive, supersede, or consolidate. The version chain is the audit trail.
4. **LLM decides content, engine decides structure.** The LLM proposes what to remember. The engine decides dedup, scoring, lifecycle, and what makes it into the prompt.
5. **Fail open.** If scoring, dedup, or consolidation fails, fall back to v0 behavior (kind-order, word-budget caps). Never lose a fact because the engine errored.

---

## 1. Relevance Scoring

Every active fact has a computed `relevance_score` (0.0–1.0). The score is **not stored** — it's computed at retrieval time so it's always fresh.

### Formula

```
score = kind_weight × decay(age) × status_weight × citation_boost
```

| Component | Calculation |
|---|---|
| `kind_weight` | core=1.0, episode=0.7, working=0.5, partner_view=0.4 |
| `decay(age)` | `e^(-age_days / halflife)` where halflife varies by kind |
| `status_weight` | active=1.0, improving=0.9, resolved=0.3, outdated=0.1 |
| `citation_boost` | `min(1.0 + 0.1 × citation_count, 1.5)` |

### Halflife by kind

| Kind | Halflife (days) | Rationale |
|---|---|---|
| core | ∞ (no decay) | Identity facts don't fade |
| episode | 180 | Compressed history, slow fade |
| working | 21 | Current situations, fast fade |
| partner_view | 30 | Impressions fade unless refreshed |

### Example scores

| Fact | Kind | Age | Status | Citations | Score |
|---|---|---|---|---|---|
| "Married 6 years" | core | 90d | active | 5 | 1.0 × 1.0 × 1.0 × 1.5 = **1.0** (capped) |
| "Stressed about job interview" | working | 2d | active | 0 | 0.5 × 0.94 × 1.0 × 1.0 = **0.47** |
| "Dishes argument last week" | working | 10d | improving | 2 | 0.5 × 0.72 × 0.9 × 1.2 = **0.39** |
| "Old job schedule" | working | 60d | outdated | 0 | 0.5 × 0.06 × 0.1 × 1.0 = **0.003** |
| "Nov 2026: in-law visit friction" | episode | 45d | active | 1 | 0.7 × 0.78 × 1.0 × 1.1 = **0.60** |

### Schema changes

```sql
ALTER TABLE facts ADD COLUMN citation_count INTEGER DEFAULT 0;
ALTER TABLE facts ADD COLUMN last_cited_at TIMESTAMP;
ALTER TABLE facts ADD COLUMN source_message_id INTEGER REFERENCES messages(id);
```

No `relevance_score` column — computed at read time.

### Citation tracking

When the context builder includes a fact in the LLM prompt, increment its `citation_count` and set `last_cited_at`. This creates a feedback loop: facts that the LLM finds useful get cited more → score goes up → included more often.

---

## 2. Source Tracking

Every fact links to the message that created it via `source_message_id`.

### How it works

1. When `apply_memory_ops()` creates a fact, it sets `source_message_id` to the current message's ID (already available as `message_id` param).
2. The Memory API exposes `get_fact_provenance(fact_id)` → returns the source message text + timestamp.
3. The PWA shows a "📍 From: [message preview]" link when viewing a fact.
4. The `/review` command shows source context: "Learned from your message on Nov 15: '...'"

### User-facing: "Why do you think that?"

When the user asks "why do you think X?", the LLM can reference fact IDs in context. The engine returns the source message. No new LLM call needed — it's a DB lookup.

---

## 3. Semantic Dedup & Contradiction Detection

### Dedup (on every add)

Current: `SequenceMatcher` with 0.85 threshold on normalized text. Works for near-exact duplicates, misses semantic ones ("likes pizza" vs "loves pizza").

**v1 approach — two-tier:**

1. **Fast path (no LLM):** FTS5 match + normalized SequenceMatcher (current). If ratio ≥ 0.85, auto-merge (same as now).
2. **Slow path (LLM, async):** After saving a new fact, queue a background dedup check. The dedup job:
   - Pulls the 5 most similar existing facts (FTS5 ranked by relevance)
   - Asks the LLM: "Are any of these the same thing? Should any be merged or does one supersede another?"
   - LLM returns: `[{"action": "merge", "keep_id": 12, "drop_id": 47}, ...]` or `[]`
   - Engine applies the merges (archive dropped, update kept)

The slow path runs **after** the response is sent — no latency impact.

### Contradiction detection

New addition. After saving a fact, the dedup job also checks for contradictions:
- FTS5 pulls similar facts
- LLM is asked: "Do any of these contradict each other?"
- If yes: flag the newer fact with `status: contradicts`, store the conflicting fact ID in a new `contradicts_id` field
- Next message to the user: "I noticed something — you said [X] but earlier you said [Y]. Which is current?"

```sql
ALTER TABLE facts ADD COLUMN contradicts_id INTEGER REFERENCES facts(id);
```

### Dedup job queue

Simple in-process async queue (no Redis/Celery needed at this scale):

```python
# In memory_api.py
_dedup_queue: asyncio.Queue[tuple[str, int, int]] = asyncio.Queue()
# Items: (db_path, user_id, fact_id)

async def dedup_worker(llm: LLMClient):
    """Background worker that processes dedup checks."""
    while True:
        db_path, user_id, fact_id = await _dedup_queue.get()
        await _run_dedup_check(db_path, llm, user_id, fact_id)
        _dedup_queue.task_done()
```

---

## 4. Lifecycle Pipeline

Facts follow a lifecycle. The engine enforces it; the LLM and user influence it.

```
                    ┌──────────────────┐
          new add → │ working (active)  │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
         cited 3x+      status change    no citation 30d
              │              │              │
              ▼              ▼              ▼
    ┌─────────────┐  ┌────────────┐  ┌──────────────┐
    │ propose_core │  │ improving/ │  │  outdated     │
    │ (user asked) │  │ resolved   │  │  (auto)       │
    └──────┬──────┘  └─────┬──────┘  └──────┬───────┘
           │               │                │
           ▼               ▼                ▼
    ┌────────────┐   consolidation    ┌───────────┐
    │   core     │   eligible         │ archived  │
    │ (permanent)│                    │ (hidden)  │
    └────────────┘                    └───────────┘
```

### Auto-promote to core

When `citation_count >= 3` AND the fact is `working` with status `active`:
- Don't auto-promote silently. Instead, propose to user: "I keep referencing '[fact]' — should I mark this as a core part of who you are?"
- If confirmed → promote to core (no expiry, no aging)
- Track via `propose_core` op

### Auto-archive

When a `working` fact has:
- `status = active` AND no citation in 30 days → set `status = outdated`
- `status = outdated` AND age > 14 days → archive (set `archived_at`)
- `fact_type = transient` AND age > 14 days → archive (already exists in v0)
- `fact_type = situational` AND age > 30 days → archive after user confirm (already in v0)

---

## 5. Smart Context Window

Replace the current kind-priority + word-budget approach with **score-based retrieval**.

### Algorithm

```python
async def build_memory_context(db_path: str, user_id: int, budget_tokens: int) -> list[str]:
    """Build the memory section of the LLM prompt."""
    
    # 1. Always include ALL core facts (they define the person)
    core = await get_facts_by_kind(db_path, user_id, "core")
    
    # 2. Score all non-core active facts
    working = await get_active_non_core_facts(db_path, user_id)
    scored = [(f, compute_relevance_score(f)) for f in working]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # 3. Fill budget: core first, then top-scored working, then episodes
    lines = []
    token_count = 0
    
    for f in core:
        line = format_fact(f, kind_label=True)
        lines.append(line)
        token_count += estimate_tokens(line)
    
    for f, score in scored:
        if token_count >= budget_tokens:
            break
        line = format_fact(f)
        lines.append(line)
        token_count += estimate_tokens(line)
        # Record citation
        await increment_citation(db_path, f["id"])
    
    return lines
```

### Token estimation

Word-based approximation: `tokens ≈ words × 1.3`. Good enough for SQLite-scale. No need for tiktoken dependency.

### Budget source

From `pack.yaml`:
```yaml
budgets:
  core: 300      # words
  working: 400
  episodes: 200
  shared: 150
  total: 1200    # NEW: hard cap on total memory context
```

---

## 6. Consolidation v2

The current `consolidation.py` has the right structure. What's missing:

1. **Scheduling**: wire to the background loop
2. **Better grouping**: cluster by semantic similarity, not just time period
3. **Episode quality**: better prompt for summarization

### Consolidation trigger

Run weekly (configurable). The background loop checks:
```python
async def maybe_consolidate(db_path: str, llm: LLMClient, user_id: int):
    last_run = await get_user_meta(db_path, user_id, "last_consolidation")
    if days_since(last_run) < 7:
        return
    await run_consolidation(db_path, llm, user_id)
    await set_user_meta(db_path, user_id, "last_consolidation", _now_iso())
```

### Grouping improvement

Current: group by month. Better: group by **category + theme**.

Steps:
1. Pull all eligible facts (resolved/outdated, age > 30d)
2. Group by `category` first (relationship, household, emotional, work, health)
3. Within each category, use FTS5 to find clusters of related facts
4. Each cluster of 3+ facts → one episode

### Episode format

```
[Episode: Nov 2026, Household] In-law visit created kitchen territory friction.
Resolved when both partners agreed on designated shelves. Key learning: direct
communication about space needs works better than hinting.
```

---

## 7. Memory API

Single interface for all consumers. Returns JSON-serializable dicts.

### Operations

```python
class MemoryAPI:
    """Unified memory interface. All memory access goes through here."""
    
    # --- Read ---
    async def list_facts(self, user_id, *, kind=None, status=None, 
                         limit=50, offset=0, scored=False) -> FactList
    async def get_fact(self, fact_id) -> Fact | None
    async def search(self, user_id, query: str, limit=10) -> list[Fact]
    async def get_provenance(self, fact_id) -> Provenance | None
    async def get_versions(self, fact_id) -> list[Version]
    async def get_stats(self, user_id) -> MemoryStats
    
    # --- Write ---
    async def add_fact(self, user_id, text, *, kind="working", 
                       category=None, fact_type="stable",
                       source_message_id=None) -> Fact
    async def update_text(self, fact_id, new_text, *, reason=None) -> Fact
    async def set_status(self, fact_id, status, *, reason=None) -> Fact
    async def promote_to_core(self, fact_id) -> Fact
    async def archive(self, fact_id, *, reason=None) -> Fact
    async def delete(self, fact_id, user_id) -> bool  # hard delete, user-initiated only
    
    # --- Bulk ---
    async def apply_llm_ops(self, user_id, raw_ops, user_message, 
                            *, message_id=None) -> OpsResult
    
    # --- Scoring ---
    async def get_scored_context(self, user_id, budget_tokens=1200) -> ContextResult
    async def get_recency_report(self, user_id) -> RecencyReport
    
    # --- Maintenance ---
    async def run_aging(self) -> AgingResult
    async def run_dedup(self, user_id, fact_id) -> DedupResult
    async def run_consolidation(self, user_id) -> list[Episode]
```

### Response types

```python
@dataclass
class Fact:
    id: int
    text: str
    kind: str           # core, working, episode, partner_view
    status: str         # active, improving, resolved, outdated
    category: str | None
    fact_type: str      # transient, situational, stable
    visibility: str     # private, shared
    relevance_score: float  # computed
    citation_count: int
    last_cited_at: str | None
    source_message_id: int | None
    created_at: str
    updated_at: str

@dataclass
class FactList:
    facts: list[Fact]
    total: int
    offset: int

@dataclass
class MemoryStats:
    total: int
    by_kind: dict[str, int]      # {"core": 12, "working": 35, ...}
    by_status: dict[str, int]
    avg_score: float
    oldest_uncited_days: int     # how stale is the stalest active fact

@dataclass
class RecencyReport:
    """For the PWA 'memory health' panel."""
    healthy: int          # cited in last 7 days
    aging: int            # 7-30 days since last citation
    stale: int            # >30 days, candidates for archival
    contradictions: int   # facts with contradicts_id set
    
@dataclass
class Provenance:
    fact: Fact
    source_message: str | None   # the message text that created this fact
    source_timestamp: str | None
    version_count: int
```

### REST endpoints (for PWA)

```
GET  /api/memory?kind=&status=&scored=true    → FactList
GET  /api/memory/:id                          → Fact
GET  /api/memory/:id/provenance               → Provenance
GET  /api/memory/:id/versions                 → list[Version]
PUT  /api/memory/:id                          → Fact  (update text)
PUT  /api/memory/:id/status                   → Fact  (change status)
PUT  /api/memory/:id/promote                  → Fact  (promote to core)
DELETE /api/memory/:id                        → OK
GET  /api/memory/stats                        → MemoryStats
GET  /api/memory/recency                      → RecencyReport
GET  /api/memory/search?q=pizza               → list[Fact]
POST /api/memory/consolidate                  → list[Episode] (admin)
```

### LLM integration

`apply_llm_ops()` replaces the current `parse_memory_ops()` + `apply_memory_ops()` pair. It:
1. Validates ops (same as now)
2. Runs fast dedup (same as now)  
3. Applies ops via the Memory API (not direct DB calls)
4. Queues slow dedup for new facts
5. Returns OpsResult (same as now)

The LLM contract (contract.md) stays the same — the LLM doesn't know about the API. It proposes `memory_ops`, the engine routes them through the API.

---

## 8. Background Scheduler

Wire aging, dedup, and consolidation to the existing background loop.

### Current background loop

The `adapters/background.py` already runs periodic tasks (delivery loop, cooldown checks). Add memory maintenance to it:

```python
async def memory_maintenance_loop(db_path: str, llm: LLMClient, interval: int = 3600):
    """Run once per hour. Lightweight — skips if nothing to do."""
    while True:
        await asyncio.sleep(interval)
        for user_id in await get_all_active_user_ids(db_path):
            # 1. Age expired facts
            await memory_api.run_aging()
            
            # 2. Process dedup queue (if items pending)
            # (handled by dedup_worker, always running)
            
            # 3. Consolidation (weekly, per-user)
            await maybe_consolidate(db_path, llm, user_id)
            
            # 4. Auto-outdated detection
            await auto_mark_outdated(db_path, user_id)
```

### `auto_mark_outdated`

New function: finds `working` + `active` facts with no citation in 30 days. Sets them to `outdated`. Runs hourly but only acts on long-uncited facts, so it's idempotent and cheap.

---

## 9. Implementation Plan

Order matters — each step builds on the previous.

### Phase 1: Schema + API scaffold (no behavior change)
1. Add new columns (`citation_count`, `last_cited_at`, `source_message_id`, `contradicts_id`)
2. Create `MemoryAPI` class wrapping existing storage functions
3. Wire all existing callers through the API (no behavior change yet)
4. **Tests**: existing suite passes, new API tests added

### Phase 2: Scoring + smart context
5. Implement `compute_relevance_score()`
6. Replace context builder with score-based retrieval
7. Add citation tracking (increment on prompt inclusion)
8. **Tests**: context builder returns facts in score order, citations increment

### Phase 3: Source tracking + provenance
9. Set `source_message_id` on all new facts
10. Implement `get_provenance()`
11. REST endpoints for PWA
12. **Tests**: provenance lookup works, REST returns correct data

### Phase 4: Dedup + contradiction
13. Background dedup worker with LLM
14. Contradiction detection and flagging
15. User notification on contradiction
16. **Tests**: dedup merges similar facts, contradictions flagged

### Phase 5: Lifecycle + scheduler
17. Auto-promote proposal (cited 3x)
18. Auto-outdated (uncited 30d)
19. Background scheduler wired to existing loop
20. **Tests**: lifecycle transitions work, scheduler runs

### Phase 6: Consolidation v2
21. Category-based grouping
22. Improved summarization prompt
23. Episode creation from clusters
24. **Tests**: consolidation produces valid episodes

### Phase 7: PWA memory panel
25. Memory list/search/edit UI
26. Recency health indicator
27. Provenance view ("where did this come from?")
28. **Tests**: E2E PWA tests

---

## Non-goals (for v1)

- **Embedding-based search** — FTS5 is sufficient for <10K facts per user. Embeddings add a dependency (vector DB or numpy) for minimal gain at this scale.
- **Cross-couple memory** — each user's memory is isolated. No sharing beyond explicit `visibility: shared`.
- **Real token counting** — word-based estimation (×1.3) is good enough. Adding tiktoken is a dependency for ~5% accuracy gain.
- **External memory store** — SQLite handles this scale. Postgres migration is a P4 item.
