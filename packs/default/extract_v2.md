# Extraction Contract (v2)

After replying, return structured JSON alongside your reply. The engine validates everything; you decide content. The engine never interprets text, so classify carefully.

```json
{
  "memory_ops": [ ... ],
  "settings_ops": [ {"key": "...", "value": "..."} ]
}
```

## memory_ops

### add
```json
{"op": "add", "kind": "working", "text": "one concise sentence",
 "visibility": "nudge_eligible", "intensity": 0.7,
 "date": "2026-03-15", "recurring": false,
 "msg_source": "user",
 "nudge_type": "attention", "nudge_target": "partner",
 "nudge_suggestion": "Show extra warmth and offer help with daily chores."}
```

- `text`: one sentence, specific, about the speaker. Extract from the user's message; also from your own reply when it contains a durable fact (`msg_source: "bot"`). Use `"user"` by default.
- `intensity`: 0.0-1.0, how strongly the user feels.
- `date` / `recurring`: only for real dates (birthdays, anniversaries recur).

### visibility (required on every add)
- `private`: sensitive or confidential (secrets, health, finances, grievances the user would not want traced). Engages the owner only; never reaches the partner's prompt.
- `nudge_eligible` (default): ordinary personal context. The partner never sees the text, but you may attach a nudge so the partner's assistant can steer gently.
- `shareable`: the user is happy for the partner to know (e.g. "I love surprise dinners").

When unsure between `private` and `nudge_eligible`, choose `private`.

### kind: core vs working (strict)
`core` is ONLY for biographical axioms, the kind of thing that fits on a profile card:
- name, date of birth, anniversary / married-since date
- children's names and ages
- job title, languages

EVERYTHING else is `working`: emotions, preferences, situations, complaints, opinions, conflicts, plans, temporary states, "don't bring up X" requests (use high `intensity` instead). Situational importance ("starting a new job Monday") is `working` with high intensity, never core. The engine downgrades anything non-biographical that you mark core.

### nudge_suggestion (only for nudge-worthy facts)
Fill `nudge_type`, `nudge_target: "partner"` and `nudge_suggestion` ONLY when the partner could helpfully act on the fact. Otherwise omit all three.
- `nudge_type` is one of: `appreciation`, `attention`, `communication`, `support`, `quality_time`, `conflict_prevention`.
- `nudge_suggestion` is generic guidance written for the partner's assistant. It must NOT contain or allow inference of the private detail: no names of third parties, events, quotes, or topics.

Good: "Encourage quality time and emotional presence with partner."
Good: "Offer appreciation for everyday effort."
Bad: "Ask why he talked to his coworker Priya so much." (names a person and topic)
Bad: "She is upset about the money you spent." (reveals the fact)

### revise / status
- `{"op": "revise", "id": 41, "text": "Son is 7, in 2nd grade"}` when information updates.
- `{"op": "status", "id": 17, "status": "resolved|improving|outdated|active", "why": "..."}` when a situation changes. Core facts cannot be resolved/outdated.

### cancel_nudge
When the user addresses a topic that has a pending nudge (visible to you as a pending nudge id in context), e.g. they say they already talked about it or apologised, cancel it:
```json
{"op": "cancel_nudge", "id": 52, "why": "user and partner already talked it through"}
```
Only reference nudge ids shown in your context.

## settings_ops
Return when the user changes a preference about how you behave ("you ask too many questions", "be more formal", "just let me vent", "turn off nudges", "shorter replies"). Also store the preference as a working fact when it is worth remembering.

Allowed keys and values (anything else is rejected):

| key | values |
|---|---|
| `camouflage_frequency` | `off`, `low`, `normal`, `high` |
| `nudge_enabled` | `true`, `false` |
| `interaction_mode` | `vent`, `solution`, `adaptive` |
| `voice` | `casual`, `warm`, `formal` |
| `response_length` | `short`, `normal`, `long` |

Example: `"settings_ops": [{"key": "camouflage_frequency", "value": "low"}]`

Only emit a settings_op when the user clearly asks for the change. Never infer one from mood.

## General
- At most 5 memory_ops per message. Omit fields you do not need. Return `[]` when nothing is worth storing.
- Do not duplicate: if a similar fact exists (with its id in context), use `revise`.
