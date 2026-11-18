# Memory Engine v2 — Knowledge Store Design

The self-maintaining knowledge base is the core challenge and the core product.

## The Two Tables

### 1. `messages` — Raw Log (append-only)

The source of truth. Never modified, never deleted (soft-delete for UI only).

```yaml
messages:
  columns:
    id: INTEGER PRIMARY KEY AUTOINCREMENT
    user_id: INTEGER NOT NULL
    role: TEXT NOT NULL          # user | assistant | system | relay
    content: TEXT NOT NULL
    metadata: TEXT               # JSON: {adapter, model, latency_ms, ...}
    created_at: TIMESTAMP
  indexes:
    - [user_id, created_at]
  rules:
    - append-only (no UPDATE, no DELETE on data)
    - soft_delete via separate column for UI purposes
    - this is the ONLY table user input writes to
```

### 2. `knowledge` — LLM-Curated Facts

The working memory. Managed exclusively by the Memory API (LLM ops + background worker + user commands). Never written directly by user input.

```yaml
knowledge:
  columns:
    id: INTEGER PRIMARY KEY AUTOINCREMENT
    user_id: INTEGER NOT NULL
    text: TEXT NOT NULL              # the fact itself
    kind: TEXT NOT NULL DEFAULT 'working'
      # core     — identity-level, permanent ("introvert", "married 6 years")
      # working  — current situations, temporary ("stressed about interview")
      # episode  — consolidated summary of past period
      # partner  — what user says about partner (unverified impressions)
    state: TEXT NOT NULL DEFAULT 'active'
      # active    — current and relevant
      # improving — getting better (for conflicts/situations)
      # resolved  — done/past, fading
      # outdated  — no longer true, pending archive
      # archived  — hidden from context, kept for history
    intensity: REAL DEFAULT 0.5     # 0.0–1.0, how strongly the user feels about this
    category: TEXT                   # relationship, household, emotional, work, health, other
    durability: TEXT DEFAULT 'stable'
      # transient    — auto-archive after 14 days
      # situational  — auto-archive after 30 days (with confirm)
      # stable       — stays until explicitly changed
    # --- scoring ---
    citation_count: INTEGER DEFAULT 0
    last_cited_at: TIMESTAMP
    relevance_score: REAL DEFAULT 0.5  # precomputed, refreshed on access
    # --- provenance ---
    source_message_id: INTEGER REFERENCES messages(id)
    contradicts_id: INTEGER REFERENCES knowledge(id)
    supersedes_id: INTEGER REFERENCES knowledge(id)
    # --- lifecycle ---
    created_at: TIMESTAMP
    updated_at: TIMESTAMP
    archived_at: TIMESTAMP
  indexes:
    - [user_id, kind, state]
    - [user_id, relevance_score DESC]
    - [user_id, category]
  fts5:
    - knowledge_fts ON knowledge(text)
```

### Why two tables and not one?

**Separation of concerns.** The raw log is the truth — it never lies, never ages, never gets curated. The knowledge store is the LLM's *interpretation* of the truth — it's curated, scored, aged, consolidated. If the knowledge store gets corrupted or the extraction logic changes, you rebuild it from the raw log. You can't do that if facts and messages are interleaved.

This is the **write-ahead log + materialized view** pattern from databases, applied to personal memory.

## Retrieval: Search, Don't Stuff

The conversation LLM never sees all knowledge. It gets a curated window:

```
1. Core facts (always, up to budget)           ~300 words
2. Top-K working facts by relevance_score      ~400 words  
3. FTS5 matches for current message            ~200 words
4. Episode summaries (compressed history)       ~200 words
5. Recent messages (last 10-20)                 ~500 words
                                         Total: ~1600 words
```

### Relevance scoring (precomputed)

Score is precomputed on every write/cite and stored in `relevance_score`:

```
score = kind_weight × decay(age) × state_weight × intensity × citation_boost
```

| Factor | Values |
|---|---|
| kind_weight | core=1.0, episode=0.7, working=0.5, partner=0.4 |
| decay | `e^(-age_days / halflife)`, core=∞, episode=180d, working=21d |
| state_weight | active=1.0, improving=0.9, resolved=0.3, outdated=0.1 |
| intensity | the user's own weighting (0.0–1.0) |
| citation_boost | `min(1.0 + 0.1 × citation_count, 1.5)` |

### FTS5 search (per-message)

On every user message, run FTS5 against the knowledge store:
1. Extract key terms from the message (simple: split + stopword removal)
2. FTS5 `MATCH` query with those terms
3. Top 5 results by BM25 rank
4. Merge with the score-ranked window, dedup by ID

This means: if the user says "remember when we argued about dishes?", the FTS5 search finds the dishes-related facts even if they have low relevance scores (old, resolved). The LLM sees them in context and can reference them.

## Knowledge Operations

The LLM proposes ops, the Memory API validates and applies:

```yaml
memory_ops:
  - op: add
    text: "Stressed about job interview tomorrow"
    kind: working
    category: work
    durability: transient
    intensity: 0.8

  - op: revise
    id: 41
    text: "Son is 7, in 2nd grade"

  - op: state
    id: 35
    state: resolved
    reason: "deadline passed, went well"

  - op: promote
    id: 22
    # working → core (needs user confirm)

  - op: link
    id: 47
    contradicts: 23
    # flags contradiction for user resolution
```

### Validation rules (engine-enforced)

- Max 5 ops per message
- LLM can only reference IDs visible in its context
- LLM cannot set visibility (only user can share)
- `add kind:core` auto-downgrades to `promote` (needs user confirm)
- Near-duplicate check on add (fast: FTS5 + SequenceMatcher)
- Async dedup check after response (slow: LLM-based semantic merge)

## Background Knowledge Worker

Runs asynchronously, separate from the per-message flow:

### Extraction pass (every N messages)

The small model (4B) reads the last N raw messages and:
1. Extracts facts the conversation LLM missed (it only sees 10-20 recent messages)
2. Detects patterns across messages (communication style, recurring themes)
3. Flags contradictions with existing knowledge
4. Proposes status changes (resolved situations, outdated facts)

### Consolidation pass (weekly)

Groups related resolved/outdated working facts → summarizes into episode facts:
- Cluster by category + FTS5 similarity
- Small model summarizes each cluster into one episode
- Originals archived, episode kept
- "Nov 2026: kitchen territory friction during in-law visit. Resolved with designated shelves."

### Aging pass (hourly)

- Transient facts > 14 days → archive
- Working facts uncited for 30 days → state=outdated
- Outdated facts > 14 days → archive
- Core facts → never age
- Update `relevance_score` for all active facts

## Rebuild from Raw

If the knowledge store needs rebuilding (new extraction logic, corruption, migration):

```python
async def rebuild_knowledge(db_path: str, user_id: int, llm: LLMClient):
    """Delete all knowledge for user, re-extract from raw messages."""
    await clear_knowledge(db_path, user_id)
    messages = await get_all_messages(db_path, user_id)
    
    # Process in chronological chunks
    for chunk in chunk_messages(messages, size=50):
        ops = await extract_knowledge(llm, chunk, existing_knowledge=[])
        await apply_ops(db_path, user_id, ops)
    
    # Run consolidation
    await run_consolidation(db_path, llm, user_id)
```

This is the key advantage of the two-table design: the raw log is the backup, the knowledge store is rebuildable.

## v1 → v2 Migration

- `messages` table stays as-is (already append-only in v1)
- `facts` table → `knowledge` table (rename + add `intensity`, `relevance_score` columns)
- Drop all v1 tables not in use: `checkins`, `interaction_scores`, `user_patterns`, `partner_perceptions`, `conflicts`, `timeline_events`, `user_mood_styles`
- Keep: `users`, `messages`, `knowledge` (née facts), `couple_config`, `user_profiles`, `auth_tokens`, `usage`
- Total: ~8 tables down from 27
