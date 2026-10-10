# ShadowTrace Arena

> Evidence first. Every claim reproducible.

ShadowTrace Arena is an evidence-first local CTF workspace. It combines resumable
challenge state, bounded artifact inspection, a workspace-scoped MCP server, an
dual-mode relay, 78 named tool integrations, four execution backends, persistent
continuity memory, and namespaced Codex skill deployment.

The desktop control center adds deployment previews, managed-content conflict
detection, repair, hook isolation/restoration, durable transaction recovery, and
relay process controls.

## Quick start

### One-click deployment

On Windows, double-click `deploy-windows.bat`. On macOS, make the launcher
executable once and then double-click `deploy-macos.command`:

```powershell
.\deploy-windows.bat
```

```bash
chmod +x deploy-macos.command
./deploy-macos.command
```

The deployers find Python 3.11+, create `.venv`, install runtime dependencies,
deploy ShadowTrace Arena plus the bundled Code Sentinel 3.9.1 core, inject its
nine skills into every discovered Codex configuration, refresh its 89-entry tool
index, and open the control center. Code Sentinel is extracted into
`.toolchain/code-sentinel`; an external source directory is not required.

The default `full` profile installs host tools through Scoop on Windows or
Homebrew on macOS. `core` installs the common CLI/mobile/reverse tools, while
`none` deploys the complete Code Sentinel core without downloading third-party
applications. Heavy full-profile applications such as Rancher Desktop on Windows,
Docker Desktop on macOS, Wireshark, Ghidra, and IDA Free can require system
approval or a restart. Docker Compose is installed with the full profile.

Use `-NoLaunch -SkipCodex -Toolchain none` on Windows or
`--no-launch --skip-codex --toolchain none` on macOS for a local CLI-only setup.
`-CheckOnly` / `--check` validates prerequisites without changing files.

After deployment, open a new Codex conversation and send exactly `皮卡丘`.
Codex replies `皮卡丘已就位，破甲模式已激活。` and enables the unified
ShadowTrace FULL plus Code Sentinel stack for that conversation. Send
`皮卡丘退下` or `ST-OFF` to deactivate it.

### Manual deployment

```powershell
python -m pip install -e .
python -m shadowtrace_arena doctor
python -m shadowtrace_arena init C:\ctf\chall-01
python deploy.py apply
python -m shadowtrace_arena hooks-status --json
python -m shadowtrace_arena discover --json
python -m shadowtrace_arena install --bridge-file C:\rules\operator.md
python -m shadowtrace_arena toolchain --profile full
```

Deployment rejects symlinks, Windows reparse points, hard-linked managed files,
path escapes, and unknown namespace content. Private before-state snapshots,
planned inventories, atomic file publication, and canonical directory locks make
install, remove, hook restore, and coordinated multi-home changes recoverable.
The TOML editor preserves unrelated content while handling quoted/dotted keys,
multiline values, and CRLF.

Active `hooks.json` is isolated by default and restored exactly on request or
uninstall. Use `--skip-hooks-isolation` only when the existing hook must remain
active. Windows and macOS launchers validate Python 3.11 and Tkinter before opening
the control center.

The MCP server exposes five bounded workspace tools by default. Passing
`--enable-execution` adds 78 named integrations and routes them through an
auto-detected local, WSL, Docker, or explicit SSH backend. HTTP debug transport
is available with `--port`.

The relay listens on `127.0.0.1` by default. Transparent mode preserves upstream
JSON/SSE. Opt-in continuity mode recursively injects context, applies 23 original
response rules, stores bounded learning records, and can expose a local dashboard.
Authentication headers are never persisted.

When a response matches a continuity rule, the relay can append a new conversation
turn and call the upstream again. Duplicate-response detection and a bounded retry
count prevent loops. This client behavior cannot change model weights or guarantee
an outcome against server-side policy.

The source distribution contains ShadowTrace Arena product code, tests,
configuration, documentation, 28 packaged skill resources, and the clean
MIT-licensed Code Sentinel 3.9.1 vendor archive.

Python 3.11+. MIT License.
