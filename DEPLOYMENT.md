# Deployment Guide

## Docker deployment (bot + dashboard)

Two services share one named volume (`pairtner-data`, SQLite at `/data/pairtner.db`):
`pairtner-bot` (Telegram bot, background loops, REST API on 8080) and
`pairtner-dashboard` (admin dashboard on 8765).

### Prerequisites

- Docker Engine with the Compose plugin
- A `.env` file: `cp .env.example .env`, then set `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `DB_ENCRYPTION_KEY` and `DASHBOARD_TOKEN` (16+ chars)

### Quick start

```bash
docker compose up -d --build
docker compose ps
# Dashboard: http://localhost:8765
```

Helpers: `scripts/start.sh`, `scripts/stop.sh`, `scripts/logs.sh`.

### Windows (WSL)

Install Docker Desktop with WSL integration (or Docker inside Ubuntu), clone the repo inside WSL or under `/mnt/c/...`, create `.env`, then edit `BOT_DIR` at the top of the scripts to the project's WSL path. Double-click:

- `start-wsl.bat` starts the stack
- `stop-wsl.bat` stops it
- `logs-wsl.bat` follows the logs

Dashboard: http://localhost:8765

### Environment variables (Docker)

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather (this is the name the code reads) |
| `GEMINI_API_KEY` | Gemini key |
| `DB_PATH` | `/data/pairtner.db` (shared volume) |
| `DB_ENCRYPTION_KEY` | Fernet key for encrypting stored keys |
| `ADMIN_USER_IDS` / `ALLOWED_USER_IDS` | Comma-separated Telegram ids |
| `LLM_PROVIDER` / `MODEL_DEFAULT` | `gemini` and model name |
| `DASHBOARD_TOKEN` / `DASHBOARD_HOST` / `DASHBOARD_PORT` / `DASHBOARD_BIND` | Dashboard login token, listen address, port, host-side bind interface |
| `ADAPTER` / `REST_HOST` / `REST_PORT` / `REST_BIND` | Adapter selection, REST listen address and port (8080), host-side bind interface |

The full variable table is under "Environment variables" below.

### Healthcheck and monitoring

Both services expose `GET /healthz` and have Docker healthchecks.

```bash
docker compose ps                                   # shows (healthy)/(unhealthy)
docker inspect --format '{{.State.Health.Status}}' $(docker compose ps -q pairtner-bot)
docker compose logs -f pairtner-bot
```

### Backup strategy

- The bot writes rotating backups to `./backups` on the host (bind mount).
- Back up the DB volume manually:

```bash
docker run --rm -v couples-bot_pairtner-data:/data -v "$PWD/backups":/out alpine \
  cp /data/pairtner.db /out/manual_$(date +%F).db
```

(The volume name is `<project-dir>_pairtner-data`; check `docker volume ls`.)
- Copy `./backups` off the machine regularly.

## Docker

### Build and run

```bash
cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN and GEMINI_API_KEY

docker compose up -d
```

### Environment variables

All secrets go in `.env` (never in `settings.yaml`):

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes, unless `ADAPTER` has no Telegram (`rest`, `websocket`, `cli`) | From @BotFather |
| `GEMINI_API_KEY` | Yes (if using Gemini) | From Google AI Studio |
| `DB_PATH` | No | SQLite path (default: `pairtner.db`) |
| `DB_ENCRYPTION_KEY` | No | Fernet key for encrypting BYO API keys |
| `LLM_PROVIDER` | No | `gemini` (default) or `openai_compat` |
| `LLM_BASE_URL` | If openai_compat | e.g. `http://host.docker.internal:11434/v1` |
| `LLM_API_KEY` | If openai_compat | API key for the provider |
| `ADAPTER` | No | `telegram` (default), `websocket`, `cli`, `both`, `rest`, `telegram+rest` |
| `WS_PORT` | No | WebSocket port (default: `8080`) |
| `REST_INVITE_CODE` | If `REST_REGISTRATION=invite` (secret) | Code a person must send to register. Env only, never in `settings.yaml`. If it is empty in invite mode, registration is refused |
| `REST_HOST` / `REST_PORT` | No | REST listen address (defaults `127.0.0.1` / `8780`). Use `0.0.0.0` inside a container |
| `REST_REGISTRATION` | No | `closed`, `invite` (default) or `open` |
| `REST_MAX_TEXT_CHARS` / `REST_EVENT_RETENTION_DAYS` / `REST_LONG_POLL_MAX_SECONDS` | No | Defaults `4000` / `14` / `25` |
| `LOOPS_DIGEST_POLL_SECONDS` / `LOOPS_CHECKIN_POLL_SECONDS` / `LOOPS_NUDGE_INTERVAL_SECONDS` / `LOOPS_BACKUP_INTERVAL_SECONDS` | No | Background loop intervals, defaults `60` / `60` / `0` (use `nudges.interval_hours`) / `3600` |
| `ALLOWED_USER_IDS` | No | Comma-separated ids. Empty = open. REST ids start at `4503599627370496` (see below) |
| `DASHBOARD_TOKEN` | For dashboard | Admin dashboard access token, 16+ chars (see Dashboard) |
| `DASHBOARD_BIND` | No | Host interface for the dashboard port (default `127.0.0.1`) |
| `DASHBOARD_HOST` / `_PORT` / `_SHOW_RAW_TEXT` / `_ADMIN_ACTIONS` / `_PAGE_SIZE` / `_SESSION_TTL_HOURS` | No | Override the `dashboard:` block in `settings.yaml` |

### Volume mounts

| Path | Purpose |
|------|---------|
| `/app/pairtner.db` | SQLite database (persistent) |
| `/app/backups` | Automated daily backups |
| `/app/settings.yaml` | Runtime config (read-only) |
| `/app/packs` | Prompt packs (read-only in the container; edit on the host, applied on the next message) |

### docker-compose.yml

The included `docker-compose.yml` handles all of this:

```yaml
services:
  pairtner:
    build: .
    env_file: .env
    volumes:
      - ./pairtner.db:/app/pairtner.db
      - ./settings.yaml:/app/settings.yaml:ro
      - ./packs:/app/packs:ro
      - ./backups:/app/backups
    restart: unless-stopped
    # Uncomment for WebSocket adapter:
    # ports:
    #   - "8080:8080"
    # environment:
    #   - ADAPTER=both
    # Uncomment for the web app / REST API (see "Web app (PWA) / REST API"):
    # ports:
    #   - "127.0.0.1:8780:8780"
    # environment:
    #   - ADAPTER=telegram+rest      # or rest
    #   - REST_HOST=0.0.0.0
    #   - REST_REGISTRATION=invite
    #   # REST_INVITE_CODE comes from .env (env_file), never from this file
```

### Useful commands

```bash
docker compose logs -f          # follow logs
docker compose restart          # restart after config change
docker compose down             # stop
docker compose up -d --build    # rebuild after code changes
```

## Oracle Cloud Free Tier

Oracle's Always Free tier includes ARM instances (4 OCPU, 24 GB RAM) — more than enough for pAIrtner.

### 1. Create an instance

- Shape: `VM.Standard.A1.Flex` (ARM), 1 OCPU, 6 GB RAM is plenty
- Image: Ubuntu 22.04 (aarch64)
- Add your SSH public key

### 2. Firewall rules

Oracle uses both security lists and iptables. For Telegram polling mode, no inbound ports are needed — the bot makes outbound HTTPS connections only.

If using the WebSocket adapter:

```bash
# On the instance
sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save

# In Oracle Cloud Console: VCN → Security List → add ingress rule for TCP 8080
```

### 3. Install dependencies

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Docker (optional, or run directly)
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
# Log out and back in
```

### 4. Deploy

```bash
git clone <repo-url> && cd couples-bot
cp .env.example .env
# Edit .env

# Option A: Docker
docker compose up -d

# Option B: Direct
uv sync
uv run python -m pairtner
```

### 5. systemd service (for direct runs)

Create `/etc/systemd/system/pairtner.service`:

```ini
[Unit]
Description=pAIrtner Couples Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/couples-bot
ExecStart=/home/ubuntu/.local/bin/uv run python -m pairtner
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/couples-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pairtner
sudo journalctl -u pairtner -f    # follow logs
```

## Docker on Home Server

If you have an always-on machine (NAS, Plex server, Raspberry Pi):

```bash
git clone <repo-url> && cd couples-bot
cp .env.example .env
# Edit .env

docker compose up -d
```

The bot uses Telegram polling (no inbound ports needed). The SQLite database and backups are persisted via volume mounts.

For automatic updates:

```bash
cd couples-bot && git pull && docker compose up -d --build
```

## Tailscale for Private Access

If you want to use the WebSocket adapter without exposing any public ports:

### 1. Install Tailscale on the server

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

### 2. Install Tailscale on your phone/laptop

Download from [tailscale.com/download](https://tailscale.com/download) and sign in with the same account.

### 3. Run pAIrtner with WebSocket

```bash
# In .env:
ADAPTER=both        # or just "websocket" if you don't need Telegram
WS_PORT=8080
```

Now connect to `ws://<tailscale-ip>:8080` from any device on your Tailnet. No port forwarding, no public exposure.

## Local LLM with Ollama

Run fully private — no data leaves your machine.

### 1. Install Ollama

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

### 2. Pull a model

```bash
ollama pull llama3.1           # good general purpose
ollama pull gemma2:9b          # smaller, faster
ollama pull mistral            # another good option
```

### 3. Configure pAIrtner

In `.env`:

```bash
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL_DEFAULT=llama3.1
# LLM_MODEL_HEAVY=llama3.1    # optional, same model is fine
```

### 4. Docker + Ollama

If running pAIrtner in Docker and Ollama on the host:

```bash
# In .env:
LLM_BASE_URL=http://host.docker.internal:11434/v1
```

On Linux, you may need `--add-host=host.docker.internal:host-gateway` in docker-compose:

```yaml
services:
  pairtner:
    build: .
    env_file: .env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./pairtner.db:/app/pairtner.db
      - ./backups:/app/backups
    restart: unless-stopped
```

### Performance notes

- 8B models (llama3.1, gemma2:9b) work well on 16GB+ RAM
- On Apple Silicon, Ollama uses the GPU automatically
- Response quality is good but not Gemini-level for nuanced relationship advice
- Works great for testing and privacy-sensitive deployments

## Web app (PWA) / REST API

The REST adapter serves a JSON API and an installable web client from one port. Start it with `ADAPTER=rest` (web app only) or `ADAPTER=telegram+rest` (both, one set of background loops). API reference: [docs/REST_API.md](docs/REST_API.md).

### Settings

| Setting | `settings.yaml` | Env | Default |
|---|---|---|---|
| Bind address | `rest.host` | `REST_HOST` | `127.0.0.1` |
| Port | `rest.port` | `REST_PORT` | `8780` |
| Registration | `rest.registration` | `REST_REGISTRATION` | `invite` |
| Invite code (secret) | not in YAML | `REST_INVITE_CODE` | unset |
| Message size limit | `rest.max_text_chars` | `REST_MAX_TEXT_CHARS` | `4000` |
| Event retention (days) | `rest.event_retention_days` | `REST_EVENT_RETENTION_DAYS` | `14` |
| Long-poll cap (s) | `rest.long_poll_max_seconds` | `REST_LONG_POLL_MAX_SECONDS` | `25` |
| Digest / check-in poll (s) | `loops.digest_poll_seconds`, `loops.checkin_poll_seconds` | `LOOPS_DIGEST_POLL_SECONDS`, `LOOPS_CHECKIN_POLL_SECONDS` | `60` |
| Nudge interval (s) | `loops.nudge_interval_seconds` | `LOOPS_NUDGE_INTERVAL_SECONDS` | `0` (= `nudges.interval_hours`) |
| Backup interval (s) | `loops.backup_interval_seconds` | `LOOPS_BACKUP_INTERVAL_SECONDS` | `3600` |

Registration modes: `invite` needs the code in `REST_INVITE_CODE` (put it in `.env`; share it with your partner out of band). `open` lets anyone who can reach the port register, so use it only on loopback or a trusted LAN. `closed` stops new sign-ups. Without a Telegram adapter, `TELEGRAM_BOT_TOKEN` is not needed.

### Docker

Uncomment the REST lines in `docker-compose.yml` (published port, `ADAPTER`, `REST_HOST=0.0.0.0`). Publish the port on a private interface (`127.0.0.1:8780:8780`, or your Tailscale IP). `REST_INVITE_CODE` goes in `.env`.

### HTTPS, Tailscale and installing the PWA

The server speaks plain HTTP. A browser installs a PWA and registers its service worker only in a secure context: `https://` or `http://localhost`. A LAN address over plain HTTP works as a normal web page but cannot be installed. Options:

```bash
# Tailscale: HTTPS with a real certificate for your tailnet name, proxied to the local port
tailscale serve --bg 8780          # then open https://<machine>.<tailnet>.ts.net/
```

or put a reverse proxy with TLS (Caddy, nginx) in front of the port. The proxy must pass `Authorization` through and must not cache `/api/`. The server does not trust `X-Forwarded-For`, so its per-IP throttles see the proxy's address; keep the proxy on the same host, private network only.

Do not expose the port to the public internet. There is no per-account lockout beyond the throttles (10 registrations and 30 failed authentications per minute per peer IP), and the invite code is the only gate to sign-up.

### Allowlist with REST ids

REST users get ids from a reserved range starting at `4503599627370496` (`2**52`), above any Telegram id. With `ALLOWED_USER_IDS` empty, ids are assigned automatically. When it is set, a person can only register with an id you listed: put `4503599627370497,4503599627370498` in the list and have each person send `"user_id": <their id>` with `/register`. Telegram ids and REST ids can be mixed in the same list.

### Privacy with a local model

The notice in `/start` and `/help` is built from the running adapter and LLM. With `ADAPTER=rest` and a local model (`LLM_BASE_URL` pointing at loopback, `host.docker.internal` or a private address) it says that messages stay on this server and are processed by a local model on your network, and it names neither Telegram nor Google. With `telegram+rest` it names both transports. This describes where the bot sends data; it does not encrypt traffic. Without TLS the HTTP hop between a phone and the server is readable by anyone on that network, so use Tailscale or TLS for anything beyond loopback.

Stored data: the `rest_outbox` table keeps the text of messages queued for web users for `rest.event_retention_days` (default 14) so a client that was offline can catch up. Lower it if you want less retained.

### Known limitation

`ADAPTER=both` (Telegram + WebSocket) runs two sets of background loops, so relays can be marked as sent by the loop that cannot reach the user. Use `telegram+rest` for combined deployments; it starts the loops once and routes each message by transport.

## Dashboard

The admin dashboard (`python -m pairtner.dashboard`) runs as a second compose
service. It is opt-in, so existing `docker compose up -d` deployments are
unchanged.

```bash
# .env (or exported in your shell): 16+ characters, keep it secret
DASHBOARD_TOKEN=<random string>
# Host-side interface. Default 127.0.0.1. Use your LAN or Tailscale IP to reach it remotely.
DASHBOARD_BIND=100.x.y.z

docker compose --profile dashboard up -d --build dashboard
```

Open `http://<DASHBOARD_BIND>:8765` and enter the token. Do not publish this port
to the public internet: there is no TLS and the token is the only protection.

- **Isolation:** the service has no `env_file`. Compose passes it only
  `DASHBOARD_TOKEN`, so the bot's Telegram, LLM and encryption keys are not in
  its environment. It mounts `pairtner.db`, `settings.yaml` (read-only) and
  `./backups`.
- **Raw text:** set `dashboard.show_raw_text: false` in `settings.yaml` (or
  `DASHBOARD_SHOW_RAW_TEXT=false`) to show counts and metadata only.
- **Admin actions:** set `dashboard.admin_actions_enabled: false` for a
  view-only dashboard.
- **Backups:** "Backup now" and every clear action write
  `./backups/admin_<action>_<UTC timestamp>.db` using SQLite's online backup API,
  then verify it with `PRAGMA integrity_check`. The bot's daily rotation ignores
  `admin_*` files, so they are kept until you delete them. Each action is also
  logged (no content) to `./backups/admin_audit.log`.
- **Concurrency:** the dashboard never keeps a connection open. Reads use a
  read-only connection. Clear actions run in one `BEGIN IMMEDIATE` transaction,
  so the bot's writes wait for it. The DB file is never replaced or deleted.
- **Bind-mount caveat:** compose mounts the single file `./pairtner.db`. In
  SQLite's default journal mode the `-journal` file lives next to the DB, and
  with a single-file mount it exists separately in each container. Dashboard
  transactions are short and always preceded by a backup, but for new
  deployments prefer mounting a directory (for example `./data:/app/data` with
  `DB_PATH=/app/data/pairtner.db` for both services).

Run it without Docker with `uv run python -m pairtner.dashboard` (see README).
