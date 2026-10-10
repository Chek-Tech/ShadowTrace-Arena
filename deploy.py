"""Compatibility entry point for namespaced Codex deployment."""

from __future__ import annotations

import sys

from shadowtrace_arena.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args.pop(0) if args else "status"
    command = {"apply": "install"}.get(command, command)
    commands = {
        "install",
        "remove",
        "status",
        "inspect",
        "preview-install",
        "preview-remove",
        "recover",
        "repair",
        "discover",
        "hooks-status",
        "hooks-restore",
    }
    if command not in commands:
        print(
            "Usage: python deploy.py "
            "[apply|remove|status|inspect|recover|repair|hooks-status|hooks-restore]"
        )
        return 2
    return cli_main([command, *args])


if __name__ == "__main__":
    raise SystemExit(main())
