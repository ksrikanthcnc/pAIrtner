"""Chat import — batch-extract knowledge from imported chat history.

Accepts a JSON array of messages with timestamps, processes them in
chronological batches, extracts facts via LLM, then runs consolidation.

Supported input formats:
  - Native: [{"from": "Alice", "text": "...", "ts": "2024-01-15T10:30:00"}, ...]
  - WhatsApp export: auto-detected and converted
  - Telegram export: auto-detected from Telegram's JSON format

Usage:
  import_chat(db_path, llm, user_id, messages_json, partner_name="Bob")

The importer processes messages in period batches (weekly by default),
runs extraction on each batch, and finally consolidates the extracted
facts into episode summaries per period.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pairtner import db as db_mod
from pairtner.llm import LLMClient

log = logging.getLogger(__name__)

BATCH_SIZE = 30  # messages per LLM call
PERIOD = "weekly"  # group messages by week for extraction


# ---------------------------------------------------------------------------
# Input format detection + normalization
# ---------------------------------------------------------------------------

def _normalize_messages(raw: list[dict] | str) -> list[dict[str, str]]:
    """Normalize input to [{"from": str, "text": str, "ts": str}].

    Accepts:
    - Already-normalized dicts
    - Telegram JSON export (messages array with from/from_id)
    - Simple {"sender", "message", "date"} format
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, list) or not raw:
        raise ValueError("Expected a non-empty JSON array of messages")

    sample = raw[0]
    messages: list[dict[str, str]] = []

    # Detect Telegram export format
    if "from_id" in sample or ("from" in sample and "text_entities" in sample):
        for m in raw:
            text = m.get("text", "")
            if isinstance(text, list):
                # Telegram rich text: list of strings/dicts
                parts = []
                for part in text:
                    if isinstance(part, str):
                        parts.append(part)
                    elif isinstance(part, dict):
                        parts.append(part.get("text", ""))
                text = "".join(parts)
            if not text or not text.strip():
                continue
            messages.append({
                "from": m.get("from", m.get("from_id", "Unknown")),
                "text": text.strip(),
                "ts": m.get("date", m.get("date_unixtime", "")),
            })
        return messages

    # Detect {"sender", "message", "date"} format
    if "sender" in sample and "message" in sample:
        for m in raw:
            text = (m.get("message") or "").strip()
            if not text:
                continue
            messages.append({
                "from": m["sender"],
                "text": text,
                "ts": m.get("date", m.get("timestamp", "")),
            })
        return messages

    # Native format: {"from", "text", "ts"}
    for m in raw:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        messages.append({
            "from": m.get("from", "Unknown"),
            "text": text,
            "ts": m.get("ts", m.get("timestamp", m.get("date", ""))),
        })
    return messages


def _parse_ts(ts_str: str) -> datetime | None:
    """Best-effort timestamp parsing."""
    if not ts_str:
        return None
    # Unix timestamp (Telegram date_unixtime)
    try:
        val = float(ts_str)
        if val > 1e9:  # looks like unix epoch
            return datetime.fromtimestamp(val, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        pass
    # ISO format
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y, %H:%M",
        "%m/%d/%Y, %H:%M",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _week_key(dt: datetime) -> str:
    """Return ISO week key like '2024-W03'."""
    return f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"


# ---------------------------------------------------------------------------
# Extraction prompt for batch import
# ---------------------------------------------------------------------------

IMPORT_SYSTEM = """You are a relationship knowledge extractor. Given a batch of chat messages
between two people, extract the key facts, preferences, and relationship dynamics.

Respond with ONLY valid JSON:
{
  "facts": [
    {
      "text": "What was learned",
      "visibility": "shared",
      "tag": "topic tag"
    }
  ]
}

Rules:
- Extract facts about preferences, habits, feelings, relationship dynamics.
- Skip greetings, logistics ("ok", "coming home at 6"), and trivial exchanges.
- Each fact should be a self-contained statement.
- visibility: "shared" for things both partners know, "private" for one person's inner state.
- tag: a short topic label (e.g. "work", "family", "feelings", "habits", "conflict").
- Write facts in third person using the person's name.
- Maximum 15 facts per batch — keep only the most significant.
- If the batch has no meaningful content, return {"facts": []}.
"""


# ---------------------------------------------------------------------------
# Main import function
# ---------------------------------------------------------------------------

async def import_chat(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    raw_messages: list[dict] | str,
    *,
    partner_name: str | None = None,
    user_name: str | None = None,
    batch_size: int = BATCH_SIZE,
    dry_run: bool = False,
    consolidate: bool = True,
) -> dict[str, Any]:
    """Import chat history and extract knowledge.

    Args:
        db_path: SQLite database path.
        llm: LLM client for extraction.
        user_id: User performing the import.
        raw_messages: Chat messages (JSON array or string).
        partner_name: Name of the partner in the chat.
        user_name: Name of the importing user in the chat.
        batch_size: Messages per LLM extraction call.
        dry_run: Extract but don't save to DB.
        consolidate: Run consolidation after extraction.

    Returns:
        Summary with counts and extracted facts.
    """
    # Normalize input
    if not raw_messages:
        return {"status": "empty", "total_messages": 0, "facts_extracted": 0}
    messages = _normalize_messages(raw_messages)

    # Sort by timestamp
    for m in messages:
        m["_dt"] = _parse_ts(m["ts"])
    messages.sort(key=lambda m: m.get("_dt") or datetime.min.replace(tzinfo=timezone.utc))

    # Detect names if not provided
    senders = set(m["from"] for m in messages)
    if not partner_name and len(senders) == 2:
        # If user_name provided, partner is the other one
        if user_name and user_name in senders:
            partner_name = (senders - {user_name}).pop()
        else:
            # Pick the less frequent sender as the user (they import their own chat)
            counts = defaultdict(int)
            for m in messages:
                counts[m["from"]] += 1
            sorted_senders = sorted(counts, key=counts.get)
            if user_name is None:
                user_name = sorted_senders[0]
            partner_name = sorted_senders[1] if len(sorted_senders) > 1 else "Partner"

    # Group by week
    weekly_batches: dict[str, list[dict]] = defaultdict(list)
    no_ts_batch: list[dict] = []
    for m in messages:
        dt = m.get("_dt")
        if dt:
            weekly_batches[_week_key(dt)].append(m)
        else:
            no_ts_batch.append(m)

    # If no timestamps, treat as one batch
    if not weekly_batches and no_ts_batch:
        weekly_batches["unknown"] = no_ts_batch

    total_facts = 0
    all_facts: list[dict] = []
    batch_count = 0

    for week, week_msgs in sorted(weekly_batches.items()):
        # Split week into sub-batches of batch_size
        for i in range(0, len(week_msgs), batch_size):
            chunk = week_msgs[i : i + batch_size]
            batch_count += 1

            # Format messages for LLM
            lines = []
            for m in chunk:
                ts_str = ""
                if m.get("_dt"):
                    ts_str = f" [{m['_dt'].strftime('%Y-%m-%d %H:%M')}]"
                lines.append(f"{m['from']}{ts_str}: {m['text']}")

            user_msg = f"Chat between {user_name or 'User'} and {partner_name or 'Partner'}:\n\n"
            user_msg += "\n".join(lines)

            try:
                result = await llm.generate(user_msg, IMPORT_SYSTEM)
                facts = _parse_import_facts(result)

                for fact in facts:
                    text = fact.get("text", "").strip()
                    if not text:
                        continue

                    visibility = fact.get("visibility", "shared")
                    if visibility not in ("shared", "private"):
                        visibility = "shared"

                    tag = fact.get("tag", "")

                    if not dry_run:
                        fact_id = await db_mod.add_fact(
                            db_path, user_id, text, visibility,
                            fact_type="working",
                            tag=tag or None,
                        )
                        fact["id"] = fact_id

                    all_facts.append(fact)
                    total_facts += 1

            except Exception:
                log.exception(
                    "import: batch %d (week %s) failed for user %s",
                    batch_count, week, user_id,
                )

    # Run consolidation on the imported facts
    consolidation_result = None
    if consolidate and not dry_run and total_facts > 0:
        try:
            from pairtner.core.consolidation import run_consolidation
            episodes = await run_consolidation(db_path, llm, user_id)
            consolidation_result = {
                "episodes_created": len(episodes),
                "facts_archived": sum(e.get("source_count", 0) for e in episodes),
            }
        except Exception:
            log.exception("import: consolidation failed for user %s", user_id)

    return {
        "status": "ok",
        "total_messages": len(messages),
        "batches_processed": batch_count,
        "facts_extracted": total_facts,
        "facts": all_facts if dry_run else [],  # only return facts in dry_run
        "weeks_covered": len(weekly_batches),
        "consolidation": consolidation_result,
        "names_detected": {
            "user": user_name,
            "partner": partner_name,
        },
    }


def _parse_import_facts(result: dict[str, Any]) -> list[dict]:
    """Extract facts from LLM import response."""
    facts = result.get("facts")
    if isinstance(facts, list):
        return facts

    reply = result.get("reply", "")
    if isinstance(reply, str):
        try:
            parsed = json.loads(reply)
            if isinstance(parsed, dict) and "facts" in parsed:
                return parsed["facts"]
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []
