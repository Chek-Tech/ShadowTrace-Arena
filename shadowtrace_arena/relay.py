"""OpenAI-compatible relay with transparent and continuity modes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .continuity import (
    build_retry_body,
    extract_response_text,
    inject_context,
    matched_rules,
    response_signature,
    rewrite_response,
)
from .dashboard import RelayMetrics, start_dashboard
from .memory import MemoryStore, categorize
from .product import BRIDGE_SOURCE, PRODUCT_NAME, VERSION

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
MAX_REQUEST_BYTES = 10 * 1024 * 1024


def prepare_body(raw: bytes, content_type: str, context: str | None) -> bytes:
    if not context or "application/json" not in content_type.lower():
        return raw
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw
    if not isinstance(payload, dict):
        return raw
    inject_context(payload, context)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def filtered_headers(headers: Any) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP
    }


def validate_upstream(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Upstream must be an absolute http(s) URL")
    return value.rstrip("/")


def extract_user_text(raw: bytes, content_type: str) -> str:
    if "json" not in content_type.lower():
        return ""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if str(value.get("role", "")).lower() == "user":
                content = value.get("content")
                if isinstance(content, str):
                    values.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            values.append(part["text"])
            for child in value.values():
                visit(child)

    visit(payload)
    return " ".join(values)


def build_handler(
    upstream: str,
    context: str | None,
    timeout: float,
    *,
    continuity: bool = False,
    memory: MemoryStore | None = None,
    metrics: RelayMetrics | None = None,
    max_retries: int = 2,
    retry_delay: float = 0.2,
):
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised by doctor.
        raise RuntimeError("Install dependencies with: pip install -e .") from exc

    client = httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
        follow_redirects=False,
    )

    class RelayHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        server_version = f"ShadowTraceArena/{VERSION}"

        def _health(self) -> None:
            body = json.dumps(
                {
                    "product": PRODUCT_NAME,
                    "version": VERSION,
                    "status": "ok",
                    "continuity": continuity,
                    "max_retries": max_retries,
                    "metrics": metrics.snapshot() if metrics else {},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _relay(self) -> None:
            if self.path == "/healthz":
                self._health()
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "Invalid Content-Length")
                return
            if length > MAX_REQUEST_BYTES:
                self.send_error(413, "Request body too large")
                return

            raw = self.rfile.read(length) if length else b""
            content_type = self.headers.get("Content-Type", "")
            user_text = extract_user_text(raw, content_type)
            body = prepare_body(
                raw,
                content_type,
                context,
            )
            headers = filtered_headers(self.headers)
            target = upstream + self.path
            started = time.monotonic()
            status = 502
            response_started = False
            bytes_out = 0
            rule_ids: list[str] = []
            response_text = ""
            retries = 0
            try:
                if continuity:
                    attempt_body = body
                    signatures: set[str] = set()
                    while True:
                        with client.stream(
                            self.command,
                            target,
                            headers=headers,
                            content=attempt_body if length else None,
                        ) as response:
                            status = response.status_code
                            response_type = response.headers.get("Content-Type", "")
                            original = response.read()
                            response_text = extract_response_text(original)
                            current_rules = matched_rules(response_text)
                            rule_ids.extend(current_rules)
                            signature = response_signature(response_text)
                            retry_body = None
                            if (
                                current_rules
                                and retries < max_retries
                                and signature not in signatures
                            ):
                                retry_body = build_retry_body(
                                    attempt_body,
                                    content_type,
                                    response_text,
                                    retries + 1,
                                )
                            if retry_body is not None:
                                signatures.add(signature)
                                retries += 1
                                attempt_body = retry_body
                                if retry_delay:
                                    time.sleep(retry_delay)
                                continue
                            outgoing, final_rules = rewrite_response(
                                original, response_type
                            )
                            rule_ids.extend(final_rules)
                            rule_ids = sorted(set(rule_ids))
                            response_text = extract_response_text(outgoing)
                            bytes_out = len(outgoing)
                            self.send_response(response.status_code)
                            for key, value in response.headers.items():
                                if key.lower() not in HOP_BY_HOP | {
                                    "content-encoding",
                                }:
                                    self.send_header(key, value)
                            self.send_header("Content-Length", str(len(outgoing)))
                            self.send_header("X-ShadowTrace-Retries", str(retries))
                            self.send_header("Connection", "close")
                            self.end_headers()
                            response_started = True
                            self.wfile.write(outgoing)
                            self.wfile.flush()
                            break
                else:
                    with client.stream(
                        self.command,
                        target,
                        headers=headers,
                        content=body if length else None,
                    ) as response:
                        status = response.status_code
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in HOP_BY_HOP:
                                self.send_header(key, value)
                        self.send_header("Connection", "close")
                        self.end_headers()
                        response_started = True
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            bytes_out += len(chunk)
                            self.wfile.write(chunk)
                            self.wfile.flush()
            except Exception as exc:
                if not response_started and not self.wfile.closed:
                    try:
                        payload = json.dumps(
                            {"error": "upstream_unavailable", "message": str(exc)}
                        ).encode("utf-8")
                        self.send_response(502)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    except OSError:
                        pass
                elif response_started:
                    print(f"upstream stream interrupted: {exc}", file=sys.stderr)
            finally:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if memory and (user_text or response_text):
                    memory.record(
                        categorize(user_text),
                        user_text,
                        response_text,
                        rule_ids,
                    )
                if metrics:
                    metrics.record(
                        self.path,
                        status,
                        elapsed_ms,
                        len(raw),
                        bytes_out,
                        bool(rule_ids),
                        retries,
                    )
                print(
                    f"{self.command} {self.path} -> {status} ({elapsed_ms}ms)"
                    f" retries={retries} continuity={','.join(rule_ids) or 'none'}",
                    file=sys.stderr,
                )
                self.close_connection = True

        do_GET = _relay
        do_POST = _relay
        do_PUT = _relay
        do_PATCH = _relay
        do_DELETE = _relay

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    return RelayHandler


def run(
    upstream: str,
    host: str = "127.0.0.1",
    port: int = 8080,
    context_file: str | Path | None = BRIDGE_SOURCE,
    timeout: float = 180.0,
    *,
    continuity: bool = False,
    memory_file: str | Path | None = None,
    dashboard_port: int | None = None,
    max_retries: int = 2,
    retry_delay: float = 0.2,
) -> None:
    upstream = validate_upstream(upstream)
    context = None
    if context_file:
        context = Path(context_file).read_text(encoding="utf-8")
    memory = MemoryStore(memory_file) if memory_file else None
    metrics = RelayMetrics()
    handler = build_handler(
        upstream,
        context,
        timeout,
        continuity=continuity,
        memory=memory,
        metrics=metrics,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    server = ThreadingHTTPServer((host, port), handler)
    dashboard = None
    if dashboard_port is not None:
        dashboard = start_dashboard(host, dashboard_port, metrics, memory)
        print(
            f"{PRODUCT_NAME} dashboard http://{host}:{dashboard_port}",
            file=sys.stderr,
        )
    print(
        f"{PRODUCT_NAME} relay http://{host}:{port} -> {upstream}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    finally:
        if dashboard:
            dashboard.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} transparent relay")
    parser.add_argument(
        "--upstream",
        default=os.environ.get("SHADOWTRACE_UPSTREAM"),
        help="Upstream API base URL or SHADOWTRACE_UPSTREAM",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--context-file", default=str(BRIDGE_SOURCE))
    parser.add_argument("--no-context", action="store_true")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--continuity", action="store_true")
    parser.add_argument("--memory-file")
    parser.add_argument("--dashboard-port", type=int)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=0.2)
    args = parser.parse_args(argv)
    if not args.upstream:
        parser.error("--upstream or SHADOWTRACE_UPSTREAM is required")
    context_file = None if args.no_context else args.context_file
    try:
        run(
            args.upstream,
            args.host,
            args.port,
            context_file,
            args.timeout,
            continuity=args.continuity,
            memory_file=args.memory_file,
            dashboard_port=args.dashboard_port,
            max_retries=max(0, min(args.max_retries, 8)),
            retry_delay=max(0.0, min(args.retry_delay, 10.0)),
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
