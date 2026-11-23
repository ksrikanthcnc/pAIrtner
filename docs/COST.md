# API Cost & Rate Limits

## Gemini (primary provider, free tier)
- 15 RPM (requests per minute)
- 1,500 RPD (requests per day)
- 1M TPM (tokens per minute)

## Token Budget per Feature
- Chat + extraction: ~2K-6K input, ~1K output (1 call)
- Nudge generation: ~1K input, ~200 output (1 call)
- Summary (per tag group): ~2K-4K input, ~500 output (1 call)
- Rolling summary: ~3K input, ~800 output (1 call)

## Optimization Rules
- Never add an extra LLM call where one can do the job
- Extraction happens IN the chat call (not separate)
- Batch offline messages into one call
- Prefer richer single calls over multiple thin calls
- For local Ollama: calls = latency (tokens are free)
- For Gemini: both calls and tokens cost, but calls have hard rate limits

## Budget at Free Tier
- ~1500 calls/day across all features
- Active couple chatting: ~50-100 calls/day
- Nudge loop: ~2-4 calls/day
- Summaries: ~5-10 calls/day
- Headroom: plenty for 2 users on free tier
