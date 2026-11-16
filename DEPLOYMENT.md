# Deployment Guide

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
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `GEMINI_API_KEY` | Yes (if using Gemini) | From Google AI Studio |
| `DB_PATH` | No | SQLite path (default: `couples_bot.db`) |
| `DB_ENCRYPTION_KEY` | No | Fernet key for encrypting BYO API keys |
| `LLM_PROVIDER` | No | `gemini` (default) or `openai_compat` |
| `LLM_BASE_URL` | If openai_compat | e.g. `http://host.docker.internal:11434/v1` |
| `LLM_API_KEY` | If openai_compat | API key for the provider |
| `ADAPTER` | No | `telegram` (default), `websocket`, `cli`, `both` |
| `WS_PORT` | No | WebSocket port (default: `8080`) |

### Volume mounts

| Path | Purpose |
|------|---------|
| `/app/couples_bot.db` | SQLite database (persistent) |
| `/app/backups` | Automated daily backups |
| `/app/settings.yaml` | Runtime config (read-only) |
| `/app/persona.md` | Bot personality file (read-only) |

### docker-compose.yml

The included `docker-compose.yml` handles all of this:

```yaml
services:
  pairtner:
    build: .
    env_file: .env
    volumes:
      - ./couples_bot.db:/app/couples_bot.db
      - ./settings.yaml:/app/settings.yaml:ro
      - ./persona.md:/app/persona.md:ro
      - ./backups:/app/backups
    restart: unless-stopped
    # Uncomment for WebSocket adapter:
    # ports:
    #   - "8080:8080"
    # environment:
    #   - ADAPTER=both
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
uv run python -m couples_bot
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
ExecStart=/home/ubuntu/.local/bin/uv run python -m couples_bot
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
      - ./couples_bot.db:/app/couples_bot.db
      - ./backups:/app/backups
    restart: unless-stopped
```

### Performance notes

- 8B models (llama3.1, gemma2:9b) work well on 16GB+ RAM
- On Apple Silicon, Ollama uses the GPU automatically
- Response quality is good but not Gemini-level for nuanced relationship advice
- Works great for testing and privacy-sensitive deployments
