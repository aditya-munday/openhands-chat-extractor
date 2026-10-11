#!/usr/bin/env python3
"""Extract a fully-parsed transcript from a publicly shared OpenHands conversation.

The public share page at
    https://app.all-hands.dev/canvas/shared/conversations/<id>
is a thin React shell that talks to two unauthenticated JSON endpoints:

    GET /api/shared-conversations?ids=<id>
    GET /api/shared-events/search?conversation_id=<id>&limit=100&page_id=<token>

This tool walks those endpoints (paginating 100 events at a time), renders every
event of the conversation into readable text, and writes it to a .txt file.

Only conversations the owner has shared publicly are readable; anything else
returns no data. No credentials are used or needed.

Usage:
    python main.py <shared-url-or-id> [-o OUT.txt] [--chat-only] [--json] [--no-system]

Examples:
    python main.py https://app.all-hands.dev/canvas/shared/conversations/<conversation-id>
    python main.py <conversation-id> -o convo.txt
    python main.py <url> --chat-only   # just the user/assistant chat, no tools/code/timestamps
    python main.py <url> --json        # also dump raw events to <out>.events.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_HOST = "https://app.all-hands.dev"
API_CONVERSATION = "/api/shared-conversations"
API_EVENTS = "/api/shared-events/search"
PAGE_LIMIT = 100  # server hard-caps this at 100 (higher values return HTTP 422)
MAX_PAGES = 1000  # safety valve against an endless pagination loop
USER_AGENT = "openhands-shared-conversation-extractor/1.0"

# A bare conversation id is a 32-char lowercase hex string.
ID_RE = re.compile(r"\b([0-9a-f]{16,64})\b", re.IGNORECASE)


class ExtractorError(Exception):
    """Raised for user-facing failures (bad input, network, no data)."""


# --------------------------------------------------------------------------- #
# Input parsing
# --------------------------------------------------------------------------- #
def parse_conversation_id(value: str) -> str:
    """Accept a full shared URL, a path, or a bare id and return the id."""
    value = value.strip()
    if not value:
        raise ExtractorError("Empty conversation reference.")

    # Bare id?
    if re.fullmatch(r"[0-9a-fA-F]{16,64}", value):
        return value.lower()

    # Try as a URL / path and pull the id out of the shared-conversations route.
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    path = parsed.path or value
    match = re.search(r"/shared/conversations/([0-9a-fA-F]{16,64})", path)
    if match:
        return match.group(1).lower()

    # Fall back to the first id-like token anywhere in the string.
    match = ID_RE.search(value)
    if match:
        return match.group(1).lower()

    raise ExtractorError(f"Could not find a conversation id in: {value!r}")


def derive_host(value: str) -> str:
    """Return the origin to query, defaulting to OpenHands Cloud."""
    value = value.strip()
    if "://" not in value:
        return DEFAULT_HOST
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return DEFAULT_HOST


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _get_json(url: str, timeout: float) -> object:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise ExtractorError(
            f"HTTP {exc.code} from {url}\n{detail}\n"
            "The conversation may not be shared publicly, or the id is wrong."
        ) from exc
    except urllib.error.URLError as exc:
        raise ExtractorError(
            f"Network error reaching {url}: {exc.reason}\n"
            "Check your internet connection (this tool needs outbound HTTPS)."
        ) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ExtractorError(
            f"Expected JSON from {url} but got something else. "
            "Is this a shared-conversation URL?"
        ) from exc


def fetch_metadata(host: str, conversation_id: str, timeout: float) -> dict:
    url = f"{host}{API_CONVERSATION}?{urllib.parse.urlencode({'ids': conversation_id})}"
    data = _get_json(url, timeout)
    if not isinstance(data, list) or not data:
        raise ExtractorError(
            "No metadata returned. The conversation is probably not shared publicly "
            "(or has been unshared / deleted)."
        )
    return data[0]


def fetch_events(host: str, conversation_id: str, timeout: float) -> list[dict]:
    events: list[dict] = []
    page_id: str | None = None
    for _ in range(MAX_PAGES):
        params = {"conversation_id": conversation_id, "limit": str(PAGE_LIMIT)}
        if page_id:
            params["page_id"] = page_id
        url = f"{host}{API_EVENTS}?{urllib.parse.urlencode(params)}"
        payload = _get_json(url, timeout)
        if not isinstance(payload, dict) or "items" not in payload:
            raise ExtractorError(f"Unexpected events payload from {url}")
        events.extend(payload["items"])
        page_id = payload.get("next_page_id")
        if not page_id:
            break
    return events


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return ""
    raw = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _indent(text: str, prefix: str = "    ") -> str:
    if text is None:
        return ""
    return "\n".join(prefix + line if line else prefix.rstrip() for line in str(text).splitlines())


def _fence(text: str, lang: str = "") -> str:
    """Wrap text in a code fence, widening it if the text already has fences."""
    text = "" if text is None else str(text)
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}{lang}\n{text}\n{fence}"


def _text_of(content) -> str:
    """Join text parts of an LLM message content list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
            elif block.get("type") == "image":
                parts.append(f"[image: {block.get('image_urls') or block.get('url') or 'attached'}]")
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _reasoning(event: dict) -> str:
    """Collect reasoning/thinking text from an agent event."""
    chunks = []
    rc = event.get("reasoning_content")
    if isinstance(rc, str) and rc.strip():
        chunks.append(rc.strip())
    for key in ("thinking_blocks", "responses_reasoning_item"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            chunks.append(val.strip())
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    txt = item.get("text") or item.get("thinking") or item.get("content")
                    if isinstance(txt, str) and txt.strip():
                        chunks.append(txt.strip())
    return "\n".join(chunks)


def _thought(event: dict) -> str:
    return _text_of(event.get("thought")).strip()


# --------------------------------------------------------------------------- #
# Per-event renderers
# --------------------------------------------------------------------------- #
def _render_action(action: dict | None, event: dict) -> str:
    action = action or {}
    kind = action.get("kind", "Action")
    tool = event.get("tool_name") or ""
    lines = []

    if kind in ("TerminalAction", "ExecuteBashAction"):
        lines.append(f"$ {action.get('command', '')}")
    elif kind in ("FileEditorAction", "StrReplaceEditorAction", "PlanningFileEditorAction"):
        cmd = action.get("command", "")
        path = action.get("path", "")
        lines.append(f"file_editor {cmd} {path}".strip())
        if action.get("old_str") is not None:
            lines.append("--- old_str ---")
            lines.append(action.get("old_str", ""))
        if action.get("new_str") is not None:
            lines.append("--- new_str ---")
            lines.append(action.get("new_str", ""))
        if action.get("file_text") is not None:
            lines.append("--- content ---")
            lines.append(action.get("file_text", ""))
        if action.get("view_range"):
            lines.append(f"view_range: {action['view_range']}")
    elif kind == "MCPToolAction":
        lines.append(f"MCP tool: {tool}")
        lines.append(json.dumps(action.get("data", action), indent=2, ensure_ascii=False))
    elif kind == "ThinkAction":
        lines.append("[think]")
    elif kind == "FinishAction":
        lines.append("[finish]")
        msg = action.get("message") or action.get("final_thought")
        if msg:
            lines.append(str(msg))
    elif kind == "TaskTrackingAction":
        lines.append("[task_tracker]")
        lines.append(json.dumps(action.get("task_list", action), indent=2, ensure_ascii=False))
    else:
        # Unknown action: dump everything except the kind so nothing is lost.
        rest = {k: v for k, v in action.items() if k != "kind"}
        lines.append(f"[{kind}]")
        if rest:
            lines.append(json.dumps(rest, indent=2, ensure_ascii=False))
    return "\n".join(lines)


def _render_observation(obs: dict | None, event: dict) -> str:
    obs = obs or {}
    kind = obs.get("kind", "Observation")
    tool = event.get("tool_name") or ""
    lines = []

    if kind in ("TerminalObservation", "ExecuteBashObservation"):
        content = _text_of(obs.get("content"))
        if content:
            lines.append(content.rstrip())
        meta = obs.get("metadata") or {}
        suffix = meta.get("suffix")
        if suffix and suffix.strip() not in (content or ""):
            lines.append(suffix.rstrip())
    elif kind in ("FileEditorObservation", "StrReplaceEditorObservation", "PlanningFileEditorObservation"):
        content = _text_of(obs.get("content"))
        lines.append(content.rstrip() if content else "[no output]")
        if obs.get("is_error"):
            lines.append("(error)")
    elif kind in ("ThinkObservation", "FinishObservation", "SwitchLLMObservation"):
        content = _text_of(obs.get("content"))
        lines.append(content.rstrip() if content else f"[{kind}]")
    elif kind == "MCPToolObservation":
        content = _text_of(obs.get("content"))
        lines.append(content.rstrip() if content else "[no output]")
    else:
        content = _text_of(obs.get("content"))
        if content:
            lines.append(content.rstrip())
        else:
            rest = {k: v for k, v in obs.items() if k not in ("kind", "content")}
            lines.append(f"[{kind}] {tool}".strip())
            if rest:
                lines.append(json.dumps(rest, indent=2, ensure_ascii=False))
    if obs.get("is_error") and not lines:
        lines.append("(error)")
    return "\n".join(lines).strip() or "[no output]"


def render_event(event: dict, include_system: bool, chat_only: bool = False) -> list[str]:
    """Render one event into a list of output lines (possibly empty)."""
    kind = event.get("kind", "Unknown")
    source = event.get("source", "?")
    ts = _fmt_ts(event.get("timestamp"))
    head = f"[{ts}] {source} · {kind}"

    if chat_only:
        # Keep only the human-readable back-and-forth between user and agent.
        if kind != "MessageEvent":
            return []
        msg = event.get("llm_message") or {}
        role = msg.get("role", source)
        if role not in ("user", "assistant"):
            return []
        text = _text_of(msg.get("content")).strip()
        if not text:
            return []
        label = "User" if role == "user" else "Assistant"
        return [f"{label}:", _indent(text), ""]

    if kind == "SystemPromptEvent":
        if not include_system:
            return []
        text = _text_of((event.get("system_prompt") or {}).get("text"))
        out = [head]
        out.append(_indent(_fence(text, "text")))
        tools = event.get("tools")
        if tools:
            names = [t.get("name") if isinstance(t, dict) else str(t) for t in tools]
            out.append(f"  tools: {', '.join(n for n in names if n)}")
        return out

    if kind == "MessageEvent":
        msg = event.get("llm_message") or {}
        role = msg.get("role", source)
        text = _text_of(msg.get("content"))
        out = [f"[{ts}] {role} · Message"]
        if text.strip():
            out.append(_indent(text.rstrip()))
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                name = (tc.get("name") or (tc.get("function") or {}).get("name")) if isinstance(tc, dict) else None
                args = tc.get("arguments") if isinstance(tc, dict) else None
                out.append(f"  → tool_call: {name}")
                if args:
                    out.append(_indent(str(args), "      "))
        reasoning = _reasoning(event)
        if reasoning:
            out.append("  · reasoning:")
            out.append(_indent(reasoning, "      "))
        return out

    if kind == "ActionEvent":
        out = [head]
        thought = _thought(event)
        if thought:
            out.append("  · thought:")
            out.append(_indent(thought, "      "))
        reasoning = _reasoning(event)
        if reasoning:
            out.append("  · reasoning:")
            out.append(_indent(reasoning, "      "))
        summary = event.get("summary")
        if summary:
            out.append(f"  summary: {summary}")
        out.append("  action:")
        out.append(_indent(_render_action(event.get("action"), event), "      "))
        return out

    if kind == "ObservationEvent":
        out = [head]
        out.append("  observation:")
        out.append(_indent(_render_observation(event.get("observation"), event), "      "))
        return out

    if kind == "AgentErrorEvent":
        out = [head]
        out.append(f"  tool: {event.get('tool_name')}")
        out.append("  error:")
        out.append(_indent(str(event.get("error", "")), "      "))
        cls = event.get("classification")
        if cls:
            out.append(f"  classification: {json.dumps(cls, ensure_ascii=False)}")
        return out

    if kind in ("ConversationErrorEvent", "ServerErrorEvent"):
        out = [head]
        detail = event.get("detail") or event.get("message") or event.get("error")
        if detail:
            out.append(_indent(str(detail)))
        return out

    if kind == "ConversationStateUpdateEvent":
        out = [head]
        key = event.get("key")
        val = event.get("value")
        if key is not None:
            out.append(f"  {key} = {val!r}")
        return out

    if kind == "Condensation":
        forgotten = event.get("forgotten_event_ids") or []
        summary = event.get("summary") or event.get("condensed_summary")
        out = [head, f"  compacted {len(forgotten)} earlier event(s)"]
        if summary:
            out.append("  summary:")
            out.append(_indent(str(summary), "      "))
        if forgotten:
            out.append(f"  forgotten_event_ids: {', '.join(forgotten)}")
        return out

    if kind == "StreamingDeltaEvent":
        return []  # transient streaming noise, superseded by the final message

    if kind == "HookExecutionEvent":
        out = [head]
        out.append(_indent(json.dumps({k: v for k, v in event.items() if k not in ("kind", "id", "timestamp", "source", "parent_id")}, indent=2, ensure_ascii=False)))
        return out

    # Generic fallback so no event is silently dropped.
    out = [head]
    rest = {k: v for k, v in event.items() if k not in ("kind", "id", "timestamp", "source", "parent_id")}
    if rest:
        out.append(_indent(json.dumps(rest, indent=2, ensure_ascii=False)))
    return out


# --------------------------------------------------------------------------- #
# Transcript assembly
# --------------------------------------------------------------------------- #
def build_transcript(meta: dict, events: list[dict], include_system: bool, source_url: str, chat_only: bool = False) -> str:
    created = _fmt_ts(meta.get("created_at"))
    updated = _fmt_ts(meta.get("updated_at"))
    title = meta.get("title") or "(untitled)"
    metrics = meta.get("metrics") or {}
    tokens = metrics.get("accumulated_token_usage") or {}

    header = [
        "=" * 78,
        f"OpenHands Shared Conversation: {title}",
        "=" * 78,
        f"Conversation ID : {meta.get('id', '')}",
        f"Source URL      : {source_url}",
        f"Created         : {created}",
        f"Updated         : {updated}",
        f"Model           : {meta.get('llm_model') or 'unknown'}",
    ]
    if not chat_only:
        header.extend([
            f"Repository      : {meta.get('selected_repository') or '-'}",
            f"Branch          : {meta.get('selected_branch') or '-'}",
            f"Git provider    : {meta.get('git_provider') or '-'}",
            f"Created by      : {meta.get('created_by_user_id') or '-'}",
            f"Events          : {len(events)}",
        ])
        if tokens:
            header.append(
                "Tokens          : "
                f"prompt={tokens.get('prompt_tokens', 0)} "
                f"completion={tokens.get('completion_tokens', 0)} "
                f"reasoning={tokens.get('reasoning_tokens', 0)}"
            )
    else:
        header.append("Mode            : chat only (user + assistant messages)")
    header.append("=" * 78)
    header.append("")

    body = []
    for event in events:
        lines = render_event(event, include_system, chat_only)
        if lines:
            body.extend(lines)
            if not chat_only:
                body.append("")

    return "\n".join(header + body).rstrip() + "\n"


def default_output_path(meta: dict, conversation_id: str, chat_only: bool = False) -> str:
    title = (meta.get("title") or "").strip()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-").lower()
    slug = slug[:60] or conversation_id[:12]
    suffix = "-chat" if chat_only else ""
    return f"conversation-{conversation_id[:12]}-{slug}{suffix}.txt"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def extract(reference: str, out_path: str | None, dump_json: bool, include_system: bool, timeout: float, chat_only: bool = False) -> str:
    conversation_id = parse_conversation_id(reference)
    host = derive_host(reference)
    source_url = f"{host}/canvas/shared/conversations/{conversation_id}"

    meta = fetch_metadata(host, conversation_id, timeout)
    events = fetch_events(host, conversation_id, timeout)
    if not events:
        raise ExtractorError("The conversation has no events to extract.")

    transcript = build_transcript(meta, events, include_system, source_url, chat_only)

    out_path = out_path or default_output_path(meta, conversation_id, chat_only)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(transcript)

    if dump_json:
        with open(out_path + ".events.json", "w", encoding="utf-8") as fh:
            json.dump(events, fh, indent=2, ensure_ascii=False)

    print(f"Extracted {len(events)} events from conversation {conversation_id}")
    print(f"Wrote transcript to {out_path}")
    if dump_json:
        print(f"Wrote raw events to {out_path}.events.json")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract a highly-parsed transcript from a publicly shared OpenHands conversation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("reference", help="Shared conversation URL, path, or 32-char conversation id")
    parser.add_argument("-o", "--out", help="Output .txt path (default: derived from the title)")
    parser.add_argument("--json", action="store_true", help="Also write raw events next to the .txt")
    parser.add_argument("--no-system", action="store_true", help="Skip the system prompt event")
    parser.add_argument(
        "--chat-only",
        action="store_true",
        help="Keep only user/assistant chat: no tool calls, code changes, observations, or timestamps",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds (default 30)")
    args = parser.parse_args(argv)

    try:
        extract(args.reference, args.out, args.json, not args.no_system, args.timeout, args.chat_only)
    except ExtractorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
