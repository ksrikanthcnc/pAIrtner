# pAIrtner v2 — Design Overview

v1 proved the concept. v2 is a focused rewrite around the core insight: **the memory engine IS the product**. Everything else (relay, PWA, Telegram, commands) is a delivery channel.

## What v1 taught us

| Good | Bad |
|---|---|
| Memory ops from LLM work well | 27 tables, most unused in practice |
| Relay draft→approve flow is solid | Consolidation/analysis/digest are stubs |
| PWA + duo testing rig is useful | Too many commands (20), confusing |
| Pack system is flexible | LLM reads entire history — no chunking |
| Fake LLM + eval harness works | No separation between raw log and knowledge |

## v2 Principles

1. **Two tables, not twenty-seven.** Raw messages (append-only log) and knowledge (LLM-curated facts). Everything else derives from these.
2. **Dual-model architecture.** Fast small model (4B) extracts knowledge. Bigger model converses with empathy. Neither reads the full history.
3. **Contract-driven.** YAML/MD files define schemas, LLM response formats, and API surfaces. Code reads contracts, validates against them. Changing behavior = editing a contract, not hunting through code.
4. **Search, don't stuff.** The LLM never sees all facts. It gets the top-K by relevance score + any FTS matches for the current message. Like RAG, but for personal facts instead of documents.
5. **Extensible by design.** New features (relay, goals, timeline) are plugins that read/write to the two core tables through the Memory API. Not baked into the schema.

## Architecture

```
User message
    │
    ▼
┌──────────────────────┐
│   Transport Layer     │  Telegram / REST+PWA / WebSocket / CLI
│   (adapters/)         │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   Message Router      │  Dispatches to handlers, manages sessions
│   (router.py)         │
└──────────┬───────────┘
           │
           ├───────────────────────────┐
           ▼                           ▼
┌──────────────────────┐    ┌────────────────────────┐
│  Raw Log (append)     │    │  Conversation LLM       │
│  messages table       │    │  (big model / API)      │
│  WRITE-ONLY by user   │    │  Reads: top-K knowledge │
└──────────┬───────────┘    │  + recent messages       │
           │                 │  Returns: reply +        │
           │                 │  memory_ops JSON         │
           │                 └──────────┬──────────────┘
           │                            │
           ▼                            ▼
┌──────────────────────┐    ┌────────────────────────┐
│  Knowledge Worker     │    │  Memory API             │
│  (small model, async) │    │  Validates & applies    │
│  Reads: raw messages  │    │  memory_ops from LLM    │
│  Extracts: facts,     │    │  Scoring, dedup,        │
│  patterns, themes     │    │  lifecycle management   │
│  Updates: knowledge   │    │                         │
└──────────────────────┘    └────────────────────────┘
           │                            │
           ▼                            ▼
    ┌──────────────────────────────────────┐
    │         Knowledge Store              │
    │  knowledge table (scored, ranked)    │
    │  FTS5 index for search               │
    │  Version history for audit            │
    └──────────────────────────────────────┘
```

### The two flows

**Synchronous (per-message):** User sends message → logged to raw → Conversation LLM gets top-K knowledge + last N messages → responds + proposes memory_ops → Memory API validates and applies → response delivered.

**Asynchronous (background):** Knowledge Worker periodically scans recent raw messages → extracts facts/patterns the conversation LLM missed → applies to knowledge store. Also runs aging, consolidation, dedup.

## Files

| File | Purpose |
|---|---|
| [MEMORY.md](MEMORY.md) | Knowledge store design — the core |
| [CONTRACTS.md](CONTRACTS.md) | Contract system — YAML/MD interface definitions |
| [LLM.md](LLM.md) | Dual-model architecture — which model does what |
| [TRANSPORT.md](TRANSPORT.md) | Adapters, PWA, Telegram, REST API |
| [MIGRATION.md](MIGRATION.md) | How to get from v1 → v2 |
