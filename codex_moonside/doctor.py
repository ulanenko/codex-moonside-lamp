from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, load_config
from .hooks_config import (
    EVENT_KEY_BY_NAME,
    missing_or_stale_trust_entries,
    trusted_hook_state_entries,
    write_hook_trust,
)


CODEX_DIR = Path("~/.codex").expanduser()
MOONSIDE_CONFIG_PATH = Path(DEFAULT_CONFIG_PATH).expanduser()
DEFAULT_HOOKS_PATH = CODEX_DIR / "hooks.json"
DEFAULT_CODEX_CONFIG_PATH = CODEX_DIR / "config.toml"
MACOS_LAUNCH_AGENT_LABEL = "local.codex-moonside-lamp"
BUNDLED_CODEX_PATH = Path("/Applications/Codex.app/Contents/Resources/codex")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnose Codex Moonside Lamp setup issues.")
    parser.add_argument("--config", default=str(MOONSIDE_CONFIG_PATH), help="Path to codex-moonside-lamp config.json.")
    parser.add_argument("--codex-config", default=str(DEFAULT_CODEX_CONFIG_PATH), help="Path to Codex config.toml.")
    parser.add_argument("--hooks", default=str(DEFAULT_HOOKS_PATH), help="Path to Codex hooks.json.")
    parser.add_argument("--hook-command", help="Hook command to smoke-test instead of reading hooks.json.")
    parser.add_argument("--fix", action="store_true", help="Apply safe local fixes, currently hook trust hashes only.")
    parser.add_argument(
        "--skip-direct-hook-test",
        action="store_true",
        help="Skip the safe hook executable smoke test that writes to a temporary state file.",
    )
    parser.add_argument(
        "--live-codex-test",
        action="store_true",
        help="Run a tiny Codex CLI turn and verify hooks fire. This can spend tokens and briefly change the lamp.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def result(name: str, status: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status=status, detail=detail)


def load_json_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except FileNotFoundError:
        return None, f"missing: {path}"
    except Exception as exc:
        return None, f"could not read {path}: {exc}"
    if not isinstance(loaded, dict):
        return None, f"expected JSON object: {path}"
    return loaded, None


def strip_toml_comment(line: str) -> str:
    in_quote = False
    escaped = False
    chars = []
    for char in line:
        if escaped:
            chars.append(char)
            escaped = False
            continue
        if char == "\\" and in_quote:
            chars.append(char)
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            chars.append(char)
            continue
        if char == "#" and not in_quote:
            break
        chars.append(char)
    return "".join(chars).strip()


def toml_bool_in_section(config_text: str, section: str, key: str) -> bool | None:
    active = False
    for raw_line in config_text.splitlines():
        line = strip_toml_comment(raw_line)
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            active = line == f"[{section}]"
            continue
        if not active or "=" not in line:
            continue
        found_key, value = line.split("=", 1)
        if found_key.strip() != key:
            continue
        value = value.strip().lower()
        if value == "true":
            return True
        if value == "false":
            return False
    return None


def find_codex_binaries() -> list[str]:
    binaries: list[str] = []
    if BUNDLED_CODEX_PATH.exists():
        binaries.append(str(BUNDLED_CODEX_PATH))
    resolved = shutil.which("codex")
    if resolved and resolved not in binaries:
        binaries.append(resolved)
    return binaries


def resolve_codex_binary() -> str | None:
    binaries = find_codex_binaries()
    if binaries:
        return binaries[0]
    return None


def run_command(args: list[str], *, timeout: float = 10, cwd: str | None = None, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        input=input_text,
    )


def check_codex_binary() -> CheckResult:
    binaries = find_codex_binaries()
    if not binaries:
        return result("Codex binary", "warn", "codex not found on PATH and bundled Codex.app binary not found")

    details: list[str] = []
    versions: set[str] = set()
    status = "ok"
    for codex in binaries:
        try:
            completed = run_command([codex, "--version"], timeout=10)
        except Exception as exc:
            details.append(f"{codex} (version check failed: {exc})")
            status = "warn"
            continue
        output = (completed.stdout or completed.stderr).strip()
        version = output.splitlines()[-1] if output else "unknown version"
        details.append(f"{codex} ({version})")
        versions.add(version)
        if completed.returncode != 0:
            status = "warn"
    if len(versions) > 1:
        status = "warn"
        details.append("multiple Codex versions found; live test uses the first binary listed")
    return result("Codex binary", status, "; ".join(details))


def check_codex_config(config_path: Path) -> tuple[list[CheckResult], str]:
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [result("Codex config", "fail", f"missing: {config_path}")], ""
    except Exception as exc:
        return [result("Codex config", "fail", f"could not read {config_path}: {exc}")], ""

    checks = [result("Codex config", "ok", str(config_path))]
    hooks_enabled = toml_bool_in_section(text, "features", "hooks")
    deprecated_enabled = toml_bool_in_section(text, "features", "codex_hooks")
    if hooks_enabled is True:
        checks.append(result("Codex hooks feature", "ok", "[features].hooks = true"))
    elif deprecated_enabled is True:
        checks.append(result("Codex hooks feature", "warn", "only deprecated [features].codex_hooks = true found; use hooks = true"))
    else:
        checks.append(result("Codex hooks feature", "fail", "missing [features].hooks = true"))
    return checks, text


def check_moonside_config(config_path: Path) -> list[CheckResult]:
    try:
        config = load_config(str(config_path))
    except Exception as exc:
        return [result("Moonside config", "fail", f"could not load {config_path}: {exc}")]

    checks = [result("Moonside config", "ok", str(config_path) if config_path.exists() else f"defaults only; missing {config_path}")]
    state_file = config.get("state_file")
    if state_file:
        checks.append(result("State file path", "ok", str(state_file)))
    else:
        checks.append(result("State file path", "fail", "state_file is not configured"))
    return checks


def check_hooks_file(hooks_path: Path) -> tuple[list[CheckResult], dict[str, Any] | None]:
    hooks, error = load_json_file(hooks_path)
    if error:
        return [result("Codex hooks file", "fail", error)], None

    checks = [result("Codex hooks file", "ok", str(hooks_path))]
    configured_events = set(hooks.get("hooks", {}).keys())
    expected_events = set(EVENT_KEY_BY_NAME.keys())
    missing_events = sorted(expected_events - configured_events)
    if missing_events:
        checks.append(result("Expected hook events", "warn", "missing: " + ", ".join(missing_events)))
    else:
        checks.append(result("Expected hook events", "ok", "all expected events configured"))
    return checks, hooks


def check_hook_trust(
    hooks: dict[str, Any] | None,
    hooks_path: Path,
    codex_config_path: Path,
    codex_config_text: str,
    *,
    fix: bool,
) -> CheckResult:
    if hooks is None:
        return result("Hook trust", "fail", "hooks.json could not be loaded")
    if not codex_config_text and not codex_config_path.exists():
        return result("Hook trust", "fail", f"missing Codex config: {codex_config_path}")

    stale = missing_or_stale_trust_entries(hooks, hooks_path, codex_config_text)
    if not stale:
        return result("Hook trust", "ok", f"{len(trusted_hook_state_entries(hooks, hooks_path))} trusted hook handler(s)")
    if fix:
        count = write_hook_trust(codex_config_path, hooks_path, hooks)
        return result("Hook trust", "ok", f"wrote {count} trusted hook handler hash(es) to {codex_config_path}")

    missing_count = sum(1 for _key, _expected, actual in stale if actual is None)
    stale_count = len(stale) - missing_count
    return result("Hook trust", "fail", f"{missing_count} missing and {stale_count} stale trusted hash(es); rerun with --fix")


def iter_hook_commands(hooks: dict[str, Any] | None) -> list[str]:
    if hooks is None:
        return []
    commands: list[str] = []
    for blocks in hooks.get("hooks", {}).values():
        for block in blocks:
            for hook in block.get("hooks", []):
                command = hook.get("command")
                if isinstance(command, str) and command:
                    commands.append(command)
    return commands


def executable_for_command(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts:
        return None
    executable = os.path.expanduser(parts[0])
    if os.path.isabs(executable):
        return executable if os.path.exists(executable) else None
    return shutil.which(executable)


def check_hook_executables(hooks: dict[str, Any] | None) -> CheckResult:
    commands = iter_hook_commands(hooks)
    if not commands:
        return result("Hook executables", "fail", "no command hooks found")
    missing = sorted({command for command in commands if not executable_for_command(command)})
    if missing:
        return result("Hook executables", "fail", "unresolvable command(s): " + "; ".join(missing))
    return result("Hook executables", "ok", f"{len(commands)} command hook(s) resolve")


def pick_hook_command(hooks: dict[str, Any] | None, override: str | None) -> str | None:
    if override:
        return override
    for command in iter_hook_commands(hooks):
        if "codex-moonside-hook" in command:
            return command
    return None


def run_direct_hook_smoke_test(hooks: dict[str, Any] | None, command_override: str | None) -> CheckResult:
    command = pick_hook_command(hooks, command_override)
    if not command:
        return result("Direct hook smoke test", "warn", "no codex-moonside-hook command found")

    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.json"
        config_path = Path(tmpdir) / "config.json"
        hook_log_path = Path(tmpdir) / "hook.log"
        config_path.write_text(
            json.dumps(
                {
                    "state_file": str(state_path),
                    "hook_log_file": str(hook_log_path),
                    "session_lock_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        full_command = (
            f"{command} --state working "
            f"--config {shlex.quote(str(config_path))} "
            f"--state-file {shlex.quote(str(state_path))}"
        )
        try:
            completed = subprocess.run(
                full_command,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            return result("Direct hook smoke test", "fail", f"failed to run hook command: {exc}")
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            return result("Direct hook smoke test", "fail", f"hook exited {completed.returncode}: {stderr}")
        payload, error = load_json_file(state_path)
        if error or payload is None:
            return result("Direct hook smoke test", "fail", f"state file was not written: {error}")
        if payload.get("state") != "working":
            return result("Direct hook smoke test", "fail", f"unexpected state payload: {payload}")
    return result("Direct hook smoke test", "ok", "hook wrote a temporary working state")


def check_launch_agent() -> CheckResult:
    if platform.system() != "Darwin":
        return result("LaunchAgent", "warn", "macOS LaunchAgent check skipped on this OS")
    launchctl = shutil.which("launchctl")
    if not launchctl:
        return result("LaunchAgent", "warn", "launchctl not found")
    uid = os.getuid()
    try:
        completed = run_command(
            [launchctl, "print", f"gui/{uid}/{MACOS_LAUNCH_AGENT_LABEL}"],
            timeout=5,
        )
    except Exception as exc:
        return result("LaunchAgent", "warn", f"launchctl check failed: {exc}")
    if completed.returncode == 0:
        return result("LaunchAgent", "ok", MACOS_LAUNCH_AGENT_LABEL)
    return result("LaunchAgent", "warn", f"{MACOS_LAUNCH_AGENT_LABEL} is not loaded")


def check_recent_logs() -> list[CheckResult]:
    log_dir = Path("~/.codex-moonside-lamp").expanduser()
    checks: list[CheckResult] = []
    for name in ("daemon.log", "hook.log"):
        path = log_dir / name
        if not path.exists():
            checks.append(result(name, "warn", f"missing: {path}"))
            continue
        age_seconds = time.time() - path.stat().st_mtime
        checks.append(result(name, "ok", f"{path} modified {age_seconds:.0f}s ago"))
    return checks


def run_live_codex_test(hooks_log_path: Path | None, cwd: Path) -> CheckResult:
    codex = resolve_codex_binary()
    if not codex:
        return result("Live Codex hook test", "fail", "codex binary not found")
    before_mtime = hooks_log_path.stat().st_mtime if hooks_log_path and hooks_log_path.exists() else 0.0
    try:
        completed = run_command(
            [codex, "exec", "--skip-git-repo-check", "--enable", "hooks", "-C", str(cwd), "Reply with just ok."],
            timeout=90,
            input_text="",
        )
    except Exception as exc:
        return result("Live Codex hook test", "fail", f"Codex CLI run failed: {exc}")
    if completed.returncode != 0:
        stderr = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "no stderr"
        return result("Live Codex hook test", "fail", f"Codex exited {completed.returncode}: {stderr}")
    if hooks_log_path and hooks_log_path.exists() and hooks_log_path.stat().st_mtime > before_mtime:
        return result("Live Codex hook test", "ok", "hook log changed during Codex CLI turn")
    if "hook: UserPromptSubmit" in completed.stdout or "hook: Stop" in completed.stdout:
        return result("Live Codex hook test", "ok", "Codex CLI emitted hook events")
    return result("Live Codex hook test", "warn", "Codex CLI completed, but no hook evidence was observed")


def status_rank(status: str) -> int:
    return {"ok": 0, "warn": 1, "fail": 2}.get(status, 2)


def print_results(checks: list[CheckResult]) -> None:
    labels = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
    for check in checks:
        print(f"[{labels.get(check.status, check.status.upper()):4}] {check.name}: {check.detail}")


def checks_to_json(checks: list[CheckResult]) -> str:
    return json.dumps([check.__dict__ for check in checks], indent=2)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    codex_config_path = Path(args.codex_config).expanduser()
    hooks_path = Path(args.hooks).expanduser()
    moonside_config_path = Path(args.config).expanduser()

    checks: list[CheckResult] = [check_codex_binary()]
    codex_config_checks, codex_config_text = check_codex_config(codex_config_path)
    checks.extend(codex_config_checks)
    checks.extend(check_moonside_config(moonside_config_path))
    hook_file_checks, hooks = check_hooks_file(hooks_path)
    checks.extend(hook_file_checks)
    checks.append(check_hook_trust(hooks, hooks_path, codex_config_path, codex_config_text, fix=args.fix))
    checks.append(check_hook_executables(hooks))
    if not args.skip_direct_hook_test:
        checks.append(run_direct_hook_smoke_test(hooks, args.hook_command))
    checks.append(check_launch_agent())
    checks.extend(check_recent_logs())

    try:
        config = load_config(str(moonside_config_path))
        hook_log_path = Path(str(config.get("hook_log_file"))).expanduser() if config.get("hook_log_file") else None
    except Exception:
        hook_log_path = None

    if args.live_codex_test:
        checks.append(run_live_codex_test(hook_log_path, Path.cwd()))

    if args.json:
        print(checks_to_json(checks))
    else:
        print_results(checks)

    worst = max((status_rank(check.status) for check in checks), default=0)
    return 1 if worst == 2 else 0


if __name__ == "__main__":
    raise SystemExit(main())
