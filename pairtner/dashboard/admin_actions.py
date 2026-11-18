"""Destructive admin actions: backup, clear one user, clear everything.

The only module in the dashboard that writes to the bot's database.

Safety rules, in order:

1. The typed confirmation is checked before anything is touched.
2. One ``BEGIN IMMEDIATE`` transaction takes the write lock (the bot's own
   writers wait on it rather than interleave).
3. A consistent backup is taken with SQLite's online backup API and verified
   with ``PRAGMA integrity_check``. If that fails nothing is deleted.
4. Rows are deleted in the same transaction, then committed.
5. A content-free audit line is appended to ``<backup_dir>/admin_audit.log``.

The DB file itself is never deleted or replaced (that would break a Docker
single-file bind mount); clearing means ``DELETE FROM``.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pairtner.storage.helpers import _now_iso

from pairtner.dashboard import store
from pairtner.dashboard.redaction import quote_ident
from pairtner.dashboard.store import NotFound

logger = logging.getLogger(__name__)

CLEAR_ALL_PHRASE = "CLEAR ALL DATA"
AUDIT_FILE = "admin_audit.log"

_COUPLE = "(couple_id LIKE :cid_a OR couple_id LIKE :cid_b)"

# (table, WHERE clause with :uid / :cid_a / :cid_b). Children before parents.
# Every user-keyed table in the schema must appear here or in GLOBAL_TABLES;
# a test introspects the real schema to enforce that.
USER_SCOPES: list[tuple[str, str]] = [
    ("pending_batch_items",
     "batch_id IN (SELECT id FROM pending_batch "
     "WHERE sender_id = :uid OR receiver_id = :uid)"),
    ("pending_batch", "sender_id = :uid OR receiver_id = :uid"),
    ("pending", "sender_id = :uid OR receiver_id = :uid"),
    ("messages", "user_id = :uid"),
    ("facts", "owner_id = :uid"),
    ("memory_versions",
     "fact_id IN (SELECT id FROM facts WHERE owner_id = :uid)"),
    ("memory_op_log", "user_id = :uid"),
    ("fact_summaries", "user_id = :uid"),
    ("user_profiles", "user_id = :uid"),
    ("user_mood_styles", "user_id = :uid"),
    ("partner_perceptions", "user_id = :uid OR about_id = :uid"),
    ("user_patterns", "user_id = :uid"),
    ("user_goals", "user_id = :uid"),
    ("interaction_scores", f"user_id = :uid OR {_COUPLE}"),
    ("shared_goals", f"created_by = :uid OR {_COUPLE}"),
    ("checkins", f"partner_a_id = :uid OR partner_b_id = :uid OR {_COUPLE}"),
    ("conflicts", _COUPLE),
    ("timeline_events", _COUPLE),
    ("couple_config", _COUPLE),
    ("rate_limit", "user_id = :uid"),
    ("auth_tokens", "user_id = :uid"),
    ("rest_outbox", "user_id = :uid"),
    ("user_meta", "user_id = :uid"),
    ("users", "tg_id = :uid"),
]
# Tables with no per-user key (aggregate counters).
GLOBAL_TABLES = {"usage", "llm_calls", "pack_snapshots"}


class ConfirmationError(ValueError):
    """The typed confirmation did not match."""


class BackupError(RuntimeError):
    """The pre-action backup failed; nothing was deleted."""


@dataclass
class ActionResult:
    action: str
    backup_path: str
    counts: dict[str, int] = field(default_factory=dict)


def user_confirmation_phrase(user_id: int) -> str:
    return f"CLEAR USER {user_id}"


def _params(user_id: int) -> dict[str, object]:
    return {"uid": user_id, "cid_a": f"{user_id}:%", "cid_b": f"%:{user_id}"}


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _make_backup(db_path: str, backup_dir: str, label: str) -> str:
    """Online-backup *db_path* into *backup_dir* and verify the copy."""
    try:
        os.makedirs(backup_dir, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"cannot create backup directory: {exc}") from exc
    dest = os.path.join(backup_dir, f"admin_{label}_{_timestamp()}.db")
    src = dst = None
    try:
        src = store.open_readonly_raw(db_path)
        dst = sqlite3.connect(dest)
        src.backup(dst)
        dst.commit()
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError(f"backup integrity check failed: {result}")
    except (sqlite3.Error, OSError) as exc:
        _discard(dest)
        raise BackupError(f"backup failed: {exc}") from exc
    except BackupError:
        _discard(dest)
        raise
    finally:
        for conn in (src, dst):
            if conn is not None:
                conn.close()
    return dest


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _audit(backup_dir: str, action: str, target: str, result: ActionResult) -> None:
    line = {
        "at": _now_iso(),
        "action": action,
        "target": target,
        "backup": os.path.basename(result.backup_path),
        "rows": result.counts,
    }
    try:
        with open(os.path.join(backup_dir, AUDIT_FILE), "a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError:
        logger.exception("could not write dashboard audit log")


def list_backups(backup_dir: str) -> list[dict[str, object]]:
    if not os.path.isdir(backup_dir):
        return []
    out = []
    for name in sorted(os.listdir(backup_dir), reverse=True):
        path = os.path.join(backup_dir, name)
        if name.endswith(".db") and os.path.isfile(path):
            st = os.stat(path)
            out.append({
                "name": name,
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S"),
            })
    return out


def backup_now(db_path: str, backup_dir: str) -> ActionResult:
    path = _make_backup(db_path, backup_dir, "manual")
    result = ActionResult("backup", path)
    _audit(backup_dir, "backup", "-", result)
    return result


# ---------------------------------------------------------------------------
# Previews (read-only)
# ---------------------------------------------------------------------------

def preview_clear_user(db_path: str, user_id: int) -> dict[str, int]:
    """Per-table row counts that clearing *user_id* would delete."""
    conn = store.open_readonly(db_path)
    try:
        counts = _count_user_rows(conn, user_id)
    finally:
        conn.close()
    if not any(counts.values()):
        raise NotFound(f"no data for user {user_id}")
    return counts


def _count_user_rows(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    params = _params(user_id)
    counts = {}
    for table, where in USER_SCOPES:
        n = conn.execute(
            f"SELECT COUNT(*) FROM {quote_ident(table)} WHERE {where}", params
        ).fetchone()[0]
        if n:
            counts[table] = n
    return counts


def preview_clear_all(db_path: str) -> dict[str, int]:
    conn = store.open_readonly(db_path)
    try:
        return {t: store.count_rows(conn, t) for t in store.list_tables(conn)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Destructive actions
# ---------------------------------------------------------------------------

def _require_confirmation(supplied: str, expected: str) -> None:
    if (supplied or "").strip() != expected:
        raise ConfirmationError(f"type exactly: {expected}")


def clear_user(
    db_path: str, backup_dir: str, user_id: int, confirmation: str
) -> ActionResult:
    _require_confirmation(confirmation, user_confirmation_phrase(user_id))
    conn = store.open_readwrite(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            counts = _count_user_rows(conn, user_id)
            if not counts:
                raise NotFound(f"no data for user {user_id}")
            backup_path = _make_backup(db_path, backup_dir, f"clear_user_{user_id}")
            params = _params(user_id)
            cur = conn.execute(
                "UPDATE users SET partner_id = NULL WHERE partner_id = :uid", params
            )
            if cur.rowcount:
                counts["users.partner_id_unset"] = cur.rowcount
            for table, where in USER_SCOPES:
                conn.execute(
                    f"DELETE FROM {quote_ident(table)} WHERE {where}", params
                )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    result = ActionResult("clear_user", backup_path, counts)
    _audit(backup_dir, "clear_user", str(user_id), result)
    return result


def clear_all(db_path: str, backup_dir: str, confirmation: str) -> ActionResult:
    _require_confirmation(confirmation, CLEAR_ALL_PHRASE)
    conn = store.open_readwrite(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            tables = _clear_order(conn)
            counts = {
                t: conn.execute(f"SELECT COUNT(*) FROM {quote_ident(t)}").fetchone()[0]
                for t in tables
            }
            backup_path = _make_backup(db_path, backup_dir, "clear_all")
            for table in tables:
                conn.execute(f"DELETE FROM {quote_ident(table)}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    result = ActionResult("clear_all", backup_path, counts)
    _audit(backup_dir, "clear_all", "all", result)
    return result


def _clear_order(conn: sqlite3.Connection) -> list[str]:
    """Known tables children-first, then any other visible table."""
    present = store.list_tables(conn)
    known = [t for t, _ in USER_SCOPES if t in present]
    rest = [t for t in present if t not in known]
    return known + rest
