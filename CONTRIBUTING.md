# Contributing

This is a small hardware integration, so practical test reports are valuable.

Please include:

- macOS version
- Python version
- Codex version if available
- Moonside lamp model/name from `codex-moonside-daemon --scan`
- the exact command or state that failed
- relevant lines from `~/.codex-moonside-lamp/daemon.log` or `hook.log`

Before opening a PR:

```bash
python3 -B -m unittest discover -s tests
python3 -B -m py_compile codex_moonside/*.py install_hooks.py
```
