"""Run extraction + consolidation benchmark against any LLM.

Tests a model on the benchmark DB and scores it against ground truth.

Usage:
    # Ollama (local)
    uv run python dev/benchmark/run_benchmark.py --model qwen3:8b --provider ollama

    # Gemini
    uv run python dev/benchmark/run_benchmark.py --model gemini-3.5-flash-lite --provider gemini

    # Just score existing results (no LLM call)
    uv run python dev/benchmark/run_benchmark.py --score-only --results dev/benchmark/results/qwen3-8b.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BENCH_DB = Path(__file__).parent / "bench.db"
RESULTS_DIR = Path(__file__).parent / "results"
EXTRACT_PROMPT = Path(__file__).resolve().parents[2] / "v2" / "prompts" / "extract.md"


async def call_ollama(system: str, user: str, model: str) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.3, "num_predict": 4096},
            },
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        if "<think>" in content:
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return json.loads(content)


async def call_gemini(system: str, user: str, model: str) -> dict:
    from google import genai
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise ValueError("GEMINI_API_KEY not found")

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model,
        contents=[user],
        config={
            "system_instruction": system,
            "response_mime_type": "application/json",
            "temperature": 0.3,
        },
    )
    return json.loads(resp.text)


async def extract_from_messages(
    messages: list[dict], provider: str, model: str,
) -> tuple[dict, float]:
    """Run extraction on a batch of messages. Returns (result, latency_sec)."""
    system = EXTRACT_PROMPT.read_text()

    msg_lines = []
    for m in messages:
        name = m.get("name", "User")
        msg_lines.append(f"[{name}]: {m['text']}")
    user_content = "Existing knowledge: (none)\n\nMessages:\n" + "\n".join(msg_lines)

    call = call_ollama if provider == "ollama" else call_gemini
    start = time.monotonic()
    result = await call(system, user_content, model)
    latency = time.monotonic() - start
    return result, latency


def score_extraction(result: dict, ground_truth: dict) -> dict:
    """Score extraction results against ground truth.

    Returns a dict with hit counts, miss counts, and details.
    """
    extracted_texts = [f.get("text", "") for f in result.get("new_facts", [])]
    extracted_lower = [t.lower() for t in extracted_texts]

    scores = {}
    for category, expected_facts in ground_truth.items():
        hits = []
        misses = []
        for expected in expected_facts:
            # Fuzzy match: check if key words from the expected fact appear
            key_words = _extract_key_words(expected)
            found = any(
                sum(1 for kw in key_words if kw in ext) >= len(key_words) * 0.5
                for ext in extracted_lower
            )
            if found:
                hits.append(expected)
            else:
                misses.append(expected)

        scores[category] = {
            "total": len(expected_facts),
            "hits": len(hits),
            "misses": len(misses),
            "hit_details": hits,
            "miss_details": misses,
            "accuracy": len(hits) / len(expected_facts) if expected_facts else 0,
        }

    # Overall
    total_expected = sum(s["total"] for s in scores.values())
    total_hits = sum(s["hits"] for s in scores.values())
    scores["overall"] = {
        "total": total_expected,
        "hits": total_hits,
        "accuracy": total_hits / total_expected if total_expected else 0,
    }

    return scores


def _extract_key_words(fact: str) -> list[str]:
    """Extract key words from a fact for fuzzy matching."""
    stop_words = {"is", "a", "an", "the", "to", "of", "for", "and", "in", "on",
                  "has", "have", "been", "was", "are", "they", "their", "about",
                  "but", "that", "with", "from", "more", "want", "would", "should"}
    words = re.findall(r'\b[a-z]+\b', fact.lower())
    return [w for w in words if w not in stop_words and len(w) > 2]


async def run_benchmark(provider: str, model: str) -> dict:
    """Run the full benchmark and return results."""
    import aiosqlite

    if not BENCH_DB.exists():
        print("Benchmark DB not found. Run seed_db.py first.")
        sys.exit(1)

    # Load ground truth
    async with aiosqlite.connect(str(BENCH_DB)) as db:
        row = await db.execute_fetchall(
            "SELECT value FROM benchmark_meta WHERE key = 'ground_truth'"
        )
        gt = json.loads(row[0][0])

    # Load messages
    async with aiosqlite.connect(str(BENCH_DB)) as db:
        db.row_factory = aiosqlite.Row
        alice_msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE user_id = ? ORDER BY created_at",
            (gt["alice_id"],),
        )
        bob_msgs = await db.execute_fetchall(
            "SELECT * FROM messages WHERE user_id = ? ORDER BY created_at",
            (gt["bob_id"],),
        )

    # Test extraction on Alice's messages (user messages only)
    alice_user_msgs = [
        {"name": "Alice", "text": m["content"]}
        for m in alice_msgs if m["role"] == "user"
    ]
    bob_user_msgs = [
        {"name": "Bob", "text": m["content"]}
        for m in bob_msgs if m["role"] == "user"
    ]

    print(f"\nTesting: {provider}/{model}")
    print(f"Alice messages: {len(alice_user_msgs)}, Bob messages: {len(bob_user_msgs)}")

    # Extract from Alice's messages
    print("\n--- Alice extraction ---")
    alice_result, alice_latency = await extract_from_messages(
        alice_user_msgs, provider, model,
    )
    alice_facts = alice_result.get("new_facts", [])
    print(f"  Facts: {len(alice_facts)} in {alice_latency:.1f}s")
    for f in alice_facts:
        print(f"    [{f.get('kind', '?')}] (i={f.get('intensity', '?')}) {f.get('text', '')[:80]}")

    # Extract from Bob's messages
    print("\n--- Bob extraction ---")
    bob_result, bob_latency = await extract_from_messages(
        bob_user_msgs, provider, model,
    )
    bob_facts = bob_result.get("new_facts", [])
    print(f"  Facts: {len(bob_facts)} in {bob_latency:.1f}s")
    for f in bob_facts:
        print(f"    [{f.get('kind', '?')}] (i={f.get('intensity', '?')}) {f.get('text', '')[:80]}")

    # Score against ground truth
    # Split ground truth by who should know what
    alice_gt = {
        "core": gt["ground_truth"]["core"],
        "working_ongoing": gt["ground_truth"]["working_ongoing"],
        "sensitive": gt["ground_truth"]["sensitive_private"][:2],  # Alice's sensitive
    }
    bob_gt = {
        "core": gt["ground_truth"]["core"],
        "working_ongoing": gt["ground_truth"]["working_ongoing"],
        "sensitive": gt["ground_truth"]["sensitive_private"][2:],  # Bob's sensitive
    }

    alice_scores = score_extraction(alice_result, alice_gt)
    bob_scores = score_extraction(bob_result, bob_gt)

    print("\n--- Scoring ---")
    for who, scores in [("Alice", alice_scores), ("Bob", bob_scores)]:
        print(f"\n  {who}:")
        for cat, s in scores.items():
            if cat == "overall":
                print(f"    OVERALL: {s['hits']}/{s['total']} ({s['accuracy']:.0%})")
            else:
                print(f"    {cat}: {s['hits']}/{s['total']} ({s['accuracy']:.0%})")
                for miss in s.get("miss_details", []):
                    print(f"      ❌ {miss[:60]}")

    # Save results
    RESULTS_DIR.mkdir(exist_ok=True)
    slug = f"{provider}-{model}".replace("/", "-").replace(":", "-")
    result_data = {
        "provider": provider,
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "alice": {
            "facts": alice_facts,
            "latency_sec": alice_latency,
            "scores": alice_scores,
        },
        "bob": {
            "facts": bob_facts,
            "latency_sec": bob_latency,
            "scores": bob_scores,
        },
    }
    result_path = RESULTS_DIR / f"{slug}.json"
    result_path.write_text(json.dumps(result_data, indent=2))
    print(f"\nResults saved: {result_path}")

    return result_data


async def main():
    parser = argparse.ArgumentParser(description="Run LLM benchmark")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--provider", default="ollama", choices=["ollama", "gemini"])
    args = parser.parse_args()
    await run_benchmark(args.provider, args.model)


if __name__ == "__main__":
    asyncio.run(main())
