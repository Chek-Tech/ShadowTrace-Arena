# Architecture

ShadowTrace Arena separates orchestration from evidence collection.

```text
CLI
├── state.py       deterministic workspace state
├── deployer.py    stable deployment API
├── deployment_engine.py durable deployment and hooks lifecycle
├── secureio.py    race-aware filesystem and directory locks
├── toml_edit.py   formatting-preserving managed TOML edits
├── mcp.py         bounded inspection + optional capability execution
├── capabilities.py 78 integrations routed through four backends
├── relay.py       transparent or continuity-mode API relay
├── continuity.py recursive context and JSON/SSE response processing
├── memory.py      bounded persistent learning records
├── dashboard.py   localhost metrics and recent activity
├── control_center.py desktop deployment and relay controls
├── toolchain.py   bundled Code Sentinel and host-tool deployment
└── doctor.py      read-only product verification
```

## Trust boundaries

The MCP root is the file-system boundary. Relative and absolute paths are
canonicalized before use, including symlink resolution. Paths outside that root
return structured tool errors.

The relay has two explicit modes. Transparent mode prepends packaged context and
streams upstream bytes unchanged. Continuity mode recursively visits instruction
surfaces, classifies response text with 23 product rules, and rewrites matching
JSON or SSE fields while retaining the outer protocol. Authentication headers
remain in memory only for the active request.

Continuity mode buffers a candidate response, classifies it, and appends a new
conversation turn when retry is allowed. Each request retains the original user
history. A normalized response signature stops repeated outputs, while a bounded
attempt counter stops open-ended loops. The final response includes
`X-ShadowTrace-Retries` for observability.

The MCP server always keeps its five file tools inside the configured workspace.
Command capabilities are added only with `--enable-execution`. Their arguments
remain arrays until a backend wrapper is selected. Automatic routing probes WSL,
then Docker, then local execution; SSH requires an explicit target.

The deployer validates the target directory, regular files, product namespace and
manifest before mutation. It rejects symbolic links, Windows reparse points,
hard-linked managed files and paths that escape the Codex directory. File updates
use private staging files, content and identity fingerprints, atomic replacement,
directory persistence, and a canonical cross-process lock.

Install, remove and managed hook restoration create a durable transaction directory
beside the product namespace. Each transaction records a before-state snapshot and
the planned file inventories before publication. Recovery accepts the before state,
the planned state, or a known mixture caused by interruption; unknown external
content blocks rollback and leaves the evidence intact. Multi-home operations lock
participants in canonical order and defer commit until every participant is ready.

By default an active `hooks.json` is isolated before product files are published.
An existing `hooks.json.disabled` is retained in the product namespace and restored
exactly during explicit hook restoration or uninstall. Operators can explicitly
keep hooks active, and that choice is recorded in the deployment manifest.

The TOML editor parses the full document, understands quoted and dotted root keys,
multiline values and CRLF, and rewrites only `model_instructions_file`. Unrelated
model, MCP, plugin and provider settings remain byte-for-byte unchanged.

The toolchain deployer accepts only the packaged Code Sentinel version and safely
extracts each ZIP member after rejecting absolute paths, parent traversal, drive
prefixes, and symbolic links. A version plus archive SHA-256 marker controls reuse;
changed or incomplete runtimes are moved to `.toolchain/backups` before replacement.
The external project must pass its 37-check doctor before its managed AGENTS block
and nine skills are injected. It does not replace the ShadowTrace bridge pointer.

Third-party host tools are profile-driven and installed independently through
Scoop or Homebrew. Package failures remain visible in the final tool-index report
and never turn an incomplete host tool into a claimed capability.

## Distribution boundary

The build configuration includes only `shadowtrace_arena*`. Runtime resources are
stored under package data so source installs and built distributions use the same
bridge, 28 skillpacks, and clean Code Sentinel 3.9.1 vendor archive.
