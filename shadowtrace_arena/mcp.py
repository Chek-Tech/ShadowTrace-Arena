"""A bounded, workspace-scoped MCP server for passive CTF inspection."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .capabilities import (
    CAPABILITIES,
    CAPABILITY_BY_NAME,
    BackendConfig,
    ToolExecutor,
)
from .product import PRODUCT_NAME, VERSION
from .state import inspect_workspace

MAX_HASH_BYTES = 512 * 1024 * 1024
MAX_STRING_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024


class WorkspaceBoundary:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(f"Workspace does not exist: {self.root}")

    def resolve(self, value: str | None = None, *, must_exist: bool = True) -> Path:
        candidate = self.root if not value else Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(f"Path escapes workspace: {value}")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(resolved)
        return resolved


def _json_content(payload: object, *, is_error: bool = False) -> dict[str, object]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "isError": is_error,
    }


class MCPServer:
    def __init__(
        self,
        root: str | Path,
        executor: ToolExecutor | None = None,
    ):
        self.boundary = WorkspaceBoundary(root)
        self.executor = executor
        self._handlers: dict[str, Callable[[dict[str, Any]], object]] = {
            "workspace_status": self._workspace_status,
            "list_artifacts": self._list_artifacts,
            "hash_file": self._hash_file,
            "extract_strings": self._extract_strings,
            "read_text_excerpt": self._read_text_excerpt,
        }

    @property
    def tool_definitions(self) -> list[dict[str, object]]:
        tools: list[dict[str, object]] = [
            {
                "name": "workspace_status",
                "description": "Inspect CTF state files and passive artifact counts.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Workspace-relative directory; defaults to root.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_artifacts",
                "description": "List files inside the configured workspace without executing them.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "hash_file",
                "description": "Calculate SHA-256 or SHA-512 for one workspace file.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "algorithm": {"type": "string", "enum": ["sha256", "sha512"]},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "extract_strings",
                "description": "Extract bounded printable ASCII and UTF-16LE strings from an artifact.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "min_length": {"type": "integer", "minimum": 3, "maximum": 64},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "read_text_excerpt",
                "description": "Read a bounded UTF-8 text excerpt from inside the workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer", "minimum": 1, "maximum": 65536},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        ]
        if self.executor:
            tools.extend(
                {
                    "name": capability.name,
                    "description": capability.description,
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "arguments": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 128,
                            },
                            "timeout": {
                                "type": "number",
                                "minimum": 0.1,
                                "maximum": 3600,
                            },
                        },
                        "additionalProperties": False,
                    },
                }
                for capability in CAPABILITIES
            )
        return tools

    def handle(self, request: dict[str, Any]) -> dict[str, object] | None:
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": PRODUCT_NAME, "version": VERSION},
                    "capabilities": {"tools": {"listChanged": False}},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": self.tool_definitions},
            }
        if method != "tools/call":
            return self._rpc_error(request_id, -32601, f"Unknown method: {method}")

        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = self._handlers.get(str(name))
        if (
            handler is None
            and self.executor is not None
            and str(name) in CAPABILITY_BY_NAME
        ):
            handler = partial(self._execute_capability, str(name))
        if handler is None:
            return self._rpc_error(request_id, -32602, f"Unknown tool: {name}")
        try:
            result = handler(arguments)
            payload = _json_content(result)
        except Exception as exc:  # MCP must return structured tool errors.
            payload = _json_content(
                {"error": type(exc).__name__, "message": str(exc)}, is_error=True
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": payload}

    @staticmethod
    def _rpc_error(request_id: object, code: int, message: str) -> dict[str, object]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    def _workspace_status(self, arguments: dict[str, Any]) -> object:
        path = self.boundary.resolve(arguments.get("path"))
        if not path.is_dir():
            raise NotADirectoryError(path)
        return inspect_workspace(path).to_dict()

    def _list_artifacts(self, arguments: dict[str, Any]) -> object:
        path = self.boundary.resolve(arguments.get("path"))
        if not path.is_dir():
            raise NotADirectoryError(path)
        limit = max(1, min(int(arguments.get("limit", 200)), 1000))
        artifacts: list[dict[str, object]] = []
        for item in sorted(path.rglob("*")):
            if not item.is_file():
                continue
            stat = item.stat()
            artifacts.append(
                {
                    "path": item.relative_to(self.boundary.root).as_posix(),
                    "size": stat.st_size,
                    "suffix": item.suffix.lower(),
                }
            )
            if len(artifacts) >= limit:
                break
        return {"root": str(self.boundary.root), "artifacts": artifacts}

    def _hash_file(self, arguments: dict[str, Any]) -> object:
        path = self.boundary.resolve(str(arguments["path"]))
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size > MAX_HASH_BYTES:
            raise ValueError(f"File exceeds {MAX_HASH_BYTES} byte hashing limit")
        algorithm = str(arguments.get("algorithm", "sha256"))
        if algorithm not in {"sha256", "sha512"}:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}")
        digest = hashlib.new(algorithm)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "path": path.relative_to(self.boundary.root).as_posix(),
            "size": size,
            "algorithm": algorithm,
            "digest": digest.hexdigest(),
        }

    def _extract_strings(self, arguments: dict[str, Any]) -> object:
        path = self.boundary.resolve(str(arguments["path"]))
        if not path.is_file():
            raise FileNotFoundError(path)
        minimum = max(3, min(int(arguments.get("min_length", 4)), 64))
        limit = max(1, min(int(arguments.get("limit", 200)), 2000))
        with path.open("rb") as handle:
            data = handle.read(MAX_STRING_BYTES + 1)
        truncated = len(data) > MAX_STRING_BYTES
        data = data[:MAX_STRING_BYTES]
        ascii_pattern = re.compile(rb"[\x20-\x7e]{" + str(minimum).encode() + rb",}")
        wide_pattern = re.compile(
            rb"(?<![\x20-\x7e])(?:[\x20-\x7e]\x00){"
            + str(minimum).encode()
            + rb",}"
        )
        values: list[str] = []
        seen: set[str] = set()
        for match in ascii_pattern.finditer(data):
            value = match.group().decode("ascii", errors="replace")
            if value not in seen:
                seen.add(value)
                values.append(value)
            if len(values) >= limit:
                break
        if len(values) < limit:
            for match in wide_pattern.finditer(data):
                value = match.group().decode("utf-16le", errors="replace")
                if value not in seen:
                    seen.add(value)
                    values.append(value)
                if len(values) >= limit:
                    break
        return {
            "path": path.relative_to(self.boundary.root).as_posix(),
            "strings": values,
            "truncated_input": truncated,
            "limit": limit,
        }

    def _read_text_excerpt(self, arguments: dict[str, Any]) -> object:
        path = self.boundary.resolve(str(arguments["path"]))
        if not path.is_file():
            raise FileNotFoundError(path)
        maximum = max(1, min(int(arguments.get("max_chars", 8000)), MAX_TEXT_BYTES))
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
        return {
            "path": path.relative_to(self.boundary.root).as_posix(),
            "text": raw[:maximum].decode("utf-8", errors="replace"),
            "truncated": len(raw) > maximum,
        }

    def _execute_capability(self, name: str, arguments: dict[str, Any]) -> object:
        if self.executor is None:
            raise RuntimeError("Command execution is not enabled")
        raw_arguments = arguments.get("arguments", [])
        if not isinstance(raw_arguments, list) or not all(
            isinstance(value, str) for value in raw_arguments
        ):
            raise TypeError("arguments must be an array of strings")
        return self.executor.run(
            name,
            raw_arguments,
            float(arguments.get("timeout", 600.0)),
        )


def stdio_loop(server: MCPServer) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = server.handle(request)
        except json.JSONDecodeError as exc:
            response = MCPServer._rpc_error(None, -32700, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def http_loop(server: MCPServer, host: str, port: int) -> int:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(
                {"product": PRODUCT_NAME, "version": VERSION, "status": "ok"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                response = server.handle(request)
                body = json.dumps(response, ensure_ascii=False).encode()
                self.send_response(200)
            except Exception as exc:
                body = json.dumps(
                    {"error": type(exc).__name__, "message": str(exc)},
                    ensure_ascii=False,
                ).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} MCP server")
    parser.add_argument("--root", default=".", help="Allowed workspace root")
    parser.add_argument("--enable-execution", action="store_true")
    parser.add_argument(
        "--backend",
        choices=("auto", "local", "wsl", "docker", "ssh"),
        default="auto",
    )
    parser.add_argument("--wsl-distro", default="kali-linux")
    parser.add_argument("--container", default="kali-tools")
    parser.add_argument("--ssh-target")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    try:
        executor = None
        if args.enable_execution:
            executor = ToolExecutor(
                BackendConfig(
                    kind=args.backend,
                    wsl_distro=args.wsl_distro,
                    container=args.container,
                    ssh_target=args.ssh_target,
                )
            )
        server = MCPServer(args.root, executor)
    except Exception as exc:
        print(f"MCP startup failed: {exc}", file=sys.stderr)
        return 2
    if args.port:
        return http_loop(server, args.host, args.port)
    return stdio_loop(server)


if __name__ == "__main__":
    raise SystemExit(main())
