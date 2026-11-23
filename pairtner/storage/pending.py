"""Relay pending, batches, delivery, feedback.

Canonical implementations of relay/pending database operations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pairtner.storage.helpers import (
    _now_iso, _to_iso, _fetch_one, _fetch_all, _execute,
)


# ---------------------------------------------------------------------------
# Single pending relays
# ---------------------------------------------------------------------------

async def create_pending(
    db_path: str,
    sender_id: int,
    receiver_id: int,
    draft: str,
) -> int:
    """Create a pending relay; return its id."""
    return await _execute(
        db_path,
        "INSERT INTO relay_queue (sender_id, receiver_id, draft) VALUES (?, ?, ?)",
        (sender_id, receiver_id, draft),
    )


async def get_pending(db_path: str, pending_id: int) -> dict[str, Any] | None:
    """Return a pending relay row by ID, or ``None``."""
    return await _fetch_one(
        db_path, "SELECT * FROM relay_queue WHERE id = ?", (pending_id,)
    )


async def update_pending_status(
    db_path: str,
    pending_id: int,
    status: str,
    send_after: str | None = None,
) -> None:
    """Transition a pending row to *status*, optionally setting *send_after*."""
    if send_after:
        await _execute(
            db_path,
            "UPDATE relay_queue SET status = ?, send_after = ? WHERE id = ?",
            (status, send_after, pending_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE relay_queue SET status = ? WHERE id = ?",
            (status, pending_id),
        )


async def update_pending_edited_draft(
    db_path: str, pending_id: int, edited_draft: str
) -> None:
    """Store the user-edited draft text on a pending row."""
    await _execute(
        db_path,
        "UPDATE relay_queue SET edited_draft = ? WHERE id = ?",
        (edited_draft, pending_id),
    )


async def get_deliverable(db_path: str) -> list[dict[str, Any]]:
    """Get approved pending messages whose cooldown has elapsed."""
    now = _now_iso()
    return await _fetch_all(
        db_path,
        "SELECT * FROM relay_queue WHERE status = 'approved' AND send_after <= ?",
        (now,),
    )


async def mark_as_sent(db_path: str, pending_id: int) -> None:
    """Mark a pending row as sent and record the delivery timestamp."""
    now = _now_iso()
    await _execute(
        db_path,
        "UPDATE relay_queue SET status = 'sent', sent_at = ? WHERE id = ?",
        (now, pending_id),
    )


async def get_pending_count(db_path: str, user_id: int) -> int:
    """Count pending rows awaiting approval or delivery for a given sender."""
    row = await _fetch_one(
        db_path,
        "SELECT COUNT(*) AS cnt FROM relay_queue "
        "WHERE sender_id = ? AND status IN ('awaiting_approval', 'approved')",
        (user_id,),
    )
    return row["cnt"] if row else 0


async def get_recent_pending_count(
    db_path: str, sender_id: int, since: str
) -> int:
    """Count pending rows created since a timestamp for a sender."""
    row = await _fetch_one(
        db_path,
        "SELECT COUNT(*) AS cnt FROM relay_queue "
        "WHERE sender_id = ? AND created_at >= ?",
        (sender_id, since),
    )
    return row["cnt"] if row else 0


async def get_sent_relays(
    db_path: str, user_id: int, limit: int = 10
) -> list[dict[str, Any]]:
    """Fetch sent pending rows where the user is sender or receiver.

    Ordered by ``sent_at`` DESC with LIMIT.
    """
    return await _fetch_all(
        db_path,
        "SELECT * FROM relay_queue "
        "WHERE (sender_id = ? OR receiver_id = ?) AND status = 'sent' "
        "ORDER BY sent_at DESC LIMIT ?",
        (user_id, user_id, limit),
    )


async def get_sent_relays_since(
    db_path: str, user_id: int, since: str
) -> list[dict[str, Any]]:
    """Get relays sent by or to *user_id* since a timestamp."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM relay_queue "
        "WHERE (sender_id = ? OR receiver_id = ?) "
        "AND status = 'sent' AND sent_at >= ? "
        "ORDER BY sent_at",
        (user_id, user_id, since),
    )


# ---------------------------------------------------------------------------
# Feedback on relays
# ---------------------------------------------------------------------------

async def update_pending_feedback(
    db_path: str, pending_id: int, feedback: str
) -> None:
    """Record the sender's feedback on a delivered relay."""
    if feedback not in ("yes", "not_sure", "no"):
        feedback = "not_sure"
    await _execute(
        db_path,
        "UPDATE relay_queue SET feedback = ? WHERE id = ?",
        (feedback, pending_id),
    )


async def mark_pending_acknowledged(
    db_path: str, pending_id: int
) -> None:
    """Set acknowledged_at to now on a delivered relay."""
    now = _now_iso()
    await _execute(
        db_path,
        "UPDATE relay_queue SET acknowledged_at = ? WHERE id = ?",
        (now, pending_id),
    )


async def get_pending_needing_feedback(
    db_path: str, sender_id: int
) -> list[dict[str, Any]]:
    """Return sent relays FROM this sender where feedback is NULL and within 24h."""
    cutoff = _to_iso(datetime.now(timezone.utc) - timedelta(hours=24))
    return await _fetch_all(
        db_path,
        "SELECT * FROM relay_queue "
        "WHERE sender_id = ? AND status = 'sent' "
        "AND feedback IS NULL AND sent_at >= ? "
        "ORDER BY sent_at DESC",
        (sender_id, cutoff),
    )


async def get_feedback_stats(
    db_path: str, user_id: int
) -> dict[str, int]:
    """Return counts of yes/not_sure/no feedback for a sender."""
    rows = await _fetch_all(
        db_path,
        "SELECT feedback, COUNT(*) AS cnt FROM relay_queue "
        "WHERE sender_id = ? AND feedback IS NOT NULL "
        "GROUP BY feedback",
        (user_id,),
    )
    return {row["feedback"]: row["cnt"] for row in rows}


async def get_unacknowledged_relays_to_user(
    db_path: str, receiver_id: int
) -> list[dict[str, Any]]:
    """Return sent relays TO this user that are not yet acknowledged."""
    return await _fetch_all(
        db_path,
        "SELECT * FROM relay_queue "
        "WHERE receiver_id = ? AND status = 'sent' "
        "AND acknowledged_at IS NULL "
        "ORDER BY sent_at DESC",
        (receiver_id,),
    )


# ---------------------------------------------------------------------------
# Pending batches (cooldown accumulation)
# ---------------------------------------------------------------------------

async def get_active_batch_for_sender(
    db_path: str, sender_id: int, receiver_id: int
) -> dict[str, Any] | None:
    """Get the currently accumulating batch for a sender→receiver pair."""
    return await _fetch_one(
        db_path,
        "SELECT * FROM relay_batch "
        "WHERE sender_id = ? AND receiver_id = ? AND status = 'accumulating'",
        (sender_id, receiver_id),
    )


async def create_or_get_active_batch(
    db_path: str, sender_id: int, receiver_id: int, send_after: str
) -> dict[str, Any]:
    """Return the active batch for this pair, creating one if none exists."""
    batch = await get_active_batch_for_sender(db_path, sender_id, receiver_id)
    if batch:
        return batch
    batch_id = await _execute(
        db_path,
        "INSERT INTO relay_batch (sender_id, receiver_id, send_after) "
        "VALUES (?, ?, ?)",
        (sender_id, receiver_id, send_after),
    )
    return await _fetch_one(  # type: ignore[return-value]
        db_path, "SELECT * FROM relay_batch WHERE id = ?", (batch_id,)
    )


async def add_item_to_batch(
    db_path: str, batch_id: int, draft: str
) -> int:
    """Add an item to a batch; return the item id."""
    return await _execute(
        db_path,
        "INSERT INTO relay_batch_items (batch_id, draft) VALUES (?, ?)",
        (batch_id, draft),
    )


async def get_batch(db_path: str, batch_id: int) -> dict[str, Any] | None:
    return await _fetch_one(
        db_path, "SELECT * FROM relay_batch WHERE id = ?", (batch_id,)
    )


async def get_batch_item(db_path: str, item_id: int) -> dict[str, Any] | None:
    return await _fetch_one(
        db_path, "SELECT * FROM relay_batch_items WHERE id = ?", (item_id,)
    )


async def get_batch_items(
    db_path: str, batch_id: int
) -> list[dict[str, Any]]:
    return await _fetch_all(
        db_path,
        "SELECT * FROM relay_batch_items WHERE batch_id = ? ORDER BY created_at",
        (batch_id,),
    )


async def update_batch_status(
    db_path: str, batch_id: int, status: str
) -> None:
    await _execute(
        db_path,
        "UPDATE relay_batch SET status = ? WHERE id = ?",
        (status, batch_id),
    )


async def update_batch_item_status(
    db_path: str,
    item_id: int,
    status: str,
    edited_draft: str | None = None,
) -> None:
    if edited_draft is not None:
        await _execute(
            db_path,
            "UPDATE relay_batch_items SET status = ?, edited_draft = ? WHERE id = ?",
            (status, edited_draft, item_id),
        )
    else:
        await _execute(
            db_path,
            "UPDATE relay_batch_items SET status = ? WHERE id = ?",
            (status, item_id),
        )


async def reset_batch_timer(
    db_path: str, batch_id: int, send_after: str
) -> None:
    """Reset the batch's send_after (cooldown timer)."""
    await _execute(
        db_path,
        "UPDATE relay_batch SET send_after = ? WHERE id = ?",
        (send_after, batch_id),
    )


async def get_deliverable_batches(db_path: str) -> list[dict[str, Any]]:
    """Get batches ready for delivery or review.

    Returns accumulating batches whose timer has elapsed, plus approved
    batches that are ready to send.
    """
    now = _now_iso()
    return await _fetch_all(
        db_path,
        "SELECT * FROM relay_batch WHERE "
        "(status = 'accumulating' AND send_after <= ?) OR status = 'approved'",
        (now,),
    )
