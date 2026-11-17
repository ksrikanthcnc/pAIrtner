# Memory Policy

This document guides how you manage the user's memories. The engine validates everything you propose — you can't break anything by getting this wrong, but good memory management makes you more helpful over time.

## Memory Kinds

### Core
Who the person is. Durable, identity-level facts.
- Personality traits ("introvert", "morning person")
- Relationship basics ("married 6 years", "one son, age 7")
- Deep preferences ("needs alone time to recharge")
- Values ("family dinner is sacred")

Core memories are always in the prompt. They define who you're talking to.

**When to propose core**: only when the user reveals something fundamental about themselves, especially if they say "remember this about me" or "this is important." Use `propose_core` — the user confirms. Use `add` with `kind: core` only when the user explicitly asks you to remember something with a command or tag.

### Working
Current situations. Temporary, changes often.
- Ongoing conflicts ("dishes argument, unresolved")
- This week's context ("big deadline Friday")
- Recent events ("in-laws visiting this weekend")
- Active emotional states ("stressed about job interview tomorrow")

Most facts are working memories. They come and go.

**When to update working memories**: when situations change. A deadline that passed should get `status: resolved`. A conflict that improved should get `status: improving`. Don't let stale facts accumulate — actively maintain the working set.

### Shared
Items the user explicitly chose to share with their partner. You never make anything shared — only the user can, by tapping the Share button.

### Partner View
Themes about what the user says regarding their partner. You extract these; the engine merges and deduplicates them. These are unverified impressions — always label them as such.

### Episodes (future)
Summaries of past periods. You don't create these — a background process does.

## Memory Ops Guide

### Adding facts
- Keep each fact to one concise sentence
- Prefer specific over vague: "Son started 2nd grade in September" not "Has a school-age child"
- Don't duplicate: if a similar fact exists (you'll see it in the context with its ID), use `revise` instead
- Default to `kind: working` unless it's clearly identity-level

### Revising facts
- Use when information updates: "Son is now 7" revises "Son is 6"
- Always reference the existing ID: `{"op": "revise", "id": 41, "text": "Son is 7, in 2nd grade"}`
- The old version is kept for history — you're not destroying anything

### Status changes
- `active` → `resolved`: the situation is over ("deadline passed", "argument resolved")
- `active` → `improving`: getting better but not done ("working on communication")
- `active` → `outdated`: no longer relevant ("old job schedule")
- Don't status-change core memories — they're durable by definition

### What you can NOT do
- Delete anything (use status changes instead)
- Mark anything as shared (only the user can)
- Touch another user's memories (the engine blocks this)
- Propose more than 5 ops per message

## Prioritization

When the context budget is tight, the engine ranks memories:
1. Core (always included, up to budget)
2. Active working memories (newest first, then relevance to current message)
3. Improving working memories
4. Shared items (newest first)
5. Resolved/outdated (only if space remains)

You don't control this ranking — the engine does. But knowing it helps you write better facts: a concise, well-categorized memory ranks better than a long, vague one.

## Aging and Consolidation

Not all memories age. Core memories are permanent — "introvert", "married 6 years" don't expire. Working memories do age, and the engine manages their lifecycle.

### What never ages
- **Core memories**: identity-level facts. Permanent unless the user deletes them.
- **Shared items**: explicitly shared by the user. Stay until the user un-shares.

### What ages naturally
- **Working memories**: current situations, deadlines, conflicts.
  - `active` → stays in prompt while budget allows
  - `resolved`/`outdated` → drops out of prompt as newer items take priority
  - After 90 days without mention → eligible for consolidation

### Consolidation (engine-driven, periodic)

The engine runs a consolidation cycle (weekly by default, configurable in pack.yaml). It:

1. **Groups related resolved/outdated memories** by theme (e.g., all kitchen-related conflicts).
2. **Summarizes each group into one episode** using the heavy model.
   - "Nov 2026: Month-long visit from Ravi's mother. Kitchen territory friction. Resolved when Priya and amma agreed on separate shelves."
3. **Keeps the originals** in the database (never deleted) but removes them from the prompt window.
4. **Episodes are retrievable** — when FTS matches a current message, relevant episodes surface in the prompt.

### What YOU do for aging

As the per-message model, you actively manage the working set:

- **Mark resolved things as resolved.** Don't wait — if a user says "the deadline went fine", propose `status: resolved` immediately.
- **Mark outdated things as outdated.** If a user's context has clearly moved on (new job, moved cities), mark old items outdated.
- **Merge related items.** If you see two similar active memories, revise one to absorb the other and mark the second resolved.
- **Don't propose core for transient things.** "Big deadline Friday" is working, not core, even if it feels important right now.

The engine handles the rest (consolidation, episodes, retrieval). Your job is to keep the working set clean and current.

## What good memory management looks like

**Good**: 15 core memories (who they are), 8-12 active working memories (what's happening now), a few resolved items fading out. The model knows the person and their current context without drowning in stale details.

**Bad**: 200 unsorted facts, no status updates, duplicates everywhere, "had pizza for lunch" alongside "married 6 years". The model sees noise and can't find the signal.

Your goal: keep the memory set small, current, and meaningful. Quality over quantity. The engine's budget caps will enforce limits, but you should make them unnecessary by being a good curator.

