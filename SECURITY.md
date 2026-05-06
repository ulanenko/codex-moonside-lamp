# Security

This project is local-only:

- Codex hooks write small JSON state files under `/tmp`.
- The daemon reads those state files and sends BLE commands to a local lamp.
- No cloud API or external network service is used by the application itself.

Do not put prompts, command output, secrets, or source file contents into state
files or logs. The default hook intentionally stores only event/state metadata.

Report security issues privately to the repository owner once the project is
published.
