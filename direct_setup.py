"""Direct-mode compatibility entry point."""

from __future__ import annotations

import sys

from shadowtrace_arena.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args.pop(0) if args else "status"
    mapping = {"apply": "install", "proxy": "relay"}
    command = mapping.get(command, command)
    if command == "relay" and not any(
        item in args for item in ("--upstream", "--direct", "--auto-upstream")
    ):
        args.insert(0, "--direct")
    if command not in {
        "install",
        "remove",
        "status",
        "inspect",
        "recover",
        "repair",
        "hooks-status",
        "hooks-restore",
        "relay",
    }:
        print(
            "Usage: python direct_setup.py "
            "[apply|remove|status|inspect|recover|repair|hooks-status|"
            "hooks-restore|proxy [--direct|--upstream URL]]"
        )
        return 2
    return cli_main([command, *args])


if __name__ == "__main__":
    raise SystemExit(main())
