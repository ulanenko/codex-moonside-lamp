from __future__ import annotations

import argparse
import sys

from .config import VALID_STATES, load_config
from .logging_utils import setup_file_logger
from .state import (
    atomic_write_state,
    clear_permission_marker,
    clear_session_lock,
    extract_cwd,
    extract_event_name,
    parse_hook_event,
    payload_from_hook_input,
    permission_marker_matches,
    read_permission_marker,
    read_session_lock,
    session_lock_matches,
    write_permission_marker,
    write_session_lock,
)


def read_available_stdin() -> str:
    if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write Codex hook state for Moonside lamp daemon.")
    parser.add_argument("--state", choices=sorted(VALID_STATES), help="Explicit state override.")
    parser.add_argument(
        "--permission-resolved-state",
        choices=sorted(VALID_STATES),
        help="For PostToolUse hooks, write this state only if a pending PermissionRequest marker matches.",
    )
    parser.add_argument("--state-file", help="State file path. Overrides config.")
    parser.add_argument("--config", help="Config file path.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser


def session_allowed(config: dict, raw_event: dict, event_name: str | None) -> bool:
    if not config.get("session_lock_enabled", True):
        return True
    if event_name in {"SessionStart"}:
        return True
    if event_name == "UserPromptSubmit" and not read_permission_marker(config.get("permission_file")):
        return True

    lock = read_session_lock(config.get("session_lock_file"))
    if not lock:
        return True
    if event_name == "PermissionRequest":
        lock_cwd = lock.get("cwd")
        event_cwd = extract_cwd(raw_event)
        if lock_cwd and event_cwd and lock_cwd == event_cwd:
            return True
    return session_lock_matches(lock, raw_event)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        logger = setup_file_logger("codex_moonside.hook", config.get("hook_log_file"), debug=args.debug)
        stdin_text = read_available_stdin()
        try:
            raw_event = parse_hook_event(stdin_text) if stdin_text.strip() else {}
        except Exception:
            if args.state:
                logger.exception("Could not parse hook stdin; falling back to explicit state=%s", args.state)
                raw_event = {}
                stdin_text = ""
            else:
                raise

        if args.permission_resolved_state:
            event_name = extract_event_name(raw_event)
            if event_name != "PostToolUse":
                logger.info("Ignoring permission resolver for event=%s", event_name)
                return 0
            marker = read_permission_marker(config.get("permission_file"))
            if not marker or not permission_marker_matches(marker, raw_event):
                logger.info("No matching permission marker for PostToolUse; ignoring")
                return 0
            payload = payload_from_hook_input(stdin_text, explicit_state=args.permission_resolved_state)
            if payload:
                atomic_write_state(payload, args.state_file or config["state_file"])
                clear_permission_marker(config.get("permission_file"))
                logger.info("Permission resolved; wrote state=%s", payload.get("state"))
            return 0

        if extract_event_name(raw_event) == "PermissionRequest":
            if not session_allowed(config, raw_event, "PermissionRequest"):
                logger.info("Ignoring PermissionRequest from inactive session")
                return 0
            write_permission_marker(raw_event, config.get("permission_file"))

        try:
            payload = payload_from_hook_input(stdin_text, explicit_state=args.state)
        except Exception:
            if not args.state:
                raise
            logger.exception("Could not parse hook stdin; falling back to explicit state=%s", args.state)
            payload = payload_from_hook_input("", explicit_state=args.state)

        if not payload:
            logger.info("No mapped state for hook payload; ignoring")
            return 0

        event_name = payload.get("event")
        if not session_allowed(config, raw_event, event_name):
            logger.info("Ignoring state=%s event=%s from inactive session", payload.get("state"), event_name)
            return 0

        if event_name == "UserPromptSubmit":
            write_session_lock(raw_event, config.get("session_lock_file"))

        state_file = args.state_file or config["state_file"]
        atomic_write_state(payload, state_file)
        if event_name == "Stop":
            clear_session_lock(config.get("session_lock_file"))
            clear_permission_marker(config.get("permission_file"))
        logger.info(
            "Wrote state=%s event=%s tool=%s cwd=%s",
            payload.get("state"),
            payload.get("event"),
            payload.get("tool"),
            payload.get("cwd"),
        )
    except Exception as exc:
        try:
            config = load_config(args.config)
            logger = setup_file_logger("codex_moonside.hook", config.get("hook_log_file"), debug=args.debug)
            logger.exception("Hook failed but will exit 0: %s", exc)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
