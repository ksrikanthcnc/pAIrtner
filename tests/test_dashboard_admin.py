"""Admin actions: confirmation, backup-first, scope, atomicity."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time

import pytest

from pairtner.dashboard import admin_actions as aa
from pairtner.dashboard import store
from pairtner.db import init_db
from tests.dashboard_seed import AUTH_CANARY, seed_db


@pytest.fixture
def env(tmp_path):
    db = seed_db(str(tmp_path / "dash.db"))
    return db, str(tmp_path / "backups")


def counts(db_path: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'facts_fts%'")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def backups(backup_dir: str) -> list[str]:
    if not os.path.isdir(backup_dir):
        return []
    return sorted(f for f in os.listdir(backup_dir) if f.endswith(".db"))


class TestBackup:
    def test_backup_now_creates_valid_copy(self, env):
        db, bdir = env
        res = aa.backup_now(db, bdir)
        assert os.path.basename(res.backup_path).startswith("admin_manual_")
        assert counts(res.backup_path) == counts(db)
        assert backups(bdir) == [os.path.basename(res.backup_path)]

    def test_admin_backups_not_rotated_by_bot(self, env):
        from pairtner.backup import _list_backup_files
        db, bdir = env
        aa.backup_now(db, bdir)
        assert _list_backup_files(bdir) == []

    def test_list_backups(self, env):
        db, bdir = env
        assert aa.list_backups(bdir) == []
        aa.backup_now(db, bdir)
        assert len(aa.list_backups(bdir)) == 1


class TestConfirmation:
    @pytest.mark.parametrize("phrase", ["", "clear user 1", "CLEAR USER 2", "yes", "CLEAR ALL DATA"])
    def test_clear_user_refuses(self, env, phrase):
        db, bdir = env
        before = counts(db)
        with pytest.raises(aa.ConfirmationError):
            aa.clear_user(db, bdir, 1, phrase)
        assert counts(db) == before
        assert backups(bdir) == []

    @pytest.mark.parametrize("phrase", ["", "clear all data", "CLEAR USER 1", "CLEAR ALL"])
    def test_clear_all_refuses(self, env, phrase):
        db, bdir = env
        before = counts(db)
        with pytest.raises(aa.ConfirmationError):
            aa.clear_all(db, bdir, phrase)
        assert counts(db) == before
        assert backups(bdir) == []


class TestClearUser:
    def test_backup_then_clear_only_that_user(self, env):
        db, bdir = env
        before = counts(db)
        res = aa.clear_user(db, bdir, 1, "CLEAR USER 1")
        # backup is a full, openable copy taken before deletion
        assert counts(res.backup_path) == before
        conn = sqlite3.connect(db)
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        for table, col in [("messages", "user_id"), ("facts", "owner_id"),
                           ("user_goals", "user_id"), ("auth_tokens", "user_id"),
                           ("user_profiles", "user_id"), ("rate_limit", "user_id"),
                           ("user_mood_styles", "user_id"), ("user_patterns", "user_id"),
                           ("fact_summaries", "user_id")]:
            assert q(f"SELECT COUNT(*) FROM {table} WHERE {col}=1") == 0, table
        assert q("SELECT COUNT(*) FROM users WHERE tg_id=1") == 0
        assert q("SELECT COUNT(*) FROM pending WHERE sender_id=1 OR receiver_id=1") == 0
        assert q("SELECT COUNT(*) FROM pending_batch WHERE sender_id=1 OR receiver_id=1") == 0
        assert q("SELECT COUNT(*) FROM pending_batch_items WHERE batch_id=1") == 0
        assert q("SELECT COUNT(*) FROM partner_perceptions WHERE user_id=1 OR about_id=1") == 0
        assert q("SELECT COUNT(*) FROM couple_config WHERE couple_id='1:2'") == 0
        assert q("SELECT COUNT(*) FROM conflicts WHERE couple_id='1:2'") == 0
        assert q("SELECT COUNT(*) FROM timeline_events WHERE couple_id='1:2'") == 0
        assert q("SELECT COUNT(*) FROM shared_goals WHERE couple_id='1:2'") == 0
        assert q("SELECT COUNT(*) FROM checkins WHERE couple_id='1:2'") == 0
        assert q("SELECT COUNT(*) FROM interaction_scores WHERE couple_id='1:2'") == 0
        # partner survives, unpaired, with their own rows
        assert q("SELECT partner_id FROM users WHERE tg_id=2") is None
        assert q("SELECT COUNT(*) FROM messages WHERE user_id=2") == 1
        assert q("SELECT COUNT(*) FROM facts WHERE owner_id=2") == 1
        # unrelated couple untouched
        for sql in ("SELECT COUNT(*) FROM users WHERE tg_id IN (3,4,5)",
                    "SELECT COUNT(*) FROM couple_config WHERE couple_id='3:4'",
                    "SELECT COUNT(*) FROM conflicts WHERE couple_id='3:4'",
                    "SELECT COUNT(*) FROM pending_batch_items WHERE batch_id=2",
                    "SELECT COUNT(*) FROM auth_tokens WHERE user_id=3"):
            assert q(sql) >= 1, sql
        assert q("SELECT COUNT(*) FROM usage") == 2
        # FTS index stays consistent with facts
        assert q("SELECT COUNT(*) FROM facts_fts WHERE facts_fts MATCH 'one'") == 0
        conn.close()

    def test_preview_matches_deleted_rows(self, env):
        db, bdir = env
        preview = aa.preview_clear_user(db, 1)
        res = aa.clear_user(db, bdir, 1, "CLEAR USER 1")
        for table, n in preview.items():
            assert res.counts[table] == n
        assert res.counts["users.partner_id_unset"] == 1

    def test_unknown_user_not_found_no_backup(self, env):
        db, bdir = env
        with pytest.raises(store.NotFound):
            aa.clear_user(db, bdir, 9999, "CLEAR USER 9999")
        assert backups(bdir) == []
        with pytest.raises(store.NotFound):
            aa.preview_clear_user(db, 9999)

    def test_backup_failure_aborts_with_db_intact(self, env, tmp_path):
        db, _ = env
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("file in the way")
        before = counts(db)
        with pytest.raises(aa.BackupError):
            aa.clear_user(db, str(blocker), 1, "CLEAR USER 1")
        with pytest.raises(aa.BackupError):
            aa.clear_all(db, str(blocker), "CLEAR ALL DATA")
        assert counts(db) == before

    def test_integrity_failure_aborts(self, env, monkeypatch):
        db, bdir = env
        monkeypatch.setattr(aa, "_make_backup",
                            lambda *a, **k: (_ for _ in ()).throw(aa.BackupError("bad copy")))
        before = counts(db)
        with pytest.raises(aa.BackupError):
            aa.clear_user(db, bdir, 1, "CLEAR USER 1")
        assert counts(db) == before


class TestClearAll:
    def test_clears_everything_keeps_schema_and_db_file(self, env):
        db, bdir = env
        before = counts(db)
        inode = os.stat(db).st_ino
        res = aa.clear_all(db, bdir, "CLEAR ALL DATA")
        assert counts(res.backup_path) == before
        after = counts(db)
        assert set(after) == set(before) and all(v == 0 for v in after.values())
        assert os.stat(db).st_ino == inode
        asyncio.run(init_db(db))  # schema intact, bot can reopen it
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM facts_fts").fetchone()[0] == 0
        conn.close()

    def test_preview_all(self, env):
        db, _ = env
        assert aa.preview_clear_all(db)["users"] == 5


class TestAudit:
    def test_audit_has_no_content(self, env):
        db, bdir = env
        aa.backup_now(db, bdir)
        aa.clear_user(db, bdir, 1, "CLEAR USER 1")
        aa.clear_all(db, bdir, "CLEAR ALL DATA")
        text = open(os.path.join(bdir, aa.AUDIT_FILE)).read()
        lines = [json.loads(line) for line in text.splitlines()]
        assert [x["action"] for x in lines] == ["backup", "clear_user", "clear_all"]
        assert "CANARY" not in text and AUTH_CANARY not in text
        assert lines[1]["target"] == "1" and lines[1]["backup"].endswith(".db")


class TestConcurrency:
    def test_waits_for_other_writer_then_succeeds(self, env):
        db, bdir = env
        writer = sqlite3.connect(db, isolation_level=None, check_same_thread=False)
        writer.execute("BEGIN IMMEDIATE")

        def release():
            time.sleep(0.6)
            writer.execute("COMMIT")

        t = threading.Thread(target=release)
        t.start()
        start = time.monotonic()
        aa.clear_user(db, bdir, 1, "CLEAR USER 1")
        t.join()
        writer.close()
        assert time.monotonic() - start >= 0.5
        assert counts(db)["users"] == 4


class TestSchemaCoverage:
    def test_every_user_keyed_table_has_a_scope(self, env):
        """Fails if a new table with an id/user column is not handled by clear-user."""
        db, _ = env
        conn = sqlite3.connect(db)
        scoped = {t for t, _ in aa.USER_SCOPES}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'facts_fts%'"
        ).fetchall():
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            user_keyed = any(c in ("user_id", "couple_id", "tg_id", "owner_id", "sender_id",
                                   "created_by") or c.endswith("_id") for c in cols)
            if name in aa.GLOBAL_TABLES:
                assert name not in scoped
            elif user_keyed:
                assert name in scoped, f"{name} has user/couple columns but no clear scope"
            else:
                assert name in scoped or name in aa.GLOBAL_TABLES, f"{name} unclassified"
        conn.close()
