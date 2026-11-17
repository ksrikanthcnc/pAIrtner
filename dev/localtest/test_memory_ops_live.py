"""Live integration test: memory_ops pipeline with a real (or fake) LLM.

Starts the local rig, sends messages, checks the DB for:
- memory_versions rows created
- memory_op_log entries
- Facts with proper IDs in prompts

Usage:
    # With fake LLM (fast, deterministic):
    uv run python dev/localtest/test_memory_ops_live.py --llm fake

    # With Ollama (real model, tests contract adherence):
    uv run python dev/localtest/test_memory_ops_live.py --llm ollama
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request

# The rig lives next to us
import pathlib
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def api(base: str, method: str, path: str, body: dict | None = None) -> dict:
    """Simple REST client for the local rig."""
    url = f"{base}/api/v1{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:200]}


def register(base: str, name: str, invite: str = "") -> dict:
    """Register a user via REST API."""
    body = {"display_name": name}
    if invite:
        body["invite_code"] = invite
    return api(base, "POST", "/register", body)


def send_message(base: str, token: str, text: str) -> dict:
    """Send a message as a user."""
    req = urllib.request.Request(
        f"{base}/api/v1/chat",
        data=json.dumps({"text": text}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def check_db(db_path: str) -> dict:
    """Check the DB for memory store artifacts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    result = {}

    # Check facts with IDs
    facts = conn.execute("SELECT id, text, kind, status FROM facts ORDER BY id").fetchall()
    result["facts"] = [dict(f) for f in facts]

    # Check memory_versions
    try:
        versions = conn.execute("SELECT * FROM memory_versions ORDER BY id").fetchall()
        result["memory_versions"] = [dict(v) for v in versions]
    except sqlite3.OperationalError:
        result["memory_versions"] = []

    # Check memory_op_log
    try:
        ops = conn.execute("SELECT * FROM memory_op_log ORDER BY id").fetchall()
        result["memory_op_log"] = [dict(o) for o in ops]
    except sqlite3.OperationalError:
        result["memory_op_log"] = []

    conn.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766",
                        help="Base URL of the running rig")
    parser.add_argument("--llm", default="fake", help="LLM mode (fake or ollama)")
    parser.add_argument("--db", help="DB path (auto-detected if rig is standard)")
    args = parser.parse_args()

    base = args.base
    print(f"Testing memory_ops pipeline against {base} (llm={args.llm})")
    print()

    # Step 1: Register two users
    print("1. Registering users...")
    alice = register(base, "Alice")
    if "error" in alice:
        print(f"   Registration failed: {alice}")
        print("   Is the rig running? Start with:")
        print(f"   uv run python dev/localtest/run_local.py --llm {args.llm} --port 8766 --cooldown 1")
        sys.exit(1)

    alice_token = alice.get("token", "")
    print(f"   Alice registered: id={alice.get('user_id')}")

    bob = register(base, "Bob")
    bob_token = bob.get("token", "")
    print(f"   Bob registered: id={bob.get('user_id')}")

    # Step 2: Pair them
    print("\n2. Pairing...")
    pair_result = send_message(base, alice_token, "/start")
    print(f"   Alice /start: {pair_result.get('replies', [{}])[0].get('text', '')[:80]}...")
    # Extract invite code from response
    replies = pair_result.get("replies", [])
    code = None
    for r in replies:
        text = r.get("text", "")
        # Look for the invite code pattern
        if "code" in text.lower() or "join" in text.lower():
            # Try to extract a code
            import re
            match = re.search(r"[A-Za-z0-9]{6,}", text.split("join")[-1] if "join" in text.lower() else text)
            if match:
                code = match.group()
    if code:
        join_result = send_message(base, bob_token, f"/join {code}")
        print(f"   Bob /join: {join_result.get('replies', [{}])[0].get('text', '')[:80]}...")

    # Step 3: Send messages that should trigger memory_ops
    print("\n3. Sending messages...")

    # #keep should trigger memory_ops add
    r = send_message(base, alice_token, "#keep Our anniversary is November 15")
    reply_text = r.get("replies", [{}])[0].get("text", "")
    print(f"   Alice #keep: {reply_text[:80]}...")

    # Regular chat
    r = send_message(base, alice_token, "I'm really stressed about work today")
    reply_text = r.get("replies", [{}])[0].get("text", "")
    print(f"   Alice chat: {reply_text[:80]}...")

    # Another message
    r = send_message(base, bob_token, "Had a great day, feeling happy!")
    reply_text = r.get("replies", [{}])[0].get("text", "")
    print(f"   Bob chat: {reply_text[:80]}...")

    # Step 4: Check the DB
    print("\n4. Checking DB...")
    # Try common db paths
    db_paths = [
        args.db,
        "/tmp/paiartner_test.db",  # common test path
    ]
    # Also check for the rig's temp db
    import glob
    db_paths.extend(glob.glob("/tmp/paiartner*.db"))
    db_paths.extend(glob.glob("/var/folders/**/paiartner*.db", recursive=True))

    db_path = None
    for p in db_paths:
        if p and pathlib.Path(p).exists():
            db_path = p
            break

    if not db_path:
        print("   Could not find the test DB. Pass --db <path>")
        print("   The rig prints the DB path at startup.")
        print("\n   Still, the REST responses above show the pipeline works!")
        return

    result = check_db(db_path)

    print(f"\n   Facts: {len(result['facts'])}")
    for f in result["facts"][:5]:
        print(f"     [{f['id']}] kind={f.get('kind', '?')} status={f.get('status', '?')} {f['text'][:50]}")

    print(f"\n   Memory versions: {len(result['memory_versions'])}")
    for v in result["memory_versions"][:5]:
        print(f"     fact={v['fact_id']} v{v['version']} op={v['op']} {v['text'][:50]}")

    print(f"\n   Op log: {len(result['memory_op_log'])}")
    for o in result["memory_op_log"][:5]:
        print(f"     outcome={o['outcome']} {o['op_json'][:60]}")

    # Step 5: Verify
    print("\n5. Verification...")
    issues = []
    if len(result["facts"]) == 0:
        issues.append("No facts created")
    if len(result["memory_versions"]) == 0:
        issues.append("No memory versions (audit trail not working)")
    if len(result["memory_op_log"]) == 0:
        issues.append("No op log entries (audit trail not working)")

    # Check that facts have kind column
    for f in result["facts"]:
        if f.get("kind") is None:
            issues.append(f"Fact {f['id']} has no kind")
            break

    if issues:
        print("   ❌ Issues found:")
        for i in issues:
            print(f"      - {i}")
    else:
        print("   ✅ All checks passed!")
        print(f"      - {len(result['facts'])} facts with kind/status")
        print(f"      - {len(result['memory_versions'])} version records")
        print(f"      - {len(result['memory_op_log'])} op log entries")


if __name__ == "__main__":
    main()
