import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from shadowtrace_arena.dashboard import RelayMetrics
from shadowtrace_arena.relay import (
    build_handler,
    filtered_headers,
    inject_context,
    prepare_body,
    validate_upstream,
)


def test_inject_context_preserves_existing_instructions() -> None:
    payload = {"instructions": "caller instruction", "input": "hello"}
    assert inject_context(payload, "arena context") is True
    assert payload["instructions"] == "arena context\n\ncaller instruction"


def test_inject_context_prepends_system_message() -> None:
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    inject_context(payload, "arena context")
    assert payload["messages"][0] == {
        "role": "system",
        "content": "arena context",
    }


def test_prepare_body_leaves_non_json_untouched() -> None:
    raw = b"not-json"
    assert prepare_body(raw, "text/plain", "context") == raw


def test_filtered_headers_preserve_auth_but_drop_transport_headers() -> None:
    headers = {
        "Authorization": "Bearer test",
        "Host": "localhost",
        "Content-Length": "12",
        "OpenAI-Beta": "responses=v1",
    }
    filtered = filtered_headers(headers)
    assert filtered == {
        "Authorization": "Bearer test",
        "OpenAI-Beta": "responses=v1",
    }


def test_validate_upstream_rejects_non_http_urls() -> None:
    assert validate_upstream("https://api.example.test/v1") == (
        "https://api.example.test/v1"
    )
    try:
        validate_upstream("file:///tmp/socket")
    except ValueError:
        pass
    else:
        raise AssertionError("Expected non-http upstream rejection")


def test_continuity_retries_upstream_and_returns_real_second_response() -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        calls = 0
        bodies = []

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            type(self).bodies.append(json.loads(self.rfile.read(length)))
            type(self).calls += 1
            text = (
                "I cannot assist with that"
                if type(self).calls == 1
                else "fixture completed result"
            )
            body = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": text}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    metrics = RelayMetrics()
    relay_handler = build_handler(
        f"http://127.0.0.1:{upstream.server_port}",
        "arena context",
        5,
        continuity=True,
        metrics=metrics,
        max_retries=2,
        retry_delay=0,
    )
    relay_server = ThreadingHTTPServer(("127.0.0.1", 0), relay_handler)
    relay_thread = threading.Thread(target=relay_server.serve_forever, daemon=True)
    relay_thread.start()
    try:
        request = json.dumps(
            {"messages": [{"role": "user", "content": "fixture request"}]}
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", relay_server.server_port, timeout=10
        )
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=request,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
    finally:
        relay_server.shutdown()
        relay_server.server_close()
        upstream.shutdown()
        upstream.server_close()

    assert response.status == 200
    assert response.getheader("X-ShadowTrace-Retries") == "1"
    assert payload["choices"][0]["message"]["content"] == "fixture completed result"
    assert UpstreamHandler.calls == 2
    assert "ShadowTrace retry 1" in UpstreamHandler.bodies[1]["messages"][-1]["content"]
    assert metrics.snapshot()["retries"] == 1
