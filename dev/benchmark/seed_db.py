"""Build a hypothetical scenario DB for model benchmarking.

Creates a SQLite DB with realistic 3-month conversation data between
a couple, with known facts that any model should be able to extract.

Usage:
    uv run python dev/benchmark/seed_db.py [--output dev/benchmark/bench.db]

The DB contains:
- 2 paired users (Alice id=101, Bob id=102)
- ~300 messages over 90 days (both sides)
- Known "ground truth" facts that extraction should find
- Some facts that should become core, some that should resolve
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aiosqlite
from pairtner.storage.schema import init_db
from pairtner import db as db_mod

ALICE_ID = 101
BOB_ID = 102

# Ground truth: facts any model MUST extract
GROUND_TRUTH = {
    "core": [
        "Alice is 32 years old",
        "Bob is 34 years old",
        "They have been married for 6 years",
        "They have a 5-year-old son named Max",
        "Alice works as a product manager",
        "Bob is a software engineer",
        "They live in San Francisco",
        "Alice's birthday is March 15",
        "Bob's mother's name is Helen",
    ],
    "working_should_resolve": [
        "Alice is stressed about a product launch deadline",
        "Bob had a conflict with his manager about remote work",
        "Max had a stomach bug last week",
        "They are planning a trip to Tahoe",
    ],
    "working_ongoing": [
        "Alice feels Bob works too many hours",
        "Bob feels Alice dismisses his work stress",
        "They disagree about screen time for Max",
        "Both want to move to a bigger apartment",
    ],
    "sensitive_private": [
        "Alice wishes Bob would exercise more but doesn't want to hurt his feelings",
        "Bob is secretly saving for a surprise anniversary trip",
        "Alice is worried about Bob's drinking on weekends",
    ],
}

# Conversation scenarios that contain the ground truth facts
CONVERSATIONS = [
    # Week 1: Introduction, basic info
    {
        "day": 1,
        "messages": [
            (ALICE_ID, "user", "Hi, I'm Alice. I'm 32, married to Bob for 6 years. We have a 5-year-old son Max."),
            (ALICE_ID, "assistant", "Welcome Alice! I'm glad you're here. Tell me about what's going on."),
            (ALICE_ID, "user", "We just moved to San Francisco for my product manager job. Bob's a software engineer."),
            (ALICE_ID, "assistant", "That's a big move! How are you both settling in?"),
        ],
    },
    {
        "day": 2,
        "messages": [
            (BOB_ID, "user", "Hey, I'm Bob. Alice told me about this. I'm 34, we've been together 8 years, married 6."),
            (BOB_ID, "assistant", "Welcome Bob! It's great that you're both willing to try this."),
            (BOB_ID, "user", "Yeah, things have been tense lately. My mom Helen keeps telling us we need help communicating."),
            (BOB_ID, "assistant", "It sounds like your family cares. What's been feeling tense?"),
        ],
    },
    # Week 2: Work stress
    {
        "day": 8,
        "messages": [
            (ALICE_ID, "user", "Ugh, I have this massive product launch deadline coming up. I'm so stressed."),
            (ALICE_ID, "assistant", "That sounds really pressuring. How is it affecting things at home?"),
            (ALICE_ID, "user", "Bob just doesn't get it. He works from home and I feel like he has it easier."),
            (ALICE_ID, "assistant", "It sounds like you're feeling like the pressure isn't equally understood."),
            (ALICE_ID, "user", "And honestly? I wish he'd work out more. But I can't say that, it'll hurt him."),
        ],
    },
    {
        "day": 10,
        "messages": [
            (BOB_ID, "user", "My manager is pushing back on our remote work setup. It's been a fight."),
            (BOB_ID, "assistant", "That must be frustrating, especially with all the changes already."),
            (BOB_ID, "user", "Alice doesn't take it seriously. She says at least I get to stay home."),
            (BOB_ID, "assistant", "It sounds like you feel your work stress isn't being validated."),
            (BOB_ID, "user", "Between you and me, I've been saving up for a surprise anniversary trip. Don't tell her."),
        ],
    },
    # Week 3: Parenting disagreements
    {
        "day": 15,
        "messages": [
            (ALICE_ID, "user", "Max had a stomach bug all week. I had to take off work three days."),
            (ALICE_ID, "assistant", "That's tough when you're already under deadline pressure."),
            (ALICE_ID, "user", "And Bob lets Max watch iPad for hours. We keep fighting about screen time."),
            (ALICE_ID, "assistant", "Screen time boundaries can be a common source of disagreement."),
        ],
    },
    {
        "day": 17,
        "messages": [
            (BOB_ID, "user", "Alice is being unreasonable about Max's iPad time. He's 5, all kids use tablets."),
            (BOB_ID, "assistant", "It sounds like you two have different comfort levels with screen time."),
            (BOB_ID, "user", "We both want a bigger place though. This apartment is way too small for three of us."),
        ],
    },
    # Week 4-5: Planning, some resolution
    {
        "day": 22,
        "messages": [
            (ALICE_ID, "user", "We're planning a trip to Tahoe next month. Trying to reconnect."),
            (ALICE_ID, "assistant", "That sounds wonderful! Sometimes a change of scenery helps."),
            (ALICE_ID, "user", "My birthday is March 15, hoping we can do something nice."),
        ],
    },
    {
        "day": 30,
        "messages": [
            (ALICE_ID, "user", "The product launch went well! Deadline stress is finally over."),
            (ALICE_ID, "assistant", "Congratulations! How does it feel to have that behind you?"),
            (ALICE_ID, "user", "So much better. But I noticed Bob's been drinking more on weekends. Should I say something?"),
        ],
    },
    {
        "day": 35,
        "messages": [
            (BOB_ID, "user", "The remote work thing got resolved. My manager backed down."),
            (BOB_ID, "assistant", "That's great news! How are things at home now?"),
            (BOB_ID, "user", "Better. The launch stress was the main issue. Max is healthy again too."),
        ],
    },
    # Week 8-12: Ongoing patterns
    {
        "day": 50,
        "messages": [
            (ALICE_ID, "user", "Bob worked until midnight again. He says he has to, but I feel like he's choosing work over us."),
            (ALICE_ID, "assistant", "That's a recurring feeling. Have you been able to talk about it?"),
        ],
    },
    {
        "day": 55,
        "messages": [
            (BOB_ID, "user", "Alice rolled her eyes when I mentioned a deployment issue. She thinks my work is easy."),
            (BOB_ID, "assistant", "Feeling dismissed about something important to you is painful."),
        ],
    },
    {
        "day": 70,
        "messages": [
            (ALICE_ID, "user", "We need more space. Max needs his own room. Both of us agree on that at least."),
        ],
    },
    {
        "day": 85,
        "messages": [
            (BOB_ID, "user", "Still haven't found a bigger apartment. Market is insane here."),
            (BOB_ID, "assistant", "SF housing can be really challenging. How's that affecting your mood?"),
            (BOB_ID, "user", "Honestly just makes everything else feel harder."),
        ],
    },
]


async def build_benchmark_db(output_path: str, scenario_days: int = 90) -> dict:
    """Build the benchmark DB and return ground truth metadata."""
    await init_db(output_path)

    # Create users and pair them
    await db_mod.create_user(output_path, ALICE_ID, "Alice")
    await db_mod.create_user(output_path, BOB_ID, "Bob")
    await db_mod.set_partner(output_path, ALICE_ID, BOB_ID)

    # Insert conversations with realistic timestamps
    base_date = datetime.now(timezone.utc) - timedelta(days=scenario_days)
    msg_count = 0
    for conv in CONVERSATIONS:
        day = conv["day"]
        ts = base_date + timedelta(days=day)
        for user_id, role, text in conv["messages"]:
            msg_id = await db_mod.log_message(output_path, user_id, role, text)
            # Backdate the message
            async with aiosqlite.connect(output_path) as db:
                await db.execute(
                    "UPDATE messages SET created_at = ? WHERE id = ?",
                    (ts.isoformat(), msg_id),
                )
                await db.commit()
            ts += timedelta(minutes=2)  # 2 min between messages
            msg_count += 1

    # Save ground truth as metadata
    gt = {
        "alice_id": ALICE_ID,
        "bob_id": BOB_ID,
        "message_count": msg_count,
        "expected_core_facts": len(GROUND_TRUTH["core"]),
        "expected_working_resolve": len(GROUND_TRUTH["working_should_resolve"]),
        "expected_working_ongoing": len(GROUND_TRUTH["working_ongoing"]),
        "expected_sensitive": len(GROUND_TRUTH["sensitive_private"]),
        "ground_truth": GROUND_TRUTH,
    }

    # Store ground truth in a separate table
    async with aiosqlite.connect(output_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS benchmark_meta "
            "(key TEXT PRIMARY KEY, value TEXT)",
        )
        await db.execute(
            "INSERT OR REPLACE INTO benchmark_meta (key, value) VALUES (?, ?)",
            ("ground_truth", json.dumps(gt)),
        )
        await db.commit()

    return gt


async def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build benchmark seed DB")
    ap.add_argument("--output", default="dev/benchmark/bench.db", help="DB path")
    ap.add_argument("--days", type=int, default=90, help="Scenario span in days (30=quick, 90=standard, 365=full)")
    ap.add_argument("--quick", action="store_true", help="Quick mode: 30 days, fewer messages")
    args = ap.parse_args()

    days = 30 if args.quick else args.days
    output = args.output

    import os
    if os.path.exists(output):
        os.remove(output)

    gt = await build_benchmark_db(output, scenario_days=days)
    print(f"Created benchmark DB: {output} ({days}-day scenario)")
    print(f"  Messages: {gt['message_count']}")
    print(f"  Expected core facts: {gt['expected_core_facts']}")
    print(f"  Expected working (should resolve): {gt['expected_working_resolve']}")
    print(f"  Expected working (ongoing): {gt['expected_working_ongoing']}")
    print(f"  Expected sensitive/private: {gt['expected_sensitive']}")
    print(f"\nGround truth saved in benchmark_meta table.")


if __name__ == "__main__":
    asyncio.run(main())
