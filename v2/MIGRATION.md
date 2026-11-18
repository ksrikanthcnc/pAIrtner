# v1 → v2 Migration

## What carries over

| Component | Action |
|---|---|
| `messages` table | Keep as-is (already append-only) |
| `facts` table | Rename to `knowledge`, add columns |
| `users` table | Keep |
| `user_profiles` table | Keep |
| `couple_config` table | Keep |
| `auth_tokens` table | Keep |
| `usage` table | Keep |
| Memory API (`memory_api.py`) | Evolve — already has scoring, provenance, lifecycle |
| Memory engine (`memory.py`) | Evolve — op parsing, validation, dedup |
| LLM clients (`llm.py`) | Keep + add knowledge worker mode |
| Adapters | Simplify, keep interface |
| PWA static files | Evolve, add memory panel |
| Pack system | Replace with contracts |
| Test infrastructure | Keep, adapt to new schema |

## What gets dropped

| Component | Why |
|---|---|
| 19 unused tables | checkins, interaction_scores, user_patterns, partner_perceptions, conflicts, timeline_events, user_mood_styles, pending_batch, pending_batch_items, pack_snapshots, llm_calls, rate_limit, user_goals, shared_goals, fact_summaries, memory_versions, memory_op_log, rest_outbox, user_meta |
| `packs/` directory | Replaced by `contracts/` |
| `core/analysis.py` | Replaced by knowledge worker |
| `core/consolidation.py` | Rewritten in knowledge worker |
| `core/dampening.py` | Simplified, inline |
| `features/checkin.py` | Stub, not useful yet |
| `features/digest.py` | Stub, not useful yet |
| `features/nudges.py` | Stub, not useful yet |
| `features/conflict_detector.py` | Replaced by knowledge worker contradiction detection |
| `features/feedback.py` | Fold into relay |
| 10 commands | See TRANSPORT.md |

## Migration steps

1. Create `v2/` branch
2. Create `contracts/` from existing pack files + schema
3. Rename `facts` → `knowledge`, add new columns
4. Drop unused tables (keep in migration, don't delete data)
5. Rewrite context builder to use scoring + FTS5 search
6. Add knowledge worker background loop
7. Slim commands from 20 → 10
8. Update PWA with memory panel
9. Update tests
10. Tag v2.0

## Can we run v1 and v2 side by side?

Yes — different DB files. The `--keep` flag in the dev rig already supports this:
```bash
# v1 (existing DB)
uv run python dev/localtest/run_local.py --port 8766 --keep

# v2 (new DB, different port)  
uv run python dev/localtest/run_local.py --port 8767 --db test_v2.db --keep
```

## Estimated scope

| Phase | Work | Effort |
|---|---|---|
| Contracts + schema | Write YAML contracts, new schema | 1 session |
| Memory API v2 | Evolve from v1 API, add FTS5 search | 1 session |
| Knowledge worker | Small model extraction loop | 1-2 sessions |
| Slim transport | Reduce commands, update adapters | 1 session |
| PWA memory panel | New UI for facts + health | 1 session |
| Testing + polish | Adapt tests, fix edges | 1 session |

Total: ~6 sessions for a working v2. The v1 test suite (1229 tests) gives a safety net.
