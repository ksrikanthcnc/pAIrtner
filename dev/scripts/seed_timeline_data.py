"""Seed a v2 DB for eyeballing the admin timeline page.

Usage: uv run python dev/scripts/seed_timeline_data.py [/tmp/pairtner_timeline_test.db]
Alice=1001, Bob=1002 -> relationship id "1001_1002".
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from pairtner.storage.schema_v2 import SCHEMA_V2_SQL, make_relationship_id  # noqa: E402

A, B = 1001, 1002
BASE = datetime(2026, 11, 17, 8, 0, 0)


def ts(day: int, hh: int, mm: int = 0) -> str:
    return (BASE + timedelta(days=day, hours=hh - 8, minutes=mm)).strftime("%Y-%m-%d %H:%M:%S")


def main(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA_V2_SQL)
    rid = make_relationship_id(A, B)
    c.execute("INSERT INTO users(tg_id,name) VALUES (?,?),(?,?)", (A, "Alice", B, "Bob"))
    c.execute("INSERT INTO relationships(id,user_a_id,user_b_id) VALUES (?,?,?)", (rid, A, B))

    msgs = [
        (A, "user", "Bob forgot our dinner plans again.", 0, 9),
        (A, "assistant", "That sounds frustrating. Want to tell me more?", 0, 9, 1),
        (B, "user", "Work is exhausting this week.", 0, 19),
        (B, "assistant", "I'm sorry, long weeks wear you down. What's the heaviest part?", 0, 19, 1),
        (A, "user", "I'd love a weekend away together.", 1, 8),
        (A, "assistant", "A getaway could be lovely. Any place in mind?", 1, 8, 1),
        (B, "user", "I think Alice is upset with me but I don't know why.", 1, 12),
        (B, "assistant", "Would it help to ask her gently how her week has been?", 1, 12, 1),
        (A, "user", "Thanks for the flowers, that was sweet.", 1, 18),
        (A, "assistant", "I'm glad that landed well!", 1, 18, 1),
        (B, "user", "Dinner at the Italian place on Friday?", 2, 10),
        (B, "assistant", "Great idea. I'll note it.", 2, 10, 1),
        (A, "user", "Yes! Friday works.", 2, 14),
        (A, "assistant", "Wonderful, enjoy it.", 2, 14, 1),
        (B, "user", "Feeling much better about us today.", 2, 21),
    ]
    for m in msgs:
        uid, role, text, d, h = m[:5]
        mm = 1 if len(m) > 5 else 0
        c.execute("INSERT INTO messages(user_id,relationship_id,role,content,created_at) VALUES (?,?,?,?,?)",
                  (uid, rid, role, text, ts(d, h, mm)))

    facts = [
        (A, "Alice feels unheard when plans are forgotten.", "private", 0.8, 0, 9),
        (A, "Alice loves weekend getaways.", "shared", 0.6, 1, 8),
        (B, "Bob is stressed by work deadlines.", "private", 0.9, 0, 19),
        (B, "Bob worries Alice is upset with him.", "shared", 0.5, 1, 12),
        (A, "Alice appreciated the flowers.", "shared", 0.7, 1, 18),
        (B, "Bob wants a Friday Italian dinner.", "shared", 0.4, 2, 10),
        (B, "Bob feels closer to Alice lately.", "shared", 0.3, 2, 21),
    ]
    for owner, text, vis, inten, d, h in facts:
        c.execute("INSERT INTO facts(owner_id,relationship_id,text,visibility,kind,intensity,created_at,updated_at) "
                  "VALUES (?,?,?,?, 'working',?,?,?)", (owner, rid, text, vis, inten, ts(d, h, 2), ts(d, h, 2)))

    nudges = [
        (A, B, "Plan something special for Alice this weekend.", "quality_time", "relay_queue", 1, 8, None, None),
        (B, A, "Check in with Bob about his workload.", "support", "delivered", 0, 19, ts(1, 9), None),
        (A, B, "Remember the dinner plans you made.", "attention", "expired", 0, 10, None, None),
    ]
    for owner, tgt, sug, typ, status, d, h, deliv, canc in nudges:
        c.execute("INSERT INTO facts(owner_id,relationship_id,text,visibility,kind,nudge_target_id,nudge_type,"
                  "nudge_suggestion,nudge_status,nudge_delivered_at,nudge_cancelled_at,created_at,updated_at) "
                  "VALUES (?,?,?,'shared','working',?,?,?,?,?,?,?,?)",
                  (owner, rid, f"nudge: {sug}", tgt, typ, sug, status, deliv, canc, ts(d, h, 5), ts(d, h, 5)))

    narrative = ("A mixed few days. Alice felt let down about forgotten plans while Bob carried work stress.\n"
                 "They ended on a warmer note with a planned Friday dinner and a sweet gesture of flowers.")
    c.execute("INSERT INTO facts(owner_id,relationship_id,text,visibility,kind,metadata,created_at,updated_at) "
              "VALUES (NULL,?,?,'shared','weekly',?,?,?)",
              (rid, narrative, json.dumps({"narrative": narrative}), ts(2, 23), ts(2, 23)))

    c.execute("INSERT INTO relationship_config(relationship_id,config_key,config_value,created_at,updated_at) "
              "VALUES (?,?,?,?,?)", (rid, "nudge_frequency", "daily", ts(0, 8), ts(0, 8)))
    c.commit()
    c.close()
    print(f"seeded {path} (relationship {rid})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pairtner_timeline_test.db")
