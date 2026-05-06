from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DEFAULT_PERMISSION_FILE, DEFAULT_SESSION_LOCK_FILE, DEFAULT_STATE_FILE, VALID_STATES


EVENT_STATE_MAP = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    "PreToolUse": "tool_running",
    "PermissionRequest": "attention",
    "PostToolUse": "tool_done",
    "Stop": "idle",
}


def event_to_state(event_name: str | None) -> str | None:
    if not event_name:
        return None
    return EVENT_STATE_MAP.get(event_name)


def extract_event_name(raw_event: dict[str, Any]) -> str | None:
    for key in (
        "event",
        "event_name",
        "eventName",
        "hook_event_name",
        "hookEventName",
        "hook_event",
        "hookEvent",
        "type",
    ):
        value = raw_event.get(key)
        if isinstance(value, str) and value:
            return value

    nested = raw_event.get("event")
    if isinstance(nested, dict):
        return extract_event_name(nested)
    return None


def extract_tool_name(raw_event: dict[str, Any]) -> str | None:
    for key in ("tool", "tool_name", "toolName", "tool_kind", "toolKind", "name"):
        value = raw_event.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested_name = value.get("name") or value.get("tool_name") or value.get("toolName")
            if isinstance(nested_name, str) and nested_name:
                return nested_name
    return None


def extract_session_id(raw_event: dict[str, Any]) -> str | None:
    for key in ("session_id", "sessionId", "session", "conversation_id", "conversationId"):
        value = raw_event.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def extract_turn_id(raw_event: dict[str, Any]) -> str | None:
    value = raw_event.get("turn_id") or raw_event.get("turnId")
    if isinstance(value, str) and value:
        return value
    return None


def extract_tool_use_id(raw_event: dict[str, Any]) -> str | None:
    value = raw_event.get("tool_use_id") or raw_event.get("toolUseId")
    if isinstance(value, str) and value:
        return value
    return None


def extract_cwd(raw_event: dict[str, Any]) -> str | None:
    value = raw_event.get("cwd") or raw_event.get("working_directory") or raw_event.get("workingDirectory")
    if isinstance(value, str) and value:
        return value
    return None


def extract_command_name(raw_event: dict[str, Any]) -> str | None:
    for key in ("command_name", "commandName"):
        value = raw_event.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def build_state_payload(
    state: str,
    *,
    event_name: str | None = None,
    raw_event: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if state not in VALID_STATES:
        raise ValueError(f"Unknown state: {state}")

    raw_event = raw_event or {}
    return {
        "state": state,
        "event": event_name,
        "tool": extract_tool_name(raw_event),
        "command": extract_command_name(raw_event),
        "session_id": extract_session_id(raw_event),
        "turn_id": extract_turn_id(raw_event),
        "tool_use_id": extract_tool_use_id(raw_event),
        "cwd": extract_cwd(raw_event) or cwd or os.getcwd(),
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def parse_hook_event(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Hook payload must be a JSON object")
    return parsed


def payload_from_hook_input(
    stdin_text: str,
    *,
    explicit_state: str | None = None,
    cwd: str | None = None,
) -> dict[str, Any] | None:
    raw_event = parse_hook_event(stdin_text) if stdin_text.strip() else {}
    event_name = extract_event_name(raw_event)
    state = explicit_state or event_to_state(event_name)
    if not state:
        return None
    return build_state_payload(state, event_name=event_name, raw_event=raw_event, cwd=cwd)


def atomic_write_state(payload: dict[str, Any], path: str = DEFAULT_STATE_FILE) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def build_permission_marker(raw_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": extract_event_name(raw_event),
        "tool": extract_tool_name(raw_event),
        "session_id": extract_session_id(raw_event),
        "turn_id": extract_turn_id(raw_event),
        "cwd": extract_cwd(raw_event) or os.getcwd(),
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def write_permission_marker(raw_event: dict[str, Any], path: str = DEFAULT_PERMISSION_FILE) -> None:
    atomic_write_state(build_permission_marker(raw_event), path)


def clear_permission_marker(path: str = DEFAULT_PERMISSION_FILE) -> None:
    try:
        Path(path).expanduser().unlink()
    except FileNotFoundError:
        pass


def read_permission_marker(path: str = DEFAULT_PERMISSION_FILE) -> dict[str, Any] | None:
    try:
        with Path(path).expanduser().open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def permission_marker_matches(marker: dict[str, Any], raw_event: dict[str, Any]) -> bool:
    matched_identity = False

    marker_turn_id = marker.get("turn_id")
    event_turn_id = extract_turn_id(raw_event)
    if marker_turn_id and event_turn_id:
        if marker_turn_id != event_turn_id:
            return False
        matched_identity = True

    marker_session_id = marker.get("session_id")
    event_session_id = extract_session_id(raw_event)
    if marker_session_id and event_session_id:
        if marker_session_id != event_session_id:
            return False
        matched_identity = True

    marker_cwd = marker.get("cwd")
    event_cwd = extract_cwd(raw_event)
    if marker_cwd and event_cwd:
        if marker_cwd != event_cwd:
            return False
        matched_identity = True

    marker_tool = marker.get("tool")
    event_tool = extract_tool_name(raw_event)
    if marker_tool and event_tool and marker_tool != event_tool:
        return False

    return matched_identity


def build_session_lock(raw_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": extract_session_id(raw_event),
        "turn_id": extract_turn_id(raw_event),
        "cwd": extract_cwd(raw_event) or os.getcwd(),
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def write_session_lock(raw_event: dict[str, Any], path: str = DEFAULT_SESSION_LOCK_FILE) -> None:
    atomic_write_state(build_session_lock(raw_event), path)


def clear_session_lock(path: str = DEFAULT_SESSION_LOCK_FILE) -> None:
    try:
        Path(path).expanduser().unlink()
    except FileNotFoundError:
        pass


def read_session_lock(path: str = DEFAULT_SESSION_LOCK_FILE) -> dict[str, Any] | None:
    try:
        with Path(path).expanduser().open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def session_lock_matches(lock: dict[str, Any], raw_event: dict[str, Any]) -> bool:
    lock_session_id = lock.get("session_id")
    event_session_id = extract_session_id(raw_event)
    if lock_session_id and event_session_id:
        return lock_session_id == event_session_id

    lock_turn_id = lock.get("turn_id")
    event_turn_id = extract_turn_id(raw_event)
    if lock_turn_id and event_turn_id:
        return lock_turn_id == event_turn_id

    lock_cwd = lock.get("cwd")
    event_cwd = extract_cwd(raw_event)
    if lock_cwd and event_cwd:
        return lock_cwd == event_cwd

    return not lock_session_id and not lock_turn_id and not lock_cwd


def read_state_file(path: str = DEFAULT_STATE_FILE) -> dict[str, Any] | None:
    try:
        with Path(path).expanduser().open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("state") not in VALID_STATES:
        return None
    return payload
