# Vendored toolchain sources

`shadowtrace_arena/data/vendor/code-sentinel-3.9.1.zip` is a clean source
snapshot of the MIT-licensed Code Sentinel 3.9.1 project. The deployment scripts extract it into
`.toolchain/code-sentinel` and run its own doctor and Codex injector.

Runtime caches, local configuration, generated targets, reports, logs, journals,
and temporary extraction artifacts are excluded from the snapshot.
