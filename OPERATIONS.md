# pAIrtner Operations Guide

Quick reference for starting, stopping, and managing the bot.

## Location

**Windows**: `E:\media\docker\pairtner\`
**WSL path**: `/mnt/e/media/docker/pairtner/`

## First-time setup

```bash
# Enter WSL
wsl -d Ubuntu-26.04 -u root

# Go to project
cd /mnt/e/media/docker/pairtner

# Create .env from example
cp .env.example .env
nano .env
```

### Required .env settings

```env
# Telegram (get from @BotFather on Telegram)
TELEGRAM_BOT_TOKEN=your-bot-token

# LLM — pick ONE:

# Option A: Gemini (free tier, cloud)
GEMINI_API_KEY=your-gemini-key
MODEL_DEFAULT=gemini-3.1-flash-lite

# Option B: Ollama (local, no API key needed)
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=local
MODEL_DEFAULT=qwen3:8b

# REST/PWA access
ADAPTER=telegram+rest
REST_HOST=0.0.0.0
REST_PORT=8780
REST_REGISTRATION=invite
REST_INVITE_CODE=your-secret-code

# Dashboard
DASHBOARD_TOKEN=your-admin-token-here
```

## Start / Stop

```bash
# Enter WSL as root
wsl -d Ubuntu-26.04 -u root
cd /mnt/e/media/docker/pairtner

# Start bot
docker compose up -d pairtner

# Start dashboard
DASHBOARD_BIND=0.0.0.0 docker compose --profile dashboard up -d dashboard

# Stop everything
docker compose --profile dashboard down

# View logs
docker compose logs -f pairtner
docker compose logs -f dashboard

# Restart after code changes
docker compose up -d --build pairtner
DASHBOARD_BIND=0.0.0.0 docker compose --profile dashboard up -d --build dashboard
```

## Access

| Service | URL | Auth |
|---------|-----|------|
| **PWA** | http://192.168.1.7:8780 | Sign up with invite code from .env |
| **Dashboard** | http://192.168.1.7:8765 | DASHBOARD_TOKEN from .env |
| **Duo test** | http://192.168.1.7:8780/duo.html | Same as PWA |
| **Telegram** | Via your bot on Telegram | /start to begin |

Via Tailscale (remote): replace `192.168.1.7` with `100.123.91.124`

## Dashboard login

Open `http://192.168.1.7:8765`, enter the `DASHBOARD_TOKEN` from your `.env`. You'll see:
- **Overview**: user counts, message counts, usage stats
- **Users**: per-user activity
- **Couples**: paired users, conversation viewer (💬 button)
- **Queue**: pending relays and batches
- **Admin**: backup, clear user data, runtime config
- **SQLite Explorer**: browse raw tables

## Updating code

When new commits are pushed:
```bash
cd /mnt/e/media/docker/pairtner
git pull origin master
docker compose up -d --build pairtner
DASHBOARD_BIND=0.0.0.0 docker compose --profile dashboard up -d --build dashboard
```

## Database

- **Location**: `couples_bot.db` in the project root (bind-mounted into container)
- **Backup**: automatic daily backups in `backups/` folder
- **Manual backup**: Dashboard → Admin → "Create backup"
- **Browse**: Dashboard → SQLite Explorer

## Prompt packs

Edit files in `packs/default/` on the host — changes apply on the next message (no restart needed):
- `persona.md` — bot personality
- `contract.md` — JSON output schema
- `memory.md` — memory policy
- `voices/` — language/style presets
- `tasks/` — per-task instructions

## Troubleshooting

```bash
# Check if containers are running
docker compose ps

# Check bot logs for errors
docker compose logs --tail 50 pairtner

# Check if Ollama is reachable (if using local LLM)
curl http://localhost:11434/api/tags

# Reset database (nuclear option)
docker compose down
rm couples_bot.db
docker compose up -d pairtner
```
