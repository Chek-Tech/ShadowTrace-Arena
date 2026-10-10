"""Local relay metrics and a compact status dashboard."""

from __future__ import annotations

import html
import json
import threading
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .memory import MemoryStore
from .product import PRODUCT_NAME, VERSION


@dataclass
class RelaySnapshot:
    requests: int = 0
    failures: int = 0
    rewrites: int = 0
    retries: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    last_path: str = ""
    last_status: int = 0
    last_elapsed_ms: int = 0


class RelayMetrics:
    def __init__(self) -> None:
        self._value = RelaySnapshot()
        self._lock = threading.Lock()

    def record(
        self,
        path: str,
        status: int,
        elapsed_ms: int,
        bytes_in: int,
        bytes_out: int,
        rewritten: bool,
        retries: int = 0,
    ) -> None:
        with self._lock:
            value = self._value
            value.requests += 1
            value.failures += int(status >= 400)
            value.rewrites += int(rewritten)
            value.retries += max(0, int(retries))
            value.bytes_in += bytes_in
            value.bytes_out += bytes_out
            value.last_path = path
            value.last_status = status
            value.last_elapsed_ms = elapsed_ms

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._value)


def _dashboard_html(metrics: dict[str, Any], memory: dict[str, Any]) -> bytes:
    stats = memory.get("stats", {}) if isinstance(memory, dict) else {}
    recent = memory.get("recent", []) if isinstance(memory, dict) else []
    cards = "".join(
        f"<section><b>{html.escape(str(key))}</b><strong>{value}</strong></section>"
        for key, value in (
            ("Requests", metrics.get("requests", 0)),
            ("Failures", metrics.get("failures", 0)),
            ("Continuity", metrics.get("rewrites", 0)),
            ("Retries", metrics.get("retries", 0)),
            ("Learned", stats.get("total", 0)),
        )
    )
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(item.get("time", ""))[-14:]),
            html.escape(str(item.get("category", ""))),
            html.escape(str(item.get("request_preview", ""))[:100]),
        )
        for item in recent[-15:]
        if isinstance(item, dict)
    )
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{PRODUCT_NAME}</title><style>
body{{font:14px system-ui;margin:32px;max-width:980px;color:#18181b}}
h1{{font-size:24px}}main{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}}
section{{border:1px solid #d4d4d8;padding:14px;border-radius:6px}}
section strong{{display:block;font-size:26px;margin-top:8px}}table{{width:100%;margin-top:24px;
border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #e4e4e7;text-align:left}}
@media(max-width:700px){{main{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><h1>{PRODUCT_NAME} <small>{VERSION}</small></h1>
<main>{cards}</main><table><thead><tr><th>Time</th><th>Category</th><th>Request</th>
</tr></thead><tbody>{rows}</tbody></table></body></html>"""
    return document.encode("utf-8")


def build_dashboard_handler(metrics: RelayMetrics, memory: MemoryStore | None):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            snapshot = metrics.snapshot()
            learned = memory.load() if memory else MemoryStore.empty()
            if self.path == "/api/status":
                body = json.dumps(
                    {"product": PRODUCT_NAME, "metrics": snapshot, "memory": learned},
                    ensure_ascii=False,
                ).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            else:
                body = _dashboard_html(snapshot, learned)
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    return DashboardHandler


def start_dashboard(
    host: str,
    port: int,
    metrics: RelayMetrics,
    memory: MemoryStore | None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), build_dashboard_handler(metrics, memory))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
