from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_STATE_FILE = "/tmp/codex_moonside_state.json"
DEFAULT_PERMISSION_FILE = "/tmp/codex_moonside_permission_pending.json"
DEFAULT_SESSION_LOCK_FILE = "/tmp/codex_moonside_active_session.json"
DEFAULT_CONFIG_PATH = "~/.codex-moonside-lamp/config.json"
DEFAULT_HOOK_LOG_FILE = "~/.codex-moonside-lamp/hook.log"
DEFAULT_DAEMON_LOG_FILE = "~/.codex-moonside-lamp/daemon.log"

VALID_STATES = {
    "idle",
    "working",
    "tool_running",
    "tool_done",
    "attention",
    "error",
    "ambient",
    "off",
}


DEFAULT_CONFIG: dict[str, Any] = {
    "state_file": DEFAULT_STATE_FILE,
    "permission_file": DEFAULT_PERMISSION_FILE,
    "session_lock_file": DEFAULT_SESSION_LOCK_FILE,
    "session_lock_enabled": True,
    "lamp_name_contains": "Moonside",
    "ble_address": None,
    "poll_interval_seconds": 0.2,
    "reconnect_interval_seconds": 3,
    "scan_timeout_seconds": 5,
    "command_delay_seconds": 0.25,
    "command_suffix": "",
    "write_response": True,
    "skip_redundant_commands": True,
    "minimum_attention_seconds": 1.0,
    "ambient_after_idle_seconds": 1800,
    "ambient_state": "ambient",
    "codex_process_tracking_enabled": False,
    "codex_process_names": ["Codex"],
    "codex_process_poll_interval_seconds": 5,
    "codex_process_missing_seconds": 60,
    "codex_process_missing_state": "ambient",
    "codex_process_return_state": "idle",
    "log_file": DEFAULT_DAEMON_LOG_FILE,
    "hook_log_file": DEFAULT_HOOK_LOG_FILE,
    "states": {
        "idle": {
            "commands": ["LEDON", "BRIGH060", "COLOR255120040"],
        },
        "working": {
            "commands": ["LEDON", "BRIGH060", "THEME.BEAT2.255,255,255"],
        },
        "tool_running": {
            "commands": ["LEDON", "BRIGH060", "THEME.BEAT2.000,080,255"],
        },
        "tool_done": {
            "commands": ["LEDON", "BRIGH060", "COLOR000255080"],
            "duration_seconds": 1.5,
            "return_to_previous": True,
            "return_to_state": "idle",
        },
        "attention": {
            "commands": ["LEDON", "BRIGH070", "THEME.GRADIENT1.180,0,255,40,0,120"],
        },
        "error": {
            "commands": ["LEDON", "BRIGH070", "COLOR255000000"],
        },
        "ambient": {
            "commands": ["LEDON", "BRIGH070", "THEME.RAINBOW3.0"],
        },
        "off": {
            "commands": ["LEDOFF"],
        },
    },
}


def expand_path(path: str | None) -> str | None:
    if path is None:
        return None
    return str(Path(path).expanduser())


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config(path: str | None = None) -> dict[str, Any]:
    config_path = Path(expand_path(path or DEFAULT_CONFIG_PATH) or "")
    user_config: dict[str, Any] = {}

    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"Config must be a JSON object: {config_path}")
        user_config = loaded

    config = deep_merge(DEFAULT_CONFIG, user_config)
    config["state_file"] = expand_path(config.get("state_file"))
    config["permission_file"] = expand_path(config.get("permission_file"))
    config["session_lock_file"] = expand_path(config.get("session_lock_file"))
    config["log_file"] = expand_path(config.get("log_file"))
    config["hook_log_file"] = expand_path(config.get("hook_log_file"))
    return config


def commands_for_state(config: dict[str, Any], state: str) -> list[str]:
    state_config = config.get("states", {}).get(state, {})
    commands = state_config.get("commands", [])
    if not isinstance(commands, list):
        raise ValueError(f"commands for state {state!r} must be a list")
    return [str(command) for command in commands]
