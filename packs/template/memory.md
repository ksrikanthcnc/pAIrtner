# Memory Policy

<!-- Customize how your pack handles memories. The engine validates everything. -->

## Memory Kinds

### Core
Who the person is. Durable, identity-level facts.
- Personality traits, relationship basics, deep preferences, values
- Always in the prompt. Core memories never age.
- Use `propose_core` when the user reveals something fundamental.

### Working
Current situations. Changes often.
- Ongoing issues, recent events, temporary states
- Ranked by status: active > improving > resolved > outdated
- Resolved working memories eventually consolidate into episodes.

### Partner View
What this user says about their partner.
- Never shown to the partner. Used for mediation and context.
- Built from patterns across messages, not single statements.

### Shared
Facts both partners can see.
- Agreements, shared decisions, mutual goals
- Created when a user explicitly shares a private fact.

### Episodes
Compressed summaries of old memories.
- Created automatically by the consolidation engine.
- Only retrieved when relevant (FTS match to current message).

## Memory Operations

The model can propose these operations (the engine validates):

| Op | When to use |
|---|---|
| `add` | New fact learned from the message |
| `revise` | Existing fact needs updating (reference by ID) |
| `status` | Change status: active → improving → resolved → outdated |
| `propose_core` | Suggest promoting to core (user confirms) |

## Rules
- Maximum {max_ops_per_message} operations per message
- Never add duplicates — if similar fact exists, revise it instead
- Never extract facts from the partner's messages
- Don't over-extract — only save what's genuinely useful for future context
- When in doubt, don't save. A missed fact is better than a wrong one.
