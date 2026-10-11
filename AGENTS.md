# AGENTS.md

## What this repo is
Single-file tool: `main.py` — pulls a fully-parsed transcript out of a
**publicly shared** OpenHands conversation and writes it to a `.txt`.

## How shared conversations are fetched (reverse-engineered)
The share page `https://app.all-hands.dev/canvas/shared/conversations/<id>` is a React
SPA. It loads data from two **unauthenticated** JSON endpoints on the same origin:

- `GET /api/shared-conversations?ids=<id>` → metadata array (title, model, repo, branch,
  timestamps, token metrics). Empty array if not shared.
- `GET /api/shared-events/search?conversation_id=<id>&limit=100&page_id=<token>`
  → `{ "items": [...events...], "next_page_id": <token|null> }`. `limit` is hard-capped
  at 100 (101+ → HTTP 422). Follow `next_page_id` until null.

These are the cloud-backend paths. The local/agent-server backend uses a different client
(`getSharedConversation` / `searchSharedEvents`) and is not used here.

### Event schema (`kind` field)
- `SystemPromptEvent` — `system_prompt.text`, `tools[]`
- `MessageEvent` — `llm_message.{role, content[{type:text,text}], tool_calls}`
- `ActionEvent` — `action.kind` (`TerminalAction`, `FileEditorAction`, `MCPToolAction`,
  `ThinkAction`, `FinishAction`, `StrReplaceEditorAction`, …), `thought`, `reasoning_content`,
  `summary`, `tool_name`
- `ObservationEvent` — `observation.kind` (`TerminalObservation`, `FileEditorObservation`,
  `MCPToolObservation`, …), `observation.content`, `observation.metadata.suffix`
- `AgentErrorEvent` — `error`, `classification`
- `StreamingDeltaEvent` — transient, skipped
- `ConversationStateUpdateEvent` — `key`/`value`

Link actions↔observations by `ActionEvent.id == ObservationEvent.action_id`.

## Run
```
python main.py <shared-url-or-32char-id> [-o OUT.txt] [--chat-only] [--json] [--no-system]
```
- `--chat-only` — keep only user/assistant chat (no tool calls, code changes, observations, or timestamps).
- `--json` — also write the raw events to `<out>.events.json`.
- `--no-system` — drop the `SystemPromptEvent`.

## Notes
- No credentials needed; only owner-shared conversations are readable.
- Stdlib only (urllib), Python 3.9+.
