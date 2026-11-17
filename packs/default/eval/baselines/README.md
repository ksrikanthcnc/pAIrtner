# Baselines

Committed `summary.md` files from real-model runs of this pack, named `<pack-version>_<model>_<UTC date>.md`. They are for reading and for `diff -ru` against later runs; nothing here is a pass/fail score.

## 0.1.0_gemini-3.1-flash-lite_20261117.md (partial: 30 of 31 cases)

The run was made with `gemini-3.1-flash-lite` (the `models.default` of `settings.yaml`) on 2026-11-17. The provider returned intermittent 503 errors, so the run was resumed several times with `--resume`; each case in the summary completed with a real model answer.

One case is marked skipped: `md-01-both-sides`. `/mediate` uses the heavy models from `settings.yaml`, and at run time `gemini-3-flash-preview` answered 404 and `gemini-3.8-flash` answered 503, so the engine returned its fallback reply. Retrying with `--model gemini-3-flash-preview` and `--model gemini-3.8-flash` also failed (no case completed). Nothing was filled in for the missing case.

To complete the baseline when those models are reachable (quota resets daily on the free tier):

```bash
uv run python dev/prompt-eval/run_eval.py --llm gemini --only md-01-both-sides
```

or run everything again (about 3 minutes at the default 12 requests per minute):

```bash
uv run python dev/prompt-eval/run_eval.py --llm gemini
```

The runner reads `GEMINI_API_KEY` from the environment or the main checkout's `.env`, never prints it, and exits with code 3 if the model is unavailable or the quota is spent; see `docs/PACKS.md`.
