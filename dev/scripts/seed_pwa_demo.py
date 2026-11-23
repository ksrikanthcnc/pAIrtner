"""Seed a throwaway DB with realistic data for PWA dashboard testing.

Uses the SAME init_db + migrate_to_v2 path as production, then populates.
No hand-rolled schema — if production works, this works.

Usage:
    uv run python dev/scripts/seed_pwa_demo.py [db_path]

Default path: dev/localtest/test.db
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

A, B = 1, 2
NOW = datetime.now()


def ts(days_ago: int = 0, hours: int = 10, minutes: int = 0) -> str:
    return (NOW - timedelta(days=days_ago, hours=-hours + 10, minutes=-minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


async def seed(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass

    from pairtner.storage.schema import init_db
    from pairtner.storage.migrate_v2 import migrate_to_v2, make_relationship_id
    from pairtner import db as db_mod

    await init_db(path)
    await migrate_to_v2(path)

    # Create users
    await db_mod.create_user(path, A, "Alice", "en")
    await db_mod.create_user(path, B, "Bob", "en")
    # NOTE: users are NOT pre-paired — use /start + /join in the UI
    # to go through the natural pairing flow.

    # Set passwords (for PWA login)
    await db_mod.set_password(path, A, "alice")
    await db_mod.set_password(path, B, "bob")

    # --- Messages ---
    messages = [
        (A, "user", "I've been feeling really overwhelmed with the kids lately.", 3, 9, 0),
        (A, "bot", "That sounds exhausting. What's been the hardest part?", 3, 9, 1),
        (A, "user", "Just doing everything alone while Bob works late every night.", 3, 9, 5),
        (A, "bot", "Carrying it all solo is draining. Have you told Bob how you're feeling?", 3, 9, 6),
        (B, "user", "Work deadline is killing me, haven't slept properly in days.", 3, 22, 0),
        (B, "bot", "Sleep deprivation on top of deadlines — that's brutal. When's the deadline?", 3, 22, 1),
        (B, "user", "Friday. Then things should calm down.", 3, 22, 5),
        (A, "user", "He brought home flowers today, out of nowhere.", 2, 18, 0),
        (A, "bot", "That's sweet! How did that make you feel?", 2, 18, 1),
        (A, "user", "Really good actually. Maybe he does notice.", 2, 18, 5),
        (B, "user", "Got Alice flowers. She seemed happy.", 2, 19, 0),
        (B, "bot", "Nice move! What made you think of it?", 2, 19, 1),
        (B, "user", "Just felt like I've been absent. Wanted to show I care.", 2, 19, 5),
        (A, "user", "We actually talked for an hour last night, like old times.", 1, 8, 0),
        (A, "bot", "An hour of real connection — that's worth celebrating.", 1, 8, 1),
        (A, "user", "Everything. Kids, work, us. He even asked about my pottery class.", 1, 8, 5),
        (B, "user", "Had a great talk with Alice. Feel closer to her.", 1, 9, 0),
        (B, "bot", "That sounds like a turning point. What worked?", 1, 9, 1),
        (B, "user", "Just actually listening, asking about her day.", 1, 9, 5),
        (A, "user", "Planning a date night for Saturday. Excited!", 0, 10, 0),
        (A, "bot", "Date night! What are you thinking?", 0, 10, 1),
        (B, "user", "Alice wants a date Saturday. Any restaurant ideas for Italian?", 0, 11, 0),
        (B, "bot", "A nice trattoria with good pasta and candlelight.", 0, 11, 1),
    ]

    import aiosqlite
    async with aiosqlite.connect(path) as db:
        # Get relationship_id
        cur = await db.execute("SELECT id FROM relationships WHERE user_a_id=? AND user_b_id=?", (A, B))
        row = await cur.fetchone()
        rid = row[0] if row else f"{A}_{B}"

        for uid, role, text, d, h, m in messages:
            await db.execute(
                "INSERT INTO messages(user_id, relationship_id, role, content, created_at) "
                "VALUES (?,?,?,?,?)",
                (uid, rid, role, text, ts(d, h, m)),
            )

        # --- Facts (using real add_fact path would need BotService, so direct insert) ---
        facts = [
            (A, "Feels overwhelmed handling kids solo", "shared", "working", 0.8, 3, "family"),
            (A, "Appreciated the surprise flowers from Bob", "shared", "working", 0.7, 2, "appreciation"),
            (A, "Had a meaningful hour-long conversation with Bob", "shared", "working", 0.6, 1, "communication"),
            (A, "Planning date night for Saturday", "shared", "working", 0.5, 0, "plans"),
            (A, "Married for 8 years, one child", "shared", "core", 0.9, 7, "identity"),
            (A, "Enjoys pottery class on Wednesdays", "shared", "core", 0.4, 7, "hobbies"),
            (A, "Wishes Bob would help more with bedtime routine", "private", "working", 0.7, 3, "family"),
            (B, "Stressed by work deadline, sleep deprived", "shared", "working", 0.9, 3, "work"),
            (B, "Bought flowers for Alice spontaneously", "shared", "working", 0.6, 2, "appreciation"),
            (B, "Feels closer to Alice after their talk", "shared", "working", 0.5, 1, "feelings"),
            (B, "Wants to plan a good Italian restaurant date", "shared", "working", 0.4, 0, "plans"),
            (B, "Software engineer, works late often", "shared", "core", 0.8, 7, "identity"),
            (B, "Realizes he has been emotionally absent", "private", "working", 0.7, 2, "feelings"),
        ]

        # Get actual column names from facts table
        cur = await db.execute("PRAGMA table_info(facts)")
        fact_cols = {r[1] for r in await cur.fetchall()}

        for owner, text, vis, kind, intensity, days_ago, tag in facts:
            cols = ["owner_id", "text", "visibility", "intensity", "created_at", "updated_at"]
            vals = [owner, text, vis, intensity, ts(days_ago, 10), ts(days_ago, 10)]
            if "kind" in fact_cols:
                cols.append("kind")
                vals.append(kind)
            if "relationship_id" in fact_cols:
                cols.append("relationship_id")
                vals.append(rid)
            if "status" in fact_cols:
                cols.append("status")
                vals.append("active")
            if "tag" in fact_cols:
                cols.append("tag")
                vals.append(tag)
            placeholders = ",".join("?" for _ in cols)
            await db.execute(
                f"INSERT INTO facts({','.join(cols)}) VALUES ({placeholders})", vals
            )

        # Timeline events
        if "timeline_events" in {r[0] for r in (await (await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")).fetchall())}:
            events = [
                ("conflict", "Alice frustrated about solo parenting", 3),
                ("nudge", "Bob bought flowers for Alice", 2),
                ("resolution", "Hour-long heart-to-heart conversation", 1),
                ("milestone", "Planning first date night in months", 0),
            ]
            for etype, desc, days_ago in events:
                await db.execute(
                    "INSERT INTO timeline_events(couple_id, relationship_id, event_type, "
                    "summary, created_at) VALUES (?,?,?,?,?)",
                    (f"{A}:{B}", rid, etype, desc, ts(days_ago, 12)),
                )

        # Interaction scores (for Gottman ratio and persona tuning)
        tables = {r[0] for r in (await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")).fetchall())}
        if "interaction_scores" in tables:
            for i in range(8):
                await db.execute(
                    "INSERT INTO interaction_scores(couple_id, user_id, interaction_type, source) "
                    "VALUES (?,?,?,?)",
                    (rid, A, "positive", "llm"),
                )
            for i in range(2):
                await db.execute(
                    "INSERT INTO interaction_scores(couple_id, user_id, interaction_type, source) "
                    "VALUES (?,?,?,?)",
                    (rid, A, "negative", "llm"),
                )

        # Nudge suggestions (so the couple view shows pending nudges)
        if "nudge_state" in tables:
            await db.execute(
                "INSERT INTO nudge_state(user_id, suggestion, nudge_type, status, created_at) "
                "VALUES (?,?,?,?,?)",
                (A, "Ask Bob about his work deadline — he mentioned it's been tough",
                 "partner_insight", "relay_queue", ts(0, 14)),
            )
            await db.execute(
                "INSERT INTO nudge_state(user_id, suggestion, nudge_type, status, created_at) "
                "VALUES (?,?,?,?,?)",
                (B, "Alice's pottery class is on Wednesdays — ask how it went",
                 "partner_insight", "relay_queue", ts(0, 14)),
            )

        await db.commit()

    print(f"Seeded: {path}")
    print(f"  Users: Alice (id=1, pwd=alice), Bob (id=2, pwd=bob)")
    print(f"  Messages: {len(messages)}, Facts: {len(facts)}")
    print(f"  Used: init_db + migrate_to_v2 (production path)")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "dev/localtest/test.db"
    asyncio.run(seed(path))
