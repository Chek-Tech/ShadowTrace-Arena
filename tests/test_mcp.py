import hashlib
import json
from pathlib import Path

from shadowtrace_arena.mcp import MCPServer


def tool_payload(response: dict[str, object]) -> dict[str, object]:
    result = response["result"]
    text = result["content"][0]["text"]
    return json.loads(text)


def call(server: MCPServer, name: str, arguments: dict[str, object]):
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )


def test_mcp_lists_only_bounded_tools(tmp_path: Path) -> None:
    server = MCPServer(tmp_path)
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {
        "workspace_status",
        "list_artifacts",
        "hash_file",
        "extract_strings",
        "read_text_excerpt",
    }


def test_hash_and_extract_strings(tmp_path: Path) -> None:
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"\x00FLAG{evidence-first}\x00W\x00I\x00D\x00E\x00")
    server = MCPServer(tmp_path)

    hashed = tool_payload(call(server, "hash_file", {"path": "sample.bin"}))
    assert hashed["digest"] == hashlib.sha256(artifact.read_bytes()).hexdigest()

    strings = tool_payload(
        call(
            server,
            "extract_strings",
            {"path": "sample.bin", "min_length": 4, "limit": 20},
        )
    )
    assert "FLAG{evidence-first}" in strings["strings"]
    assert "WIDE" in strings["strings"]


def test_mcp_rejects_workspace_escape(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    server = MCPServer(root)

    response = call(server, "read_text_excerpt", {"path": "../outside.txt"})
    assert response["result"]["isError"] is True
    payload = tool_payload(response)
    assert payload["error"] == "PermissionError"


def test_mcp_rejects_unlisted_hash_algorithm(tmp_path: Path) -> None:
    artifact = tmp_path / "sample.bin"
    artifact.write_bytes(b"sample")
    server = MCPServer(tmp_path)

    response = call(
        server,
        "hash_file",
        {"path": "sample.bin", "algorithm": "md5"},
    )
    assert response["result"]["isError"] is True
    assert tool_payload(response)["error"] == "ValueError"
