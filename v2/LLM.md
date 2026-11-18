# Dual-Model Architecture

v2 uses two models with different jobs. Neither reads the full history.

## The two roles

### Conversation Model (big, empathetic)

**Job:** Talk to the user. Respond warmly, extract quick memory ops, draft relays.

| Attribute | Value |
|---|---|
| Size | 8B+ local or API (Gemini, Claude) |
| Runs | Synchronously, per-message |
| Input | Top-K knowledge + last N messages + current message |
| Output | Reply + memory_ops JSON |
| Latency | 2-10s (user-facing, must be fast) |
| Context | ~2K words of memory + ~500 words of history + system prompt |

**Recommended models:**
- **Gemini Flash** (API, free tier) — good empathy, fast, JSON-capable
- **Qwen3:8B** (local, 5.2GB) — strong general purpose, good JSON
- **Llama 3.1 8B** (local) — good instruction following

### Knowledge Worker (small, precise)

**Job:** Extract structured facts from raw messages. Detect patterns. Flag contradictions. Not user-facing.

| Attribute | Value |
|---|---|
| Size | 3-4B local |
| Runs | Asynchronously, every N messages |
| Input | Batch of raw messages + existing knowledge |
| Output | Structured JSON (facts, patterns, contradictions, state changes) |
| Latency | Doesn't matter (background) |
| Context | Larger batches (50-100 messages at a time) |

**Recommended models:**
- **Qwen3.5 4B** (local, 2.5GB) — best small model for structured output, 256K context
- **Phi-4-mini 3.8B** (local, 3GB) — strong reasoning, good at JSON
- **Gemma 3 4B** (local, 3.4GB) — multilingual, good understanding

### Why not one model for both?

1. **Latency.** The conversation model must respond in 2-10s. Running extraction in-band adds latency.
2. **Cost.** Extraction processes 50-100 messages at once — expensive on an API. Small local model = free.
3. **Quality.** The conversation model is optimized for empathy/warmth. The extraction model is optimized for precision/structure. Different strengths.
4. **Independence.** If the API is down, the conversation falls back to local. Knowledge extraction keeps running regardless.

## Research context

### How LLMs handle large text (RAG-like patterns)

The academic consensus (2025-2026) on LLM memory for long conversations:

1. **Flat RAG fails.** Treating memory as a vector store with top-K retrieval loses temporal context and can't distinguish stale from current facts. (Content rephrased from [Continuum Memory Architectures](https://arxiv.org/html/2601.09913) for compliance with licensing restrictions.)

2. **Chunked retrieval works.** For documents, 64-128 token chunks are optimal for fact-based retrieval; 512-1024 for broader context. For personal facts, our single-sentence knowledge entries (~20-50 tokens) are already ideal chunks. (Content rephrased from [Rethinking Chunk Size](https://ar5iv.labs.arxiv.org/html/2505.21700) for compliance with licensing restrictions.)

3. **Dual-system is the emerging pattern.** MemoRAG uses a light long-range model for global memory + retrieval tools for detail. Our architecture matches this: knowledge worker = global memory, FTS5 search = retrieval tool. (Content rephrased from [MemoRAG](https://ar5iv.labs.arxiv.org/html/2409.05591v3) for compliance with licensing restrictions.)

4. **Memory ≠ RAG.** AI agent memory systems distinguish four types: working (current context), episodic (past events), semantic (general facts), procedural (how-to). Our `kind` taxonomy maps to this: working=working, episode=episodic, core=semantic. (Content rephrased from [How Agents Remember](https://aishwaryasrinivasan.substack.com/p/how-memory-in-ai-agents-work) for compliance with licensing restrictions.)

### Small models and empathy

Research on empathy in small language models:

- The [TIDE study](https://arxiv.org/html/2505.15065v2) found that fine-tuned 3-5B parameter models can approach frontier model empathy levels on trauma-informed dialogue. Zero-shot small models hit an "empathy ceiling."
- For pAIrtner, the **conversation model** needs empathy (use 8B+ or API). The **knowledge worker** needs precision (4B is fine — it's not empathizing, it's extracting).
- If we fine-tune the knowledge worker on pAIrtner's own extraction patterns (distillation from the big model's memory_ops), quality improves significantly. This is a future optimization.

## Fallback chain

```
Conversation: Gemini API → Qwen3:8B local → Phi-4-mini local
Knowledge:    Qwen3.5 4B local → Qwen3:8B local → conversation model
```

If the dedicated knowledge worker isn't available, the conversation model handles extraction inline (v1 behavior). v2's dual-model is an optimization, not a hard requirement.

## Message flow

```
User: "I'm stressed about the interview tomorrow"
                    │
                    ▼
            ┌───────────────┐
            │ Log to messages│ (raw, append-only)
            └───────┬───────┘
                    │
                    ▼
            ┌───────────────────────────────────────┐
            │ Build context for conversation model   │
            │                                        │
            │ System prompt (from contracts/response) │
            │ + Core facts: "married 6y, one son"     │
            │ + Top-5 working facts by score          │
            │ + FTS5 matches for "stressed interview"  │
            │ + Last 10 messages                       │
            │ + Current message                        │
            └───────────────┬───────────────────────┘
                            │
                            ▼
                    Conversation LLM
                            │
                            ▼
            ┌───────────────────────────────────────┐
            │ Response:                              │
            │ {                                      │
            │   "reply": "That sounds nerve-wracking │
            │     — what role is it for?",           │
            │   "memory_ops": [                      │
            │     {"op":"add", "text":"stressed about│
            │      job interview", "kind":"working", │
            │      "durability":"transient",          │
            │      "intensity": 0.8}                  │
            │   ],                                   │
            │   "detected_mood": "stressed"           │
            │ }                                      │
            └───────────────┬───────────────────────┘
                            │
                    ┌───────┴───────┐
                    │               │
                    ▼               ▼
            Send reply      Apply memory_ops
            to user         via Memory API
```

**Later (background):**
```
Knowledge Worker wakes up (every 10 messages)
        │
        ▼
Reads last 50 raw messages + existing knowledge
        │
        ▼
Small model extracts:
- facts the conversation LLM missed
- patterns ("tends to catastrophize before events")
- contradiction: fact #12 says "loves mornings" but
  recent messages suggest otherwise
        │
        ▼
Applies to knowledge store via Memory API
```
