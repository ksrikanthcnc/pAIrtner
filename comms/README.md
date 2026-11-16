# Comms

> pAIrtner couples mediator Telegram bot

## Agents

| Agent | Role | Pending |
|-------|------|---------|
| advisor | design and research session | 0 |
| main | builds the bot | 0 |

## Structure

Each agent folder:
```
<agent>/
  README.md       ← role, owns, focus (agent maintains)
  inbox/          ← messages TO this agent
  done/           ← processed messages
  contracts/      ← interfaces/specs this agent owns
  session.md      ← stable state
  wip.md          ← active work
  journal.md      ← session log
```

## Rules

- **NEVER touch another agent's files directly** — no writing, editing, moving, or deleting.
- Use `comms send` to communicate, `comms done` to acknowledge. No manual file operations.
- Check your inbox (`comms inbox <you>`) when starting a session.
- Respond by sending to the original sender's inbox.
- Only modify files you own (see your README.md `Owns:` section).
- Only the owning agent modifies contracts/; others propose via messages.
- Update your README.md when your focus or ownership changes.
