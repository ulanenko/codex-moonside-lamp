#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_DIR = Path("~/.codex-moonside-lamp").expanduser()
CODEX_DIR = Path("~/.codex").expanduser()
VENV_HOOK = ROOT / ".venv" / "bin" / "codex-moonside-hook"
DEFAULT_HOOK_TIMEOUT_SECONDS = 600
EVENT_KEY_BY_NAME = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
}


def parser() -> argparse.ArgumentParser:
    arg_parser = argparse.ArgumentParser(description="Install example config files for codex-moonside-lamp.")
    arg_parser.add_argument("--write-config", action="store_true", help="Write ~/.codex-moonside-lamp/config.json if missing.")
    arg_parser.add_argument("--write-hooks", action="store_true", help="Write ~/.codex/hooks.json if missing.")
    arg_parser.add_argument(
        "--trust-hooks",
        action="store_true",
        help="Add/update ~/.codex/config.toml hooks.state trusted_hash entries for the installed hooks.",
    )
    arg_parser.add_argument("--print-hooks", action="store_true", help="Print example hooks JSON.")
    arg_parser.add_argument("--hook-command", help="Command/path Codex should run for hook updates.")
    arg_parser.add_argument("--force", action="store_true", help="Overwrite generated config files.")
    return arg_parser


def write_if_missing(source: Path, destination: Path, *, force: bool = False) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        return False
    shutil.copyfile(source, destination)
    return True


def default_hook_command() -> str:
    if VENV_HOOK.exists():
        return str(VENV_HOOK)
    resolved = shutil.which("codex-moonside-hook")
    if resolved:
        return resolved
    return str(ROOT / "scripts" / "codex-moonside-hook")


def load_hooks_template(hook_command: str) -> dict:
    command_for_shell = shlex.quote(hook_command)
    with (ROOT / "examples" / "hooks.json").open("r", encoding="utf-8") as f:
        hooks = json.load(f)

    for event_blocks in hooks.get("hooks", {}).values():
        for block in event_blocks:
            for hook in block.get("hooks", []):
                command = hook.get("command")
                if isinstance(command, str) and command.startswith("codex-moonside-hook"):
                    hook["command"] = command.replace("codex-moonside-hook", command_for_shell, 1)
    return hooks


def write_hooks(destination: Path, hook_command: str, *, force: bool = False) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        return False
    hooks = load_hooks_template(hook_command)
    destination.write_text(json.dumps(hooks, indent=2) + os.linesep, encoding="utf-8")
    return True


def normalized_hook_group(event_name: str, block: dict) -> dict:
    event_key = EVENT_KEY_BY_NAME.get(event_name, event_name)
    normalized_hooks = []
    for hook in block.get("hooks", []):
        normalized_hook = dict(hook)
        normalized_hook.setdefault("timeout", DEFAULT_HOOK_TIMEOUT_SECONDS)
        normalized_hook.setdefault("async", False)
        normalized_hooks.append(normalized_hook)

    normalized: dict = {
        "event_name": event_key,
        "hooks": normalized_hooks,
    }
    matcher = block.get("matcher")
    if matcher not in (None, ""):
        normalized["matcher"] = matcher
    return normalized


def hook_trusted_hash(event_name: str, block: dict) -> str:
    normalized = normalized_hook_group(event_name, block)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def trusted_hook_state_entries(hooks: dict, source_path: Path) -> list[tuple[str, str]]:
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


def write_hook_trust(config_path: Path, hooks_path: Path, hooks: dict) -> int:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    entries = trusted_hook_state_entries(hooks, hooks_path)
    config_path.write_text(upsert_hook_trust_entries(config_text, entries), encoding="utf-8")
    return len(entries)


def load_installed_or_template_hooks(hooks_path: Path, hook_command: str) -> dict:
    if hooks_path.exists():
        with hooks_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return load_hooks_template(hook_command)


def main() -> int:
    args = parser().parse_args()
    hook_command = args.hook_command or default_hook_command()

    if args.write_config:
        wrote = write_if_missing(ROOT / "config.example.json", CONFIG_DIR / "config.json", force=args.force)
        print(("Wrote " if wrote else "Already exists: ") + str(CONFIG_DIR / "config.json"))

    if args.write_hooks:
        wrote = write_hooks(CODEX_DIR / "hooks.json", hook_command, force=args.force)
        print(("Wrote " if wrote else "Already exists: ") + str(CODEX_DIR / "hooks.json"))

    if args.trust_hooks:
        hooks_path = CODEX_DIR / "hooks.json"
        hooks = load_installed_or_template_hooks(hooks_path, hook_command)
        count = write_hook_trust(CODEX_DIR / "config.toml", hooks_path, hooks)
        print(f"Trusted {count} hook handler(s) in {CODEX_DIR / 'config.toml'}")

    if args.print_hooks or not (args.write_config or args.write_hooks or args.trust_hooks):
        hooks = load_hooks_template(hook_command)
        print(json.dumps(hooks, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
