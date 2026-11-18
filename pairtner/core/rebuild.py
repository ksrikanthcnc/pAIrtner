"""Knowledge rebuild — re-extract from raw messages.

Used when switching LLM models or fixing bad extraction.
Processes raw messages in chronological batches, extracts facts,
then runs consolidation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pairtner import db as db_mod
from pairtner.llm import LLMClient
from pairtner.storage.helpers import _fetch_all

log = logging.getLogger(__name__)

BATCH_SIZE = 50


async def rebuild_knowledge(
    db_path: str,
    llm: LLMClient,
    user_id: int,
    *,
    model: str | None = None,
    api_key_override: str | None = None,
    batch_size: int = BATCH_SIZE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-extract knowledge from raw messages.

    Does NOT delete existing knowledge — adds alongside with `model` column
    distinguishing old vs new extraction. This lets you A/B compare models.

    Args:
        db_path: Path to SQLite database.
        llm: LLM client for extraction calls.
        user_id: User to rebuild knowledge for.
        model: Model name to record in the `model` column.
        api_key_override: Override API key for LLM calls.
        batch_size: Messages per extraction batch.
        dry_run: If True, extract but don't write to DB.

    Returns:
        Summary dict with counts.
    """
    from pairtner.core.memory_api import MemoryAPI

    # Load extraction prompt
    extract_prompt = _load_extract_prompt()

    # Get all raw messages chronologically
    messages = await _fetch_all(
        db_path,
        "SELECT id, role, content, created_at FROM messages "
        "WHERE user_id = ? ORDER BY id",
        (user_id,),
    )

    if not messages:
        return {"batches": 0, "facts_extracted": 0, "errors": 0}

    api = MemoryAPI(db_path)
    total_facts = 0
    total_batches = 0
    errors = 0

    # Process in chronological batches
    for i in range(0, len(messages), batch_size):
        batch = messages[i : i + batch_size]
        total_batches += 1

        # Format batch as conversation
        batch_text = "\n".join(
            f"[{m['role']}] {m['content']}" for m in batch
        )

        # Get existing knowledge (so extraction can avoid duplicates)
        existing = await db_mod.get_active_facts(db_path, user_id)
        existing_lines = "\n".join(
            f"[{f['id']}] ({f.get('kind', 'working')}) {f['text']}"
            for f in existing
        )

        user_msg = f"Existing knowledge:\n{existing_lines or '(none)'}\n\nRecent messages:\n{batch_text}"

        try:
            result = await llm.generate(
                user_msg,
                extract_prompt,
                model=model,
                api_key_override=api_key_override,
            )

            # Parse extraction result
            new_facts = result.get("new_facts", [])
            if isinstance(new_facts, str):
                try:
                    new_facts = json.loads(new_facts)
                except (json.JSONDecodeError, TypeError):
                    new_facts = []

            if dry_run:
                total_facts += len(new_facts)
                log.info(
                    "rebuild [dry_run]: batch %d — %d facts extracted",
                    total_batches, len(new_facts),
                )
                continue

            # Apply extracted facts
            msg_ids = [m["id"] for m in batch]
            source_ids_json = json.dumps(msg_ids)

            for fact in new_facts:
                if not isinstance(fact, dict):
                    continue
                text = fact.get("text", "").strip()
                if not text:
                    continue

                kind = fact.get("kind", "working")
                if kind not in ("working", "core"):
                    kind = "working"

                intensity = 0.5
                try:
                    intensity = max(0.0, min(1.0, float(fact.get("intensity", 0.5))))
                except (ValueError, TypeError):
                    pass

                await api.add_fact(
                    user_id,
                    text,
                    kind=kind,
                    intensity=intensity,
                    source_ids=source_ids_json,
                    model=model or "rebuild",
                )
                total_facts += 1

            # Also apply updates, state_changes from extraction
            for update in result.get("updates", []):
                if isinstance(update, dict) and "id" in update and "text" in update:
                    await api.update_text(update["id"], update["text"])

            for sc in result.get("state_changes", []):
                if isinstance(sc, dict) and "id" in sc and "state" in sc:
                    await api.set_status(sc["id"], sc["state"], reason=sc.get("reason"))

            log.info(
                "rebuild: batch %d/%d — %d new facts",
                total_batches,
                (len(messages) + batch_size - 1) // batch_size,
                len(new_facts),
            )

        except Exception:
            log.exception("rebuild: batch %d failed", total_batches)
            errors += 1

    # Run consolidation after all batches
    if not dry_run and total_facts > 0:
        from pairtner.core.consolidation import run_consolidation
        try:
            episodes = await run_consolidation(
                db_path, llm, user_id, api_key_override=api_key_override,
            )
            log.info("rebuild: consolidation created %d episodes", len(episodes))
        except Exception:
            log.exception("rebuild: consolidation failed")

    return {
        "batches": total_batches,
        "facts_extracted": total_facts,
        "errors": errors,
        "message_count": len(messages),
    }


def _load_extract_prompt() -> str:
    """Load the extraction prompt from v2/prompts/extract.md or fall back to inline."""
    import os
    for path in [
        os.path.join(os.path.dirname(__file__), "../../v2/prompts/extract.md"),
        os.path.join(os.path.dirname(__file__), "../../packs/default/memory.md"),
    ]:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            with open(abs_path) as f:
                return f.read()

    # Inline fallback
    return (
        "You are a knowledge extraction engine. Given messages and existing facts, "
        "extract new facts as JSON: {\"new_facts\": [{\"text\": \"...\", \"kind\": \"working|core\", "
        "\"intensity\": 0.0-1.0}], \"updates\": [], \"state_changes\": [], "
        "\"duplicates\": [], \"contradictions\": []}. Empty arrays for sections with no findings."
    )
