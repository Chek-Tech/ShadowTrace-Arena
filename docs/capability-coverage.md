# Capability Coverage

This matrix is the release gate for the complete ShadowTrace Arena feature set.
It records observable behavior, its original implementation, and its verification.

| Surface | Product implementation | Verification |
|---|---|---|
| Install, remove, status | Manifest-owned `deployer.py` | deployment round-trip tests |
| Deployment health and conflict | managed file hashes | conflict and repair test |
| Transaction recovery | product-owned transaction record | interrupted update test |
| Durable before-state snapshot | private transaction directory | fault-injection recovery test |
| Known-state recovery gate | before/planned file inventories | unknown-drift blocking test |
| Cross-process operation lock | canonical per-directory lock | deterministic lock implementation |
| Link and node defense | symlink, reparse and hardlink rejection | filesystem attack tests |
| Private transaction storage | POSIX mode / Windows protected DACL | secure I/O tests |
| Multi-home coordination | sorted lock set and deferred commit | participant rollback test |
| Hooks isolation | active-to-disabled guarded publication | two-file round-trip test |
| Hooks restoration | managed backup and exact content restore | explicit/uninstall restore tests |
| Formatting-preserving TOML | quoted/dotted/multiline-aware editor | TOML preservation tests |
| Custom instruction source | guarded UTF-8 `--bridge-file` staging | custom bridge round-trip test |
| Desktop control center | `control_center.py` | import and command routing checks |
| Provider URL discovery | `discover_upstream` | relay configuration test |
| Automatic provider setup | `configure_relay` / `restore_relay` | exact-line round trip |
| Direct API mode | `relay --direct` | CLI parser and relay tests |
| Recursive request context | `continuity.inject_context` | nested payload test |
| 23 response rules | `CONTINUITY_RULES` | exact-count and rewrite tests |
| Automatic continuation request | bounded upstream retry loop | two-response HTTP fixture |
| Retry loop control | response signatures and attempt limit | integration assertions |
| JSON response processing | `rewrite_response` | envelope-preservation test |
| SSE response processing | `rewrite_response` | event and done-marker test |
| Persistent learning | `MemoryStore` | bounded persistence test |
| Terminal activity | relay method/path/status/rule log | relay integration behavior |
| Web dashboard | `dashboard.py` on a selected local port | handler test surface |
| Workspace analysis | five default MCP tools | MCP and boundary tests |
| Complete tool catalog | 78 named integrations | exact-count catalog test |
| Local execution | argv-based `ToolExecutor` | Python execution test |
| WSL execution | explicit WSL wrapper | backend construction test |
| Docker execution | explicit container wrapper | backend construction test |
| SSH execution | batch SSH wrapper | backend construction test |
| Automatic backend selection | WSL, Docker, then local probes | deterministic routing code |
| MCP stdio transport | line-delimited JSON-RPC | existing MCP tests |
| MCP HTTP transport | localhost threaded handler | product endpoint |
| Domain workflows | 28 original skillpacks | doctor and deployment count |
| CTF state continuation | deterministic state contract | state tests |
| Reversible release | namespaced resources and source ZIP | release scan |
| Windows launch diagnostics | Python and Tkinter probes | launcher source check |
| macOS launch diagnostics | PATH/Homebrew/python.org probes | launcher source check |

The capability catalog uses external executables when a workflow requires a
specialized engine. `shadowtrace tools --json` reports the complete catalog and
`shadowtrace tools --available-only` reports what is currently installed locally.
