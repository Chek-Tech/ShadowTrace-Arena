# Security model

ShadowTrace Arena treats the local challenge directory, API responses, and model
tool arguments as untrusted input.

## Boundaries

- MCP file paths are resolved beneath the configured `--root` directory.
- MCP command integrations are absent by default and require
  `--enable-execution`; enabled commands use a named catalog, argv arrays,
  argument limits, timeouts, and bounded output.
- The relay binds to `127.0.0.1` unless the operator explicitly changes it.
- Transparent relay logs contain metadata only. Opt-in continuity memory stores
  bounded request and response previews plus hashes; authentication headers are
  never persisted.
- Continuity retries are bounded, stop on repeated normalized responses, and never
  retry non-JSON requests that cannot preserve conversation context.
- Deployment owns only `~/.codex/shadowtrace-arena/` and skill directories whose
  names begin with `shadowtrace-arena-` and appear in its manifest.
- Deployment rejects symlinks, Windows reparse points, hard-linked managed files,
  non-regular nodes, path escapes, and unknown product-namespace files.
- Writes use content/identity fingerprints, private staging files, atomic replace,
  and a canonical cross-process lock. Windows staging files receive a protected
  DACL; POSIX staging files use owner-only modes.
- Removal does not restore an entire stale config file. It edits only the root
  instruction key when that key still points to the managed bridge.
- Relay URL setup records and restores only the exact `base_url` line it changed.
- Deployment transactions contain private before-state snapshots and planned
  inventories. Recovery accepts only known states and preserves evidence when an
  unrelated external mutation would otherwise be overwritten.
- Active `hooks.json` is isolated by default. A pre-existing disabled hook is kept
  as a managed backup and restored byte-for-byte by hook restore or uninstall.
- Multi-home writes acquire locks in canonical order and defer commit until every
  participant reaches its verified ready state.

## Reporting a defect

Include the affected version, operating system, minimal reproduction, expected
boundary, observed boundary, and any non-sensitive test artifact needed to
reproduce the issue.
