# Codex Moonside Menu Bar

Experimental macOS menu bar wrapper for the existing Python daemon. This is an optional helper for users who want a lightweight local UI without changing the core CLI-first workflow.

It does not replace the daemon. It reads the existing config/state/log files and calls the existing project scripts:

- `scripts/install-macos-service`
- `scripts/uninstall-macos-service`
- `.venv/bin/codex-moonside-hook`
- `.venv/bin/codex-moonside-daemon`

## Build In Development

```bash
cd macos/MenuBarApp
Scripts/package-app
```

## Build A Local `.app`

```bash
cd macos/MenuBarApp
Scripts/package-app
open "build/Codex Moonside.app"
```

The packaged app stores the project root path in `Contents/Resources/ProjectRoot.txt`, so it can still call the local daemon scripts.

## What It Does

- shows whether the LaunchAgent daemon appears to be running
- shows the current state from `/tmp/codex_moonside_state.json`
- shows the configured BLE address or lamp name filter
- can test `attention`, `ambient`, and `off` states through `codex-moonside-hook`
- can run BLE scan output through `codex-moonside-daemon --scan`
- can restart or stop the macOS LaunchAgent using the project scripts
- opens the local config and log files

## Limitations

- macOS only
- unsigned local app
- requires the project checkout and `.venv` to remain in place
- does not install itself as a login item yet
- does not manage BLE directly; the Python daemon remains the source of truth
