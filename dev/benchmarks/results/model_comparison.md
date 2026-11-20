# Model comparison: multi-topic summary quality

## Test: 10-topic dense week (argument + promotion + kids + therapy + chores)

### gemma3:4b (2s/call, 3.4GB)
- Captured: 7/10 topics
- Missed: couples therapy disagreement, promotion acknowledgment detail
- Narrative: fluent but slightly generic ("communication issues, differing priorities")
- Verdict: good for routine weeks, may drop 2-3 minor topics on heavy days

### qwen3:8b (10-15s/call, 5.2GB)  
- Captured: 9/10 topics
- Explicitly names therapy disagreement and unacknowledged promotion
- Narrative: lists ALL topics specifically
- Verdict: better for comprehensive capture, 5-7x slower

### Recommendation
- gemma3:4b for daily/weekly summaries (fast, frequent, OK to miss minor topics)
- qwen3:8b for monthly/quarterly summaries (less frequent, need comprehensive capture)
- Or: gemma3:4b for all, with improved SUMMARY_SYSTEM prompt (already done)

### Improved SUMMARY_SYSTEM prompt impact
Both models improved with "name concrete events and causes; list ALL distinct topics":
- Before: "fluctuating emotions", "routines and apologies" 
- After: "chore imbalances", "mother's opinions", "formal chore rota"
