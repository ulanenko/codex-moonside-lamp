from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_HOOK_TIMEOUT_SECONDS = 600
EVENT_KEY_BY_NAME = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
}


def normalized_hook_group(event_name: str, block: dict[str, Any]) -> dict[str, Any]:
    event_key = EVENT_KEY_BY_NAME.get(event_name, event_name)
    normalized_hooks = []
    for hook in block.get("hooks", []):
        normalized_hook = dict(hook)
        normalized_hook.setdefault("timeout", DEFAULT_HOOK_TIMEOUT_SECONDS)
        normalized_hook.setdefault("async", False)
        normalized_hooks.append(normalized_hook)

    normalized: dict[str, Any] = {
        "event_name": event_key,
        "hooks": normalized_hooks,
    }
    matcher = block.get("matcher")
    if matcher not in (None, ""):
        normalized["matcher"] = matcher
    return normalized


def hook_trusted_hash(event_name: str, block: dict[str, Any]) -> str:
    normalized = normalized_hook_group(event_name, block)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def trusted_hook_state_entries(hooks: dict[str, Any], source_path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    source = str(source_path.expanduser())
    for event_name, blocks in hooks.get("hooks", {}).items():
        event_key = EVENT_KEY_BY_NAME.get(event_name, event_name)
        for block_index, block in enumerate(blocks):
            trusted_hash = hook_trusted_hash(event_name, block)
            for hook_index, _hook in enumerate(block.get("hooks", [])):
                key = f"{source}:{event_key}:{block_index}:{hook_index}"
                entries.append((key, trusted_hash))
    return entries


def trusted_hashes_from_config(config_text: str) -> dict[str, str]:
    trusted: dict[str, str] = {}
    pattern = re.compile(
        r'^\[hooks\.state\."(?P<key>[^"]+)"\]\s*\n'
        r'^\s*trusted_hash\s*=\s*"(?P<hash>[^"]+)"',
        re.MULTILINE,
    )
    for match in pattern.finditer(config_text):
        trusted[match.group("key")] = match.group("hash")
    return trusted


def missing_or_stale_trust_entries(hooks: dict[str, Any], hooks_path: Path, config_text: str) -> list[tuple[str, str, str | None]]:
    trusted = trusted_hashes_from_config(config_text)
    missing: list[tuple[str, str, str | None]] = []
    for key, expected_hash in trusted_hook_state_entries(hooks, hooks_path):
        actual_hash = trusted.get(key)
        if actual_hash != expected_hash:
            missing.append((key, expected_hash, actual_hash))
    return missing


def upsert_hook_trust_entries(config_text: str, entries: list[tuple[str, str]]) -> str:
    updated = config_text.rstrip()
    for key, trusted_hash in entries:
        escaped_key = re.escape(key)
        pattern = (
            rf'(\[hooks\.state\."{escaped_key}"\]\n'
            rf'trusted_hash = ")[^"]*(")'
        )
        replacement = rf"\g<1>{trusted_hash}\2"
        updated, count = re.subn(pattern, replacement, updated)
        if count == 0:
            updated += f'\n\n[hooks.state."{key}"]\ntrusted_hash = "{trusted_hash}"'
    return updated + os.linesep


def write_hook_trust(config_path: Path, hooks_path: Path, hooks: dict[str, Any]) -> int:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    entries = trusted_hook_state_entries(hooks, hooks_path)
    config_path.write_text(upsert_hook_trust_entries(config_text, entries), encoding="utf-8")
    return len(entries)
