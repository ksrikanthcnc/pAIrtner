# Output Contract

<!-- Define what JSON the model returns for each task. -->
<!-- Copy sections from packs/default/contract.md and modify as needed. -->

## Task: chat

Regular conversation. The model reads the user's message and responds.

```json
{
  "reply": "Your response to the user",
  "memory_ops": [],
  "relay_draft": null,
  "detected_mood": null,
  "articulation_note": null
}
```

**reply**: Your conversational response. Be natural, not formulaic.

**memory_ops**: Facts to save, update, or status-change. See memory.md for when to use each op.

**relay_draft**: If the user is venting about their partner, optionally draft a softened version they could send. null if not applicable.

**detected_mood**: One of: happy, stressed, angry, sad, anxious, reflective, neutral, sarcastic. null if unclear.

**articulation_note**: If the user struggled to express something, a brief tip. null usually.

## Task: keep

User tagged with #keep — extract and save facts only.

```json
{
  "reply": "Brief acknowledgment",
  "memory_ops": [{"op": "add", "kind": "working", "text": "..."}]
}
```

## Task: rewrite

User tagged with #send — draft a softened relay for their partner.

```json
{
  "reply": "Your response to the sender",
  "memory_ops": [],
  "relay_draft": "The softened message for the partner"
}
```

<!-- Add more task sections as needed. See packs/default/contract.md for the full set. -->
