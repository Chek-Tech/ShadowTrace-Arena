"""Command-line interface for ShadowTrace Arena."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import deployer, doctor, mcp, relay, state, toolchain
from .capabilities import availability
from .product import BRIDGE_SOURCE, PRODUCT_NAME, TAGLINE, VERSION


def _print(payload: object, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
    else:
        print(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shadowtrace",
        description=f"{PRODUCT_NAME} - {TAGLINE}",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--lang", choices=("auto", "zh-CN", "en"), default="auto")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in (
        "install",
        "remove",
        "status",
        "inspect",
        "preview-install",
        "preview-remove",
        "recover",
        "repair",
    ):
        command = sub.add_parser(name)
        command.add_argument("--codex-home", action="append")
        command.add_argument("--all", action="store_true")
        command.add_argument("--json", action="store_true")

    sub.choices["install"].add_argument(
        "--skip-hooks-isolation", action="store_true"
    )
    sub.choices["install"].add_argument("--bridge-file")
    sub.choices["preview-install"].add_argument(
        "--skip-hooks-isolation", action="store_true"
    )
    sub.choices["preview-install"].add_argument("--bridge-file")

    discover_parser = sub.add_parser(
        "discover", help="Discover local Codex configuration directories"
    )
    discover_parser.add_argument("--json", action="store_true")

    hooks_status = sub.add_parser("hooks-status", help="Inspect hooks.json state")
    hooks_status.add_argument("--codex-home")
    hooks_status.add_argument("--json", action="store_true")

    hooks_restore = sub.add_parser(
        "hooks-restore", help="Restore an isolated hooks.json"
    )
    hooks_restore.add_argument("--codex-home")
    hooks_restore.add_argument("--json", action="store_true")

    verify_parser = sub.add_parser("doctor")
    verify_parser.add_argument("--codex-home")
    verify_parser.add_argument("--json", action="store_true")

    init_parser = sub.add_parser("init", help="Initialize evidence state files")
    init_parser.add_argument("path", nargs="?", default=".")
    init_parser.add_argument("--json", action="store_true")

    state_parser = sub.add_parser("state", help="Inspect a CTF workspace")
    state_parser.add_argument("path", nargs="?", default=".")
    state_parser.add_argument("--json", action="store_true")

    mcp_parser = sub.add_parser("mcp", help="Run the workspace-scoped MCP server")
    mcp_parser.add_argument("--root", default=".")
    mcp_parser.add_argument("--enable-execution", action="store_true")
    mcp_parser.add_argument(
        "--backend",
        choices=("auto", "local", "wsl", "docker", "ssh"),
        default="auto",
    )
    mcp_parser.add_argument("--wsl-distro", default="kali-linux")
    mcp_parser.add_argument("--container", default="kali-tools")
    mcp_parser.add_argument("--ssh-target")
    mcp_parser.add_argument("--host", default="127.0.0.1")
    mcp_parser.add_argument("--port", type=int)

    tools_parser = sub.add_parser("tools", help="List capability integrations")
    tools_parser.add_argument("--available-only", action="store_true")
    tools_parser.add_argument("--json", action="store_true")

    toolchain_parser = sub.add_parser(
        "toolchain", help="Deploy bundled Code Sentinel and host security tools"
    )
    toolchain_parser.add_argument(
        "--profile", choices=toolchain.PROFILES, default="full"
    )
    toolchain_parser.add_argument("--runtime-root")
    toolchain_parser.add_argument("--codex-home", action="append")
    toolchain_parser.add_argument("--skip-codex", action="store_true")
    toolchain_parser.add_argument(
        "--no-package-manager-bootstrap", action="store_true"
    )
    toolchain_parser.add_argument("--json", action="store_true")

    configure_parser = sub.add_parser(
        "relay-config", help="Point an existing Codex provider at the local relay"
    )
    configure_parser.add_argument("--codex-home")
    configure_parser.add_argument("--url", default="http://127.0.0.1:8080/v1")
    configure_parser.add_argument("--json", action="store_true")

    restore_parser = sub.add_parser(
        "relay-restore", help="Restore the provider URL changed by relay-config"
    )
    restore_parser.add_argument("--codex-home")
    restore_parser.add_argument("--json", action="store_true")

    relay_parser = sub.add_parser("relay", help="Run the transparent API relay")
    relay_parser.add_argument("--upstream")
    relay_parser.add_argument("--auto-upstream", action="store_true")
    relay_parser.add_argument("--direct", action="store_true")
    relay_parser.add_argument("--codex-home")
    relay_parser.add_argument("--host", default="127.0.0.1")
    relay_parser.add_argument("--port", type=int, default=8080)
    relay_parser.add_argument("--context-file", default=str(BRIDGE_SOURCE))
    relay_parser.add_argument("--no-context", action="store_true")
    relay_parser.add_argument("--timeout", type=float, default=180.0)
    relay_parser.add_argument("--continuity", action="store_true")
    relay_parser.add_argument("--memory-file")
    relay_parser.add_argument("--dashboard-port", type=int)
    relay_parser.add_argument("--max-retries", type=int, default=2)
    relay_parser.add_argument("--retry-delay", type=float, default=0.2)
    relay_parser.add_argument("--configure-codex", action="store_true")
    relay_parser.add_argument("--restore-on-exit", action="store_true")
    return parser


def _single_home(args: argparse.Namespace) -> str | None:
    homes = args.codex_home or []
    if args.all or len(homes) > 1:
        raise ValueError(f"{args.command} accepts exactly one Codex home")
    return homes[0] if homes else None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "install":
        if args.all or len(args.codex_home or []) > 1:
            results = deployer.install_many(
                None if args.all else args.codex_home,
                skip_hooks_isolation=args.skip_hooks_isolation,
                bridge_file=args.bridge_file,
            )
            _print({"results": [item.to_dict() for item in results]}, args.json)
        else:
            result = deployer.install(
                _single_home(args),
                skip_hooks_isolation=args.skip_hooks_isolation,
                bridge_file=args.bridge_file,
            )
            _print(result.to_dict(), args.json)
        return 0
    if args.command == "remove":
        if args.all or len(args.codex_home or []) > 1:
            results = deployer.remove_many(
                None if args.all else args.codex_home
            )
            _print({"results": [item.to_dict() for item in results]}, args.json)
        else:
            result = deployer.remove(_single_home(args))
            _print(result.to_dict(), args.json)
        return 0
    if args.command == "status":
        homes = deployer.discover_codex_homes() if args.all else args.codex_home
        if homes and len(homes) > 1:
            payload = [deployer.status(home).to_dict() for home in homes]
            _print({"results": payload}, args.json)
        else:
            result = deployer.status(homes[0] if homes else None)
            _print(result.to_dict(), args.json)
        return 0
    if args.command == "inspect":
        _print(deployer.inspect_deployment(_single_home(args)).to_dict(), args.json)
        return 0
    if args.command == "preview-install":
        _print(
            deployer.preview_install(
                _single_home(args),
                skip_hooks_isolation=args.skip_hooks_isolation,
                bridge_file=args.bridge_file,
            ),
            args.json,
        )
        return 0
    if args.command == "preview-remove":
        _print(deployer.preview_remove(_single_home(args)), args.json)
        return 0
    if args.command == "recover":
        _print(deployer.recover_transaction(_single_home(args)), args.json)
        return 0
    if args.command == "repair":
        _print(deployer.repair(_single_home(args)).to_dict(), args.json)
        return 0
    if args.command == "discover":
        homes = [str(item) for item in deployer.discover_codex_homes()]
        _print({"count": len(homes), "codex_homes": homes}, args.json)
        return 0
    if args.command == "hooks-status":
        _print(deployer.inspect_hooks(args.codex_home), args.json)
        return 0
    if args.command == "hooks-restore":
        _print(deployer.restore_hooks(args.codex_home), args.json)
        return 0
    if args.command == "doctor":
        payload = doctor.report(args.codex_home)
        _print(payload, args.json)
        return 0 if payload["ok"] else 1
    if args.command == "init":
        created = state.initialize_workspace(args.path)
        payload = {
            "root": str(Path(args.path).expanduser().resolve()),
            "created": [str(path) for path in created],
        }
        _print(payload, args.json)
        return 0
    if args.command == "state":
        payload = state.inspect_workspace(args.path).to_dict()
        _print(payload, args.json)
        return 0 if payload["exists"] else 1
    if args.command == "mcp":
        mcp_args = ["--root", args.root, "--backend", args.backend]
        if args.enable_execution:
            mcp_args.append("--enable-execution")
        mcp_args.extend(["--wsl-distro", args.wsl_distro, "--container", args.container])
        if args.ssh_target:
            mcp_args.extend(["--ssh-target", args.ssh_target])
        if args.port:
            mcp_args.extend(["--host", args.host, "--port", str(args.port)])
        return mcp.main(mcp_args)
    if args.command == "tools":
        payload = availability()
        if args.available_only:
            payload = [item for item in payload if item["available_local"]]
        _print({"count": len(payload), "tools": payload}, args.json)
        return 0
    if args.command == "toolchain":
        emit = None if args.json else lambda message: print(f"[toolchain] {message}")
        payload = toolchain.deploy(
            profile=args.profile,
            runtime_root=args.runtime_root,
            codex_homes=args.codex_home,
            skip_codex=args.skip_codex,
            bootstrap_package_manager=not args.no_package_manager_bootstrap,
            stream_installs=not args.json,
            emit=emit,
        )
        _print(payload if args.json else toolchain.compact_report(payload), args.json)
        return 0
    if args.command == "relay-config":
        _print(deployer.configure_relay(args.codex_home, args.url), args.json)
        return 0
    if args.command == "relay-restore":
        _print(deployer.restore_relay(args.codex_home), args.json)
        return 0
    if args.command == "relay":
        upstream = args.upstream
        if args.direct:
            upstream = "https://api.openai.com"
        elif args.auto_upstream:
            upstream = deployer.discover_upstream(args.codex_home)
        if not upstream:
            parser.error("relay requires --upstream, --direct, or --auto-upstream")
        context_file = None if args.no_context else args.context_file
        memory_file = args.memory_file
        if args.continuity and not memory_file:
            memory_file = str(Path.home() / ".shadowtrace-arena" / "memory.json")
        configured = False
        if args.configure_codex:
            relay_url = f"http://{args.host}:{args.port}/v1"
            result = deployer.configure_relay(args.codex_home, relay_url)
            configured = bool(result.get("configured"))
            _print(result)
        try:
            relay.run(
                upstream,
                args.host,
                args.port,
                context_file,
                args.timeout,
                continuity=args.continuity,
                memory_file=memory_file,
                dashboard_port=args.dashboard_port,
                max_retries=max(0, min(args.max_retries, 8)),
                retry_delay=max(0.0, min(args.retry_delay, 10.0)),
            )
        finally:
            if configured and args.restore_on_exit:
                _print(deployer.restore_relay(args.codex_home))
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
