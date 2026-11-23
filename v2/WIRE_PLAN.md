# Schema v2 Wire Plan — Atomic Rename

## Goal

One pass: rename all v1 table names to v2 names across ALL code and tests atomically. No half-states.

## Renames

| v1 name | v2 name | Files affected |
|---|---|---|
| `pending` | `relay_queue` | pending.py, stats.py, admin_actions.py, redaction.py, 5 test files |
| `pending_batch` | `relay_batch` | pending.py, 2 test files |
| `pending_batch_items` | `relay_batch_items` | pending.py, 1 test file |
| `user_goals` | `goals` (scope='personal') | goals.py, stats.py, checkin.py, 3 test files |
| `shared_goals` | `goals` (scope='shared') | goals.py, stats.py, checkin.py, 2 test files |
| `couple_config` | `relationship_config` | couples.py, admin_actions.py, 2 test files |
| `make_couple_id()` format `1:2` | `make_relationship_id()` format `1_2` | ~40 callers |

## ID format unification

`make_couple_id(a, b)` returns `"min:max"` (colon).
`make_relationship_id(a, b)` returns `"min_max"` (underscore).

Decision: **keep colon format**, rename function to `make_couple_id()` → still used everywhere.
OR: **switch to underscore**, update all 40+ callers.

Recommendation: **keep colon format for now**. The format is irrelevant to correctness — it's just a string key. Renaming the function AND the format doubles the blast radius for no functional benefit.

## Execution plan

1. **Write the target schema.py** — one DDL with v2 table names only
2. **grep-replace each table name** across ALL .py files (not just SQL — also dict keys, comments)
3. **Update ALL test fixtures** — shared conftest `make_test_db()` using the new schema
4. **Run full suite** — must be ≥1550 passing
5. **Single commit** — atomic, one diff, easy to revert

## Preconditions

- Dedicated session, nothing else in flight
- No feature work interleaved
- Full test run before AND after

## Estimated effort

~3-4 hours focused work. The table rename is mechanical grep-replace. The goals merge (user_goals + shared_goals → unified goals table with scope column) is the only structural change.

## Risk

Low — it's all internal table names. No API surface changes. No user-facing behavior changes.
