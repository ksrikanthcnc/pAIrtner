# pAIrtner REST API

Self-contained reference for scripts and AI agents. It describes everything needed to register users, pair them, send messages, press buttons and receive relayed messages over plain HTTP + JSON. The same API backs the installable web client (PWA) that the server serves at `/`.

- Version: `v1` (all API routes live under `/api/v1`).
- Format: UTF-8 JSON. No third-party client library is needed. `curl` or Python's `urllib` is enough.
- Source of truth: `pairtner/rest/api.py` (`ROUTES`). A test fails if a route is missing from this file.

## 1. Run a server

```bash
# REST only (self-hosted, no Telegram)
ADAPTER=rest REST_REGISTRATION=open uv run python -m pairtner
# or: uv run python -m pairtner --adapter rest
# Telegram and REST together
uv run python -m pairtner --adapter telegram+rest
# Zero-secret local rig (fake LLM that upper-cases input, throwaway DB)
uv run python dev/localtest/run_local.py --llm fake        # http://127.0.0.1:8766
```

Default listen address is `127.0.0.1:8780` (`REST_HOST`, `REST_PORT`). The rig uses port 8766.

## 2. Conventions

| Item | Value |
|---|---|
| Base URL | `http://<host>:<port>` (examples use `$BASE`) |
| Auth header | `Authorization: Bearer <user_id>:<secret>` (the `token` returned by `/register`, verbatim) |
| Request body | JSON object, `Content-Type: application/json` required on every POST except `/api/v1/token/rotate` |
| Body limit | 16 KiB (`413 body_too_large`) |
| Text limit | `text` up to 4000 characters (`REST_MAX_TEXT_CHARS`) |
| Caching | `/api/*` responses carry `Cache-Control: no-store` |
| CORS | None. The API is same-origin only; a script is not affected |
| Errors | `{"error": "<code>"}` with the HTTP status from section 6 |

### Msg shape

Every bot message (inline reply or event) is a `Msg`:

```json
{
  "text": "📝 Here's the softened draft for your partner:\n\nYOU LEFT THE DISHES AGAIN",
  "markup": "📝 Here's the softened draft for your partner:\n\nYOU LEFT THE DISHES AGAIN",
  "buttons": [[{"text": "✅ Approve", "data": "ap:2"}, {"text": "✏️ Edit", "data": "ed:2"}, {"text": "❌ Discard", "data": "di:2"}]],
  "document": null
}
```

- `text`: plain text (tags stripped, entities decoded). **Agents should read this field.**
- `markup`: the same text with `< > &` escaped as entities, except the whitelisted tags `b i u code pre`. Clients that render rich text use this field; never insert it as raw HTML.
- `buttons`: rows of `{text, data}`. To "press" a button, send its `data` to `POST /api/v1/callback`. Empty list when there are none.
- `document`: `null` or `{"filename": "...", "content": "..."}` (for `/export`).

## 3. Routes

### `GET /healthz` (no auth)

`200` with the body `ok` (text/plain).

### `GET /api/v1/meta` (no auth)

Non-sensitive server description. Show `privacy` to a person before sign-up.

```json
{"name": "pAIrtner", "transport": "rest", "registration": "open",
 "llm": "a local model (fake-upper) on your own network",
 "languages": ["en", "te"],
 "privacy": "🔒 <b>Privacy note:</b> Messages pass through this server over HTTP ..."}
```

`registration` is `open`, `invite` or `closed` (section 4).

### `POST /api/v1/register` (no auth, throttled)

Request: `{"name": "Asha", "invite": "<code>", "lang": "en", "user_id": 4503599627370497}`

| Field | Required | Notes |
|---|---|---|
| `name` | yes | 1-40 chars after stripping control characters and `< > &`. Shown to the partner. Not unique |
| `invite` | in `invite` mode | Must equal the server's `REST_INVITE_CODE` |
| `lang` | no | `en` (default) or `te` |
| `user_id` | only when the server has an allowlist | Integer `>= 4503599627370496` that the admin put in `ALLOWED_USER_IDS`. Ignored otherwise |

Response `201`:

```json
{"user_id": 4503599627370498, "name": "Asha",
 "token": "4503599627370498:cJamwmeLgu5na3MThTjpixcfJSd8Zz1pNLZKOe5RsWA"}
```

The `token` is shown once. Store it and send it as the bearer credential.

### `GET /api/v1/me`

```json
{"user_id": 4503599627370498, "name": "Asha", "partner_id": null,
 "partner_name": null, "lang": "en", "paused": false}
```

`partner_id` and `partner_name` are `null` until `/join` completes.

### `POST /api/v1/message`

Request: `{"text": "<anything a user could type>"}`. Runs the same dispatcher as the chat apps, so every slash command and `#tag` works: `/start`, `/join CODE`, `/skip`, `/help`, `/status`, `/mediate [window]`, `/whatdoyouknow`, `#send ...`, `#keep ...` and plain chat text.

Response `200`: `{"replies": [Msg, ...]}`. Replies addressed to the caller come back here. Messages for the partner (for example "your partner joined") are not in `replies`; they go to the partner's `/events`.

Calls from one user are processed one at a time. The call can take as long as the LLM takes (several seconds with a real model), so use a client timeout of at least 120 s.

### `POST /api/v1/callback`

Request: `{"data": "ap:2"}` (a button's `data`, up to 256 chars). Response is the same as `/message`. Callback formats seen in practice:

| `data` | Meaning |
|---|---|
| `ap:<id>` | Approve draft `<id>` (delivery after the cool-down) |
| `apn:<id>` | Approve and deliver now (offered after `#now`) |
| `ed:<id>` / `di:<id>` | Edit / discard draft `<id>` |
| `ba:<batch>` / `br:<batch>` / `bd:<batch>` | After the cool-down: send all / review each / discard all |
| `fv:<fact_id>` | Toggle a fact between private and shared (owner only). The reply states the new state |

Always take `data` from a received button instead of constructing it, except `fv:<id>` where the id comes from `/whatdoyouknow`. A button that does not belong to the caller (or an id that does not exist) is refused by the bot and changes nothing.

### `GET /api/v1/events?after=N&wait=S`

Messages that arrive for you without a request of yours: partner joined, relayed partner messages, review prompts, digests, check-ins and nudges.

Response `200`:

```json
{"events": [{"id": 7, "type": "message", "msg": {"text": "...", "markup": "...", "buttons": [], "document": null},
             "created_at": "2026-06-15 10:42:07"}],
 "cursor": 7}
```

Cursor and long-poll rules:

- `after` is the id of the last event you processed (default `0`). Only events with a larger id are returned, oldest first, up to 100 per call.
- `cursor` is the id of the last event returned, or the `after` you sent when there were none. Send it as `after` next time.
- Events are **not deleted on read**. Repeating a call with the same `after` returns the same events, so a retry after a network failure is safe. A first call with no `after` replays everything still retained (`REST_EVENT_RETENTION_DAYS`, default 14).
- `wait` is seconds to hold the request open when there is nothing new (default `0` = answer immediately). It is clamped to `REST_LONG_POLL_MAX_SECONDS` (default 25). A waiting call returns as soon as an event is written. After a timeout the answer is `{"events": [], "cursor": <after>}`.
- A non-numeric or negative value gives `400 invalid_query`.
- Loop pattern: `GET /events?after=$cursor&wait=25`, handle events, set `cursor`, repeat.

### `DELETE /api/v1/message`

Soft-delete a message (sets `deleted_at`, excluded from future history fetches). The message must belong to the authenticated user.

Request body:
```json
{"id": 42}
```

Response `200`:
```json
{"deleted": 42}
```

`404` if the message doesn't exist or doesn't belong to you.

### `POST /api/v1/token/rotate`

No body needed. Returns `{"user_id": ..., "token": "<uid>:<new secret>"}` and invalidates the old token at once.

### `GET /api/v1/history?limit=N&offset=N`

Full message history for the authenticated user (oldest first, with timestamps). Used by the PWA to reconstruct the complete conversation on login.

Response `200`:

```json
{"messages": [{"id": 1, "role": "user", "content": "Hello!", "created_at": "2026-06-15 10:00:00"},
              {"id": 2, "role": "assistant", "content": "Hi there!", "created_at": "2026-06-15 10:00:01"}],
 "count": 2}
```

Query parameters:
- `limit` — max messages to return (default 500, max 2000)
- `offset` — skip this many messages from the start (default 0)

### `GET /api/v1/facts?kind=K&visibility=V`

List the authenticated user's active (non-archived) facts. Optionally filtered.

Response `200`:

```json
{"facts": [{"id": 1, "text": "Likes morning walks", "visibility": "private",
            "kind": "core", "status": "active", "created_at": "..."}],
 "count": 1}
```

Query parameters:
- `kind` — filter by memory kind: `core`, `working`, `episode`, `partner_view` (optional)
- `visibility` — filter by `private` or `shared` (optional)

### `GET /api/v1/memory?kind=K&status=S&scored=1&limit=N&offset=N`

Memory API v1. List facts with relevance scoring, filtering, and pagination.

Response `200`:

```json
{"facts": [{"id": 1, "text": "Likes morning walks", "kind": "core", "status": "active",
            "relevance_score": 1.0, "citation_count": 5, "last_cited_at": "...",
            "source_message_id": 42, "contradicts_id": null, "created_at": "...", "updated_at": "..."}],
 "total": 47, "offset": 0}
```

Query parameters:
- `kind` — filter: `core`, `working`, `episode`, `partner_view`
- `status` — filter: `active`, `improving`, `resolved`, `outdated`, `archived`
- `scored` — `1` or `true` to sort by relevance score descending
- `limit` — max results (default 50)
- `offset` — pagination offset (default 0)

### `GET /api/v1/memory/stats`

Memory statistics for the authenticated user.

```json
{"total": 47, "by_kind": {"core": 12, "working": 30, "episode": 5},
 "by_status": {"active": 40, "resolved": 5, "outdated": 2},
 "avg_score": 0.623, "oldest_uncited_days": 45}
```

### `GET /api/v1/memory/recency`

Memory health breakdown — useful for a "memory health" indicator in the PWA.

```json
{"healthy": 30, "aging": 12, "stale": 5, "contradictions": 1}
```

- `healthy`: cited in last 7 days
- `aging`: 7–30 days since last citation
- `stale`: >30 days, archival candidates
- `contradictions`: facts with detected contradictions

### `GET /api/v1/memory/search?q=pizza&limit=N`

Search facts by text (FTS5 when available, LIKE fallback).

```json
{"facts": [...], "count": 3}
```

### Static client files (no auth)

`GET /`, `/index.html`, `/duo.html`, `/app.js`, `/render.js`, `/sw.js`, `/style.css`, `/manifest.webmanifest`, `/icons/icon-192.png`, `/icons/icon-512.png`, `/icons/icon.svg`. This is a fixed list; any other path is `404 not_found`.

## 4. Registration modes

Set with `rest.registration` in `settings.yaml` or `REST_REGISTRATION`.

| Mode | Behavior |
|---|---|
| `invite` (default) | `invite` in the body must equal the secret `REST_INVITE_CODE`. If the server has no code set, every attempt gets `403 registration_unavailable`. A wrong code gets `403 invalid_invite` |
| `open` | No invite needed. For a trusted loopback or LAN only. The dev rig uses it |
| `closed` | `403 registration_closed` |

With `ALLOWED_USER_IDS` set on the server, registration also needs an explicit allowlisted `user_id` (`403 not_allowed` otherwise, `409 user_exists` if that id is already registered). Without an allowlist the server assigns the id and ignores any `user_id`.

## 5. Ids, tokens and limits

- **REST ids** start at `REST_ID_BASE = 2**52 = 4503599627370496`. Telegram ids are always smaller, so the two never collide. Ids stay below `2**53`, so they are exact JavaScript numbers.
- **Token**: `<user_id>:<secret>`. Missing, malformed, wrong or unknown tokens all give the same `401 unauthorized`.
- **Token loss**: there is no recovery. If you lost the token, register again (a new user). To replace a token you still hold, call `/token/rotate`.
- **Rate limits** (per peer IP, in memory, not shared across restarts): 10 registrations per minute and 30 failed authentications per minute. Beyond that: `429 too_many_requests`. `X-Forwarded-For` is not trusted.
- **Allowlist**: an authenticated id that is not allowed gets `403 not_allowed`.

## 6. Error codes

| Status | `error` | When |
|---|---|---|
| 400 | `invalid_json` | Body is not a JSON object |
| 400 | `invalid_text` | `text` missing, not a string, or blank |
| 400 | `text_too_long` | `text` longer than the limit |
| 400 | `invalid_name` | `name` empty after cleaning |
| 400 | `invalid_lang` | `lang` is not `en` or `te` |
| 400 | `invalid_data` | `data` missing, blank, or over 256 chars |
| 400 | `invalid_query` | Bad `after` or `wait` |
| 400 | `missing_query` | Search query parameter `q` is empty or missing |
| 400 | `invalid_id` | Message id missing or not a positive integer |
| 401 | `unauthorized` | Bad or missing token (response has `WWW-Authenticate`) |
| 403 | `not_allowed` | Id not in `ALLOWED_USER_IDS` |
| 403 | `registration_closed` | Mode is `closed` |
| 403 | `registration_unavailable` | Mode is `invite` but the server has no `REST_INVITE_CODE` |
| 403 | `invalid_invite` | Wrong invite code |
| 404 | `not_found` | Unknown path |
| 405 | `method_not_allowed` | Known path, wrong HTTP method |
| 409 | `user_exists` | Allowlisted id already registered |
| 413 | `body_too_large` | Body over 16 KiB |
| 415 | `json_required` | POST without `Content-Type: application/json` |
| 429 | `too_many_requests` | Throttled |

## 7. Two-user walkthrough

The flow: register A and B, A runs `/start` and gets a pairing code, B runs `/join CODE`, both `/skip` the onboarding interview, A sends `#send ...`, A presses approve, the cool-down passes, A gets a review prompt and presses **Send all**, B receives the relay through `/events`. Then A shares a fact and runs `/mediate 30d`.

Timing: the approval cool-down is `cooldown_minutes` (15 by default; the dev rig uses 1). With `review_before_send: true` (default) the sender gets an extra prompt after the cool-down. Press `ap:<id>` then wait, or use `#now` and `apn:<id>` to deliver immediately.

### curl

```bash
BASE=http://127.0.0.1:8766
J='Content-Type: application/json'

# 1. register (open mode; add "invite":"<code>" in invite mode)
A=$(curl -s -X POST $BASE/api/v1/register -H "$J" -d '{"name":"Asha"}')
B=$(curl -s -X POST $BASE/api/v1/register -H "$J" -d '{"name":"Ben"}')
TA=$(echo "$A" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
TB=$(echo "$B" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 2. A: /start -> the reply text contains "/join <CODE>"
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TA" -d '{"text":"/start"}'
# 3. B: /join <CODE> (use the code from step 2)
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TB" -d '{"text":"/join 85C5F8"}'
# 4. both skip onboarding
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TA" -d '{"text":"/skip"}'
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TB" -d '{"text":"/skip"}'
# 5. A drafts a relay; the reply has buttons ap:<id> ed:<id> di:<id>
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TA" -d '{"text":"#send you left the dishes again"}'
# 6. A approves (replace 2 with the id from step 5)
curl -s -X POST $BASE/api/v1/callback -H "$J" -H "Authorization: Bearer $TA" -d '{"data":"ap:2"}'
# 7. after the cool-down, A long-polls and finds the review prompt with a ba:<batch> button
curl -s "$BASE/api/v1/events?after=0&wait=25" -H "Authorization: Bearer $TA"
curl -s -X POST $BASE/api/v1/callback -H "$J" -H "Authorization: Bearer $TA" -d '{"data":"ba:1"}'
# 8. B receives the relay
curl -s "$BASE/api/v1/events?after=0&wait=25" -H "Authorization: Bearer $TB"
# 9. A saves a fact, lists facts (buttons fv:<id>), shares it, asks for a mediation over 30 days
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TA" -d '{"text":"#keep I like hiking"}'
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TA" -d '{"text":"/whatdoyouknow"}'
curl -s -X POST $BASE/api/v1/callback -H "$J" -H "Authorization: Bearer $TA" -d '{"data":"fv:1"}'
curl -s -X POST $BASE/api/v1/message -H "$J" -H "Authorization: Bearer $TA" -d '{"text":"/mediate 30d"}'
```

### Python (stdlib only)

```python
import json, re, time, urllib.error, urllib.request

BASE = "http://127.0.0.1:8766"


def call(method, path, body=None, token=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def say(token, text):
    status, body = call("POST", "/api/v1/message", {"text": text}, token)
    assert status == 200, (status, body)
    return body["replies"]


def click(token, data):
    status, body = call("POST", "/api/v1/callback", {"data": data}, token)
    assert status == 200, (status, body)
    return body["replies"]


def button(msgs, prefix):
    """First button whose data starts with prefix, or None."""
    for m in msgs:
        for row in m["buttons"]:
            for b in row:
                if b["data"].startswith(prefix):
                    return b["data"]
    return None


class Inbox:
    """Long-poll /events with a cursor; remember what was seen."""

    def __init__(self, token):
        self.token, self.cursor = token, 0

    def wait_for(self, predicate, seconds=180):
        deadline = time.time() + seconds
        while time.time() < deadline:
            _, body = call("GET", f"/api/v1/events?after={self.cursor}&wait=25", token=self.token)
            self.cursor = body["cursor"]
            for ev in body["events"]:
                if predicate(ev["msg"]):
                    return ev["msg"]
        return None


# 1. register (add "invite": "<code>" in invite mode)
a = call("POST", "/api/v1/register", {"name": "Asha"})[1]["token"]
b = call("POST", "/api/v1/register", {"name": "Ben"})[1]["token"]

# 2-3. pair
code = re.search(r"/join (\w+)", say(a, "/start")[0]["text"]).group(1)
say(b, f"/join {code}")

# 4. skip onboarding for both
say(a, "/skip")
say(b, "/skip")

# 5-6. draft and approve a relay
inbox_a, inbox_b = Inbox(a), Inbox(b)
draft = say(a, "#send you left the dishes again")
click(a, button(draft, "ap:"))

# 7. wait out the cool-down; the review prompt arrives as an event for A
prompt = inbox_a.wait_for(lambda m: button([m], "ba:"))
click(a, button([prompt], "ba:"))

# 8. the relay arrives for B as an event
relay = inbox_b.wait_for(lambda m: "DISHES" in m["text"].upper())
print("B received:", relay["text"])

# 9. share a fact and mediate
say(a, "#keep I like hiking")
fv = button(say(a, "/whatdoyouknow"), "fv:")
print(click(a, fv)[0]["text"])        # "🔓 Fact #1 is now shared with your partner."
print(say(a, "/mediate 30d")[0]["text"])
```

On the dev rig the "LLM" upper-cases input, so the relayed text reads `YOU LEFT THE DISHES AGAIN`. A real model rewrites it.

## 8. `/mediate` and sharing from `/whatdoyouknow`

- `/mediate [window]` gives a neutral view of the last `window`: `week` (default), `month`, `14d`, `2w` or a bare number of days. A window above `mediate.max_days` (90) is clamped. A bad argument returns a usage message.
- Inputs: facts both partners marked **shared** in that window, plus each partner's filtered, unverified impressions of the other. Raw chat messages and private facts are never used. With no input the reply says so and no model call happens.
- `/whatdoyouknow` lists the caller's facts with one toggle button each (`fv:<id>`, most recent 30). The facts are private until their owner presses the button. The model can never publish a fact.

## 9. Security notes

- The API has no TLS. Bind to loopback, a LAN, or a Tailscale address, or put a TLS proxy in front. Installing the PWA needs a secure context (HTTPS or `localhost`); see DEPLOYMENT.md.
- `REST_INVITE_CODE` is a secret. It is read from the environment at request time and is never in `settings.yaml`, logs or responses.
- Tokens are stored in `auth_tokens` and returned only on registration or rotation. Treat them as passwords.
- Every response carries `Content-Security-Policy`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- Event payloads contain the message text of the relayed conversation, kept in the `rest_outbox` table for `REST_EVENT_RETENTION_DAYS`. Lower the value if you want less stored. The admin dashboard classifies this table as raw text.
- The static file list is fixed. There is no directory listing or path traversal.
