# pAIrtner — Typical User Flows & How the System Handles Them

## Flow 1: Daily Casual Check-in

```
Alice: "Hey, how was your day?"
Bot: "Hey Alice! How'd Max's recital go?"  ← proactive injection (remembered from 3 days ago)
Alice: "It was great! He nailed the piano piece"
Bot: "That's wonderful! Bob must have been proud too"

Memory ops:
  → add: "Max's piano recital went well" (working, intensity: 0.4)
  → status: "Max has piano recital Friday" → resolved
  
Context used:
  → core: "They have a 5-year-old son named Max"
  → working: "Max has piano recital Friday" (proactive injection, event_date match)
  → FTS5: none needed
```

**System requirement:** Proactive injection must surface upcoming/recent events naturally.
The bot should feel like it "remembers," not that it's reading a database.

---

## Flow 2: Ongoing Tension (Never Cleanly Resolved)

```
Week 1 — Alice: "Bob never helps with the dishes"
  → add: "Alice frustrated about household chore imbalance" (working, 0.8)
  
Week 3 — Alice: "We had a good week actually. Still wish he'd help more though"
  → status: "chore imbalance" → improving (NOT resolved)
  → add: "Had a good week" (working, 0.3)
  
Week 8 — Alice: "Ugh, the dishes again. Nothing changed"
  → status: "chore imbalance" → active (back from improving)
  → revise: intensity 0.8 → 0.9 (escalating)

This fact NEVER resolves. It's a recurring pattern.
```

**System requirement:** The consolidation model must handle facts that never resolve.
Weekly summaries capture the ongoing state: "Chore imbalance still unresolved, 
escalated this week." The fact stays active and high-scoring because it's 
regularly cited.

---

## Flow 3: Conflict Mediation via Relay

```
Alice: "I'm so tired of him choosing work over our family"
  → add: "Alice feels Bob prioritizes work over family" (working, 0.9)
  → relay_draft: "Hey, I've been missing quality time together..."
  
Alice clicks: "💬 Share with partner"
  → relay queued with cool-down
  → 15 min later, delivered to Bob's chat
  
Bob sees: "Your partner wanted to share: 'I've been missing quality time...'"
Bob: "I didn't realize she felt that way. I'll try to be home for dinner"
  → add (Bob): "Committed to being home for dinner" (working, 0.7)
  → The bot can track whether this commitment holds via future check-ins
```

**System requirement:** Cross-partner awareness without leaking private facts.
Bob's commitment is a SHARED fact visible to both. Alice's original venting
stays private.

---

## Flow 4: The Bot Proactively Pokes on Old Issues

```
6 weeks after "committed to being home for dinner" — no follow-up.
The fact is active, uncited for 6 weeks, intensity 0.7.

During a casual conversation:
Alice: "Things have been okay I guess"
Bot: "Glad to hear that! A while back, Bob mentioned wanting to be home 
     for dinner more. Has that been happening?"

This is the proactive review mechanism — subtle, conversational, not /review.
```

**System requirement:** Proactive injection extended to include old uncited 
working facts (not just dates). The injection surfaces them as natural 
conversation topics during casual interactions.

---

## Flow 5: Weekly Check-in (Structured)

```
Bot (to both partners, Sunday evening):
  "Hey! Quick weekly check-in 😊 How would you rate your relationship this week? 
   What went well? Anything to work on?"

Alice: "7/10. Liked our walk on Wednesday. Still arguing about bedtime for Max."
Bob: "8/10. Good week. Wish we had more alone time."

Bot generates shared summary:
  "This week: You both valued the Wednesday walk (shared positive). 
   Alice wants to align on Max's bedtime. Bob is looking for more couple time.
   Both concerns are about TIME — suggest planning a weekly date night?"

  → Episode: weekly check-in summary with both perspectives
  → Shared goals suggested: "Align on Max's bedtime", "Weekly date night"
```

**System requirement:** Check-in summaries are a separate episode type that 
captures BOTH partner perspectives in one record.

---

## Flow 6: Import & Cold Start

```
New user imports 6 months of WhatsApp history:
  {"messages": [
    {"from": "alice", "text": "Can you pick up Max?", "ts": "2026-05-15T08:30:00Z"},
    ...1200 messages...
  ]}

System:
  1. Store raw messages with original timestamps
  2. Batch extract facts (50 messages per LLM call)
  3. Create weekly summaries for each ISO week with activity
  4. Create monthly rollups for complete months
  5. Score everything, ready to serve

After import: bot already "knows" the couple's patterns, names, topics.
First real conversation feels like the bot has been there all along.
```

**System requirement:** Import pipeline reuses the same extraction + consolidation
code. No separate import-specific logic except the message format parser.

---

## Flow 7: Model Switching / Rebuild

```
User was on Gemini Flash Lite, switches to local Qwen3 8B.

System:
  1. Keep all raw messages (immutable)
  2. Keep all facts (tagged with model=gemini-3.5-flash-lite)
  3. Run rebuild_knowledge(model="qwen3:8b")
     → Batch-processes raw messages with new model
     → New facts tagged model=qwen3:8b
     → Old facts NOT deleted — available for comparison
  4. Run consolidation for all periods with new facts
  5. Score uses model=qwen3:8b facts by default
```

**System requirement:** Model column on facts enables A/B comparison and 
non-destructive model switching.

---

## Flow 8: Sensitive Information Handling

```
Alice: "I think Bob drinks too much on weekends but I can't bring it up"
  → add (PRIVATE): "Worried about Bob's weekend drinking" (working, 0.9)
  → This fact NEVER appears in Bob's prompt
  → The bot never says "your partner mentioned your drinking"
  
When talking to Alice about health/weekends:
  Bot might say: "Weekends can be tricky for establishing routines. 
  Have you found ways to make them more enjoyable together?"
  → Subtle redirection without revealing the private fact
  
If Alice shares it: "#send I think we should talk about our weekend habits"
  → The relay is softened and sent through the approval flow
  → If approved, Bob sees a gentle version
```

**System requirement:** Privacy is structural (DB query filters), not prompt-based.
The LLM's indirect hinting comes from seeing the fact in Alice's context.

---

## Flow 9: The Spiral (Escalating Conflict)

```
Day 1: Alice vents about work
Day 2: Bob feels dismissed when he tries to talk about HIS work
Day 3: Alice says "you never listen"  ← Gottman horseman: criticism
Day 4: Bob goes silent (stonewalling)
Day 5: Both frustrated, short messages

System detects:
  → Horseman detected: criticism (day 3)
  → Horseman detected: stonewalling (day 4)
  → Interaction score trending negative
  → Conflict opened: "mutual work-stress dismissal"

Bot response (to Alice, day 5):
  "I notice things have been tense this week. These patterns are common when
  both partners are stressed. Sometimes starting with 'I feel...' instead of
  'you never...' can open the door. Want to try drafting something to Bob?"
```

**System requirement:** Pattern detection (horsemen, interaction scores) feeds 
into both the memory system AND the real-time response. The timeline tracks 
the conflict arc.

---

## Flow 10: Long-term Memory Retrieval

```
8 months in. Alice mentions "our anniversary."
  → FTS5 hits: "Anniversary is March 15" (core fact, high score)
  → If not in core: LLM returns search_query: "anniversary date plans"
  → Second pass with enriched context
  
Bot: "Your anniversary is coming up in March! Last year you mentioned wanting
     to do something special. Any ideas this year?"
  → Retrieved from quarterly episode: "In Q1, discussed anniversary plans"
  → FTS5 hit on "anniversary" from core facts
```

**System requirement:** Two retrieval paths must work together:
1. Score-based pre-loading (automatic, every message)
2. FTS5 on-demand search (LLM-triggered for specific queries)

---

## The Consolidation Schedule (Unified)

```
             Active facts (live, scoring, searchable)
                    │
    ┌───────────────┼───────────────┐
    │               │               │
    ▼               ▼               ▼
core facts    working facts     episode facts
(permanent)   (active/resolved)  (summaries)
    │               │               │
    │          ┌────┴────┐     ┌────┴────┐
    │          │ Week 33 │     │ Week 32 │ ...
    │          │ summary │     │ summary │
    │          └────┬────┘     └────┬────┘
    │               │               │
    │          ┌────┴───────────────┴────┐
    │          │     July summary        │
    │          └────────────┬────────────┘
    │                       │
    │          ┌────────────┴────────────┐
    │          │     Q3 summary          │
    │          └─────────────────────────┘

Weekly:   Summarize ALL activity from ISO week (resolved + ongoing + new)
          → Working facts STAY active. Summary is additive.
Monthly:  Roll up weekly summaries → monthly. Archive weekly summaries.
Quarterly: Roll up monthly → quarterly. Archive monthly summaries.
```

No fact is ever silently auto-archived. Summaries are parallel context, not replacements.
