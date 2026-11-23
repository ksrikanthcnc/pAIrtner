"""Web Push notification support (VAPID + pywebpush).

Stores push subscriptions per user and delivers notifications via the Web Push
protocol (FCM for Chrome, APNs for Safari). Outbound HTTPS only — no inbound
connections needed. Works behind NAT.

VAPID keys are read from config (VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY env vars
or settings.yaml). Generate once with:
    python -m pairtner.push --generate-vapid
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

PUSH_SCHEMA = """\
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id),
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_id);
"""


async def ensure_push_table(db_path: str) -> None:
    """Create the push_subscriptions table if it doesn't exist."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(PUSH_SCHEMA)
        await db.commit()


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------

@dataclass
class PushSubscription:
    endpoint: str
    p256dh: str
    auth: str


async def save_subscription(
    db_path: str, user_id: int, sub: PushSubscription,
) -> None:
    """Upsert a push subscription for a user."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET p256dh=?, auth=?, user_id=?",
            (user_id, sub.endpoint, sub.p256dh, sub.auth,
             sub.p256dh, sub.auth, user_id),
        )
        await db.commit()


async def remove_subscription(db_path: str, endpoint: str) -> None:
    """Remove a subscription (e.g. when push fails with 410 Gone)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await db.commit()


async def get_subscriptions(db_path: str, user_id: int) -> list[PushSubscription]:
    """Get all push subscriptions for a user."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        )
        return [PushSubscription(r["endpoint"], r["p256dh"], r["auth"]) for r in rows]


# ---------------------------------------------------------------------------
# Send push notification
# ---------------------------------------------------------------------------

def _get_vapid_claims(contact: str = "") -> dict:
    """Build VAPID claims. contact should be a mailto: or https: URL."""
    return {"sub": contact or "mailto:pairtner@localhost"}


async def send_push(
    db_path: str,
    user_id: int,
    title: str,
    body: str,
    *,
    vapid_private_key: str,
    vapid_claims: dict | None = None,
) -> int:
    """Send a push notification to all of a user's subscriptions.

    Returns the number of successfully delivered pushes.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.warning("pywebpush not installed — push notifications disabled")
        return 0

    subs = await get_subscriptions(db_path, user_id)
    if not subs:
        return 0

    payload = json.dumps({"title": title, "body": body, "url": "/"})
    claims = vapid_claims or _get_vapid_claims()
    sent = 0

    for sub in subs:
        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=claims,
            )
            sent += 1
        except WebPushException as e:
            if "410" in str(e) or "404" in str(e):
                # Subscription expired or invalid — clean up
                logger.info("Push subscription expired, removing: %s", sub.endpoint[:50])
                await remove_subscription(db_path, sub.endpoint)
            else:
                logger.warning("Push failed for %s: %s", sub.endpoint[:50], e)
        except Exception:
            logger.exception("Unexpected push error")

    return sent


# ---------------------------------------------------------------------------
# CLI: generate VAPID keys
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if "--generate-vapid" in sys.argv:
        from py_vapid import Vapid
        import base64
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        v = Vapid()
        v.generate_keys()
        priv = v.private_pem().decode().strip()
        raw_pub = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode()
        print(f"VAPID_PRIVATE_KEY={priv}")
        print(f"VAPID_PUBLIC_KEY={pub}")
        print("\nAdd both to your .env file.")
    else:
        print("Usage: python -m pairtner.push --generate-vapid")
