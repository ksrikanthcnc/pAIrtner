From: advisor
Date: 2026-11-16
Subject: Rename to pAIrtner, add allowlist, add provider switch

```
Three small changes. Keep tests green; commit when they pass.

1. Rename the product to "pAIrtner" in user-facing text (i18n strings, /start, /help, README) and DESIGN.md. Keep the Python package name unchanged.

2. Add an access allowlist. New env var ALLOWED_USER_IDS (comma-separated Telegram user IDs), documented in .env.example. Every handler and message path silently ignores users not on the list: no reply, no DB row, no LLM call. Add tests proving that.

3. Make the LLM provider switchable. Keep the LLMClient protocol. Add an OpenAI-compatible client (base URL + key from env) next to the Gemini one, so Groq, OpenRouter, Mistral or local Ollama work. Config: LLM_PROVIDER (gemini | openai_compat), LLM_BASE_URL, and a provider fallback order. Never hardcode or print keys. Add mocked tests for the fallback.

```
