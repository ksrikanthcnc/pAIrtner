# Contract System — Interface Definitions

Contracts are YAML/MD files that define **what** the system does. Code implements **how**.

## Why contracts?

1. **Single source of truth.** Change a schema in the contract, code validates against it. No hunting through 12 storage modules.
2. **LLM-readable.** The contract files ARE the LLM prompt instructions. `contracts/memory_ops.yaml` goes straight into the system prompt.
3. **Extensible.** Adding a new knowledge kind or op type = adding lines to YAML. The engine reads it at runtime.
4. **Testable.** Generate test cases from contracts. If the contract says `intensity: 0.0–1.0`, a test asserts that out-of-range values are rejected.

## Contract files

```
contracts/
├── schema.yaml          # DB schema (tables, columns, types, constraints)
├── memory_ops.yaml      # LLM memory operations (add, revise, state, promote, link)
├── response.yaml        # LLM response formats per task (chat, rewrite, mediate, ...)
├── scoring.yaml         # Relevance scoring parameters (weights, halflives, boosts)
├── lifecycle.yaml       # Aging rules, consolidation triggers, auto-promote thresholds
├── commands.yaml        # Slash commands and their handlers
└── api.yaml             # REST API endpoints, auth rules, rate limits
```

### Example: `memory_ops.yaml`

```yaml
# Memory operations the LLM can propose.
# Engine validates against this schema at runtime.

max_ops_per_message: 5

ops:
  add:
    description: "Add a new fact to knowledge store"
    fields:
      text: { type: string, required: true, max_length: 500 }
      kind: { type: enum, values: [working, core], default: working }
      category: { type: enum, values: [relationship, household, emotional, work, health, other], required: false }
      durability: { type: enum, values: [transient, situational, stable], default: stable }
      intensity: { type: float, min: 0.0, max: 1.0, default: 0.5 }
    rules:
      - "kind: core auto-downgrades to promote (needs user confirm)"
      - "near-duplicate check against existing knowledge"
      - "visibility always forced to private"

  revise:
    description: "Update an existing fact's text"
    fields:
      id: { type: integer, required: true, note: "must be in LLM's visible context" }
      text: { type: string, required: true, max_length: 500 }
    rules:
      - "old version kept in history"
      - "rejects if text is identical after normalization"

  state:
    description: "Change a fact's state"
    fields:
      id: { type: integer, required: true }
      state: { type: enum, values: [active, improving, resolved, outdated] }
      reason: { type: string, required: false }
    rules:
      - "core facts cannot be set to resolved or outdated"

  promote:
    description: "Propose a working fact for core promotion"
    fields:
      id: { type: integer, required: true }
    rules:
      - "queues for user confirmation"
      - "if user confirms, kind changes to core, durability to stable"

  link:
    description: "Flag a relationship between facts"
    fields:
      id: { type: integer, required: true }
      contradicts: { type: integer, required: false }
      supersedes: { type: integer, required: false }
    rules:
      - "asks user to resolve contradiction"
```

### Example: `scoring.yaml`

```yaml
# Relevance scoring parameters.
# Memory API reads these at startup.

formula: "kind_weight × decay(age) × state_weight × intensity × citation_boost"

kind_weight:
  core: 1.0
  episode: 0.7
  working: 0.5
  partner: 0.4

halflife_days:
  core: null        # infinite, no decay
  episode: 180
  working: 21
  partner: 30

state_weight:
  active: 1.0
  improving: 0.9
  resolved: 0.3
  outdated: 0.1

citation_boost:
  formula: "min(1.0 + 0.1 × count, 1.5)"
  max: 1.5

context_budget:
  core: 300          # words
  working: 400
  fts_matches: 200
  episodes: 200
  recent_messages: 500
  total: 1600
```

### Example: `response.yaml`

```yaml
# LLM response schemas per task type.
# Injected into the system prompt as the output contract.

tasks:
  chat:
    description: "Regular conversation"
    schema:
      reply: { type: string, required: true }
      memory_ops: { type: array, items: "$ref:memory_ops", required: true, default: [] }
      detected_mood: { type: enum, values: [stressed, happy, angry, reflective, neutral], required: false }
      relay_draft: { type: string, required: false }

  rewrite:
    description: "Rewrite for partner delivery"
    schema:
      reply: { type: string, required: true }
      relay_draft: { type: string, required: true }
      memory_ops: { type: array, items: "$ref:memory_ops", default: [] }

  extract:
    description: "Background knowledge extraction (small model)"
    schema:
      facts: { type: array, items: { text: string, kind: string, category: string, intensity: float } }
      patterns: { type: array, items: string }
      contradictions: { type: array, items: { fact_id: integer, conflicts_with: string } }
      state_changes: { type: array, items: { fact_id: integer, new_state: string, reason: string } }
```

## How code uses contracts

```python
# At startup
contracts = load_contracts("contracts/")

# Validation
def validate_memory_ops(raw_ops: list, contract: dict) -> list[MemoryOp]:
    """Validate LLM-proposed ops against the memory_ops contract."""
    max_ops = contract["max_ops_per_message"]
    valid_ops = contract["ops"]
    # ... validate each op's fields against the schema
    
# Prompt injection
def build_system_prompt(task: str, contracts: dict) -> str:
    """Build the system prompt from contracts."""
    response_schema = contracts["response"]["tasks"][task]["schema"]
    memory_ops_schema = contracts["memory_ops"]["ops"]
    # ... format into the prompt
```

## Is this too abstract?

No, IF:
- Contracts stay **declarative** (what, not how)
- Each contract file fits on one screen (~50-100 lines)
- Code reads them with a simple YAML loader (no metaprogramming, no codegen)
- The LLM prompt IS the contract (not a translation of it)

Yes, IF:
- You try to make contracts Turing-complete (conditions, loops, inheritance)
- Contracts grow to hundreds of lines with nested schemas
- You need a "contract compiler" to generate code from them
- The gap between contract and code requires a mapping layer

Keep it simple: YAML schemas that the engine reads and validates against. Not a DSL.
