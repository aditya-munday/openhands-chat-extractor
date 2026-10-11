# OpenHands Chat Extractor

Extract a clean, fully-parsed transcript from any **publicly shared** OpenHands
conversation, straight from its share link. Single file, standard library only,
no install, no credentials.

The OpenHands share page
(`https://app.all-hands.dev/canvas/shared/conversations/<id>`) is a thin React
app that loads its data from two public JSON endpoints. This tool talks to those
endpoints directly, paginates the whole event stream, and renders it as readable
text — either the **full transcript** (messages, tool calls, code, observations,
reasoning) or a **chat-only** view (just the user ⇄ assistant conversation).

## Features

- **One file, zero dependencies** — pure Python 3.9+ standard library (`urllib`).
- **Full pagination** — walks every page of events (the API caps pages at 100).
- **Two output modes**:
  - *Full* — metadata header + every event, chronological.
  - *`--chat-only`* — only user/assistant messages: no tool calls, code changes,
    observations, or timestamps.
- **Complete event coverage** — messages, actions (`terminal`, `file_editor`, MCP
  tools, …), observations, reasoning/thoughts, agent errors, and context
  compactions.
- **Raw JSON export** (`--json`) for programmatic use.
- **Forgiving input** — accepts a full share URL, a path, or a bare 32-char id,
  and auto-detects the host for non-cloud deployments.
- **Clean errors** — clear messages for unshared/missing conversations, network
  failures, and non-JSON responses.

## Requirements

- Python 3.9 or newer
- Outbound HTTPS access to the OpenHands host

No third-party packages, no API keys. Only conversations the owner has shared
publicly are readable — that is by design of the public endpoints.

## Install

```bash
git clone https://github.com/aditya-munday/openhands-chat-extractor.git
cd openhands-chat-extractor
# that's it — it's a single script
```

## Usage

```bash
python main.py <shared-url-or-id> [options]
```

### Examples

```bash
# Full parsed transcript, output name derived from the conversation title
python main.py https://app.all-hands.dev/canvas/shared/conversations/<conversation-id>

# Bare conversation id, explicit output path
python main.py <conversation-id> -o convo.txt

# Just the user/assistant chat (no tools, code, observations, timestamps)
python main.py <url> --chat-only

# Also dump the raw event stream next to the .txt
python main.py <url> --json
```

### Options

| Flag | Description |
|------|-------------|
| `-o, --out PATH` | Output `.txt` path (default: derived from the conversation title). |
| `--chat-only` | Keep only user/assistant messages; drop tool calls, code, observations, timestamps. |
| `--json` | Also write the raw events to `<out>.events.json`. |
| `--no-system` | Drop the `SystemPromptEvent` from the full transcript. |
| `--timeout SECONDS` | Per-request timeout (default `30`). |

## Output

### Full transcript

```
==============================================================================
OpenHands Shared Conversation: Example conversation
==============================================================================
Conversation ID : <conversation-id>
Source URL      : https://app.all-hands.dev/canvas/shared/conversations/<conversation-id>
Created         : 2026-01-01 12:00:00 UTC
Updated         : 2026-01-01 12:05:00 UTC
Model           : openhands/<model-name>
Repository      : -
Branch          : -
Git provider    : -
Created by      : <user-id>
Events          : 42
Tokens          : prompt=100000 completion=5000 reasoning=0
==============================================================================

[2026-01-01 12:00:10 UTC] user · Message
    Summarize the README in this repo.

[2026-01-01 12:00:11 UTC] agent · ActionEvent
  · reasoning:
      I should read the README first.
  summary: Read the README file
  action:
      $ cat README.md

[2026-01-01 12:00:12 UTC] environment · ObservationEvent
  observation:
      # My Project
      ...

      [The command completed with exit code 0.]
```

### Chat only (`--chat-only`)

```
User:
    Summarize the README in this repo.

Assistant:
    Here's a short summary ...
```

## How it works

The share page loads data from two **unauthenticated** JSON endpoints on the
same origin as the page:

| Endpoint | Purpose |
|----------|---------|
| `GET /api/shared-conversations?ids=<id>` | Conversation metadata (title, model, repo, branch, timestamps, token metrics). Empty array if not shared. |
| `GET /api/shared-events/search?conversation_id=<id>&limit=100&page_id=<token>` | One page of events, plus `next_page_id`. `limit` is hard-capped at 100 (higher returns HTTP 422). |

The tool follows `next_page_id` until exhausted, then renders every event. Event
types include `MessageEvent`, `ActionEvent`, `ObservationEvent`,
`SystemPromptEvent`, `AgentErrorEvent`, and `Condensation`. Actions and their
results are correlated by `ActionEvent.id == ObservationEvent.action_id`.

See [`AGENTS.md`](AGENTS.md) for the full event-schema notes.

## Privacy note

Shared conversations are public to anyone with the link. If a conversation
contains pasted secrets (tokens, keys, credentials), those are exposed — extract
responsibly and rotate anything sensitive. This tool does not redact content.

## Limitations

- Only **publicly shared** conversations can be read; private ones return no data.
- Requires network access; it is a live client, not an offline parser.
- Output format follows the event schema at extraction time; unknown event kinds
  are preserved as a JSON dump so nothing is silently dropped.

## License

[MIT](LICENSE)
