"""Seeded database for dashboard tests.

Every raw-text value contains ``CANARY-RAW`` and every secret contains
``CANARY-SECRET`` so tests can assert exactly what may appear in pages.
"""
from __future__ import annotations

import asyncio
import sqlite3

from pairtner.db import (
    add_fact, add_shared_goal, add_timeline_event, create_auth_token,
    create_conflict, create_pending, create_user, increment_usage, init_db,
    log_message, make_couple_id, set_couple_config, set_partner,
)

AUTH_CANARY = "CANARY-SECRET-AUTH-TOKEN"
KEY_CANARY = "gAAAA-CANARY-SECRET-ENCRYPTED-KEY"
RAW = "CANARY-RAW"
DASH_TOKEN = "dash-token-0123456789abcdef-XYZ"


async def _seed(db_path: str) -> None:
    await init_db(db_path)
    for uid in (1, 2, 3, 4, 5):
        await create_user(db_path, uid, f"{RAW}-name-{uid}")
    await set_partner(db_path, 1, 2)
    await set_partner(db_path, 3, 4)
    await log_message(db_path, 1, "user", f"{RAW}-msg-one")
    await log_message(db_path, 1, "assistant", f"{RAW}-msg-reply")
    await log_message(db_path, 2, "user", f"{RAW}-msg-two")
    await log_message(db_path, 3, "user", f"{RAW}-msg-three")
    await add_fact(db_path, 1, f"{RAW}-fact-one", "private")
    await add_fact(db_path, 2, f"{RAW}-fact-two", "shared")
    await add_fact(db_path, 3, f"{RAW}-fact-three", "private")
    await create_pending(db_path, 1, 2, f"{RAW}-draft-12")
    await create_pending(db_path, 3, 4, f"{RAW}-draft-34")
    await increment_usage(db_path, "model-a")
    await increment_usage(db_path, "model-a")
    await increment_usage(db_path, "model-b")
    c12, c34 = make_couple_id(1, 2), make_couple_id(3, 4)
    await set_couple_config(db_path, c12, "api_key", KEY_CANARY)
    await set_couple_config(db_path, c34, "api_key", KEY_CANARY + "-34")
    await create_auth_token(db_path, 1, AUTH_CANARY)
    await create_auth_token(db_path, 3, AUTH_CANARY + "-3")
    await create_conflict(db_path, c12, f"{RAW}-conflict-theme")
    await create_conflict(db_path, c34, f"{RAW}-conflict-theme-34")
    await add_timeline_event(db_path, c12, "milestone", f"{RAW}-timeline-12")
    await add_timeline_event(db_path, c34, "milestone", f"{RAW}-timeline-34")
    await add_shared_goal(db_path, c12, 1, f"{RAW}-shared-goal-12")

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO pending_batch (id, sender_id, receiver_id) VALUES (1, 1, 2)")
    conn.execute("INSERT INTO pending_batch (id, sender_id, receiver_id) VALUES (2, 3, 4)")
    conn.execute("INSERT INTO pending_batch_items (batch_id, draft) VALUES (1, ?)", (f"{RAW}-item-1",))
    conn.execute("INSERT INTO pending_batch_items (batch_id, draft) VALUES (2, ?)", (f"{RAW}-item-2",))
    conn.execute("INSERT INTO user_goals (user_id, text) VALUES (1, ?)", (f"{RAW}-goal-1",))
    conn.execute("INSERT INTO user_goals (user_id, text) VALUES (3, ?)", (f"{RAW}-goal-3",))
    conn.execute(
        "INSERT INTO checkins (couple_id, week_start, partner_a_id, partner_a_answers, "
        "partner_b_id, summary) VALUES (?, '2026-01-05', 1, ?, 2, ?)",
        (c12, f"{RAW}-answers", f"{RAW}-summary"),
    )
    conn.execute("INSERT INTO rate_limit (user_id, window, count, reset_at) VALUES (1, 'min', 1, '2999-01-01')")
    conn.execute("INSERT INTO user_profiles (user_id, relationship_type) VALUES (1, ?)", (f"{RAW}-married",))
    conn.execute("INSERT INTO user_mood_styles (user_id, mood) VALUES (1, ?)", (f"{RAW}-mood",))
    conn.execute("INSERT INTO partner_perceptions (user_id, about_id, theme) VALUES (1, 2, ?)", (f"{RAW}-perc",))
    conn.execute("INSERT INTO partner_perceptions (user_id, about_id, theme) VALUES (3, 4, ?)", (f"{RAW}-perc34",))
    conn.execute("INSERT INTO user_patterns (user_id, pattern) VALUES (1, ?)", (f"{RAW}-pattern",))
    conn.execute("INSERT INTO interaction_scores (couple_id, user_id, interaction_type) VALUES (?, 1, 'positive')", (c12,))
    conn.execute("INSERT INTO fact_summaries (user_id, year_month, summary_text) VALUES (1, '2026-01', ?)", (f"{RAW}-summ",))
    conn.commit()
    conn.close()


def seed_db(db_path: str) -> str:
    asyncio.run(_seed(db_path))
    return db_path
