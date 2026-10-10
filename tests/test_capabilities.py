import sys

from shadowtrace_arena.capabilities import (
    CAPABILITIES,
    BackendConfig,
    ToolExecutor,
)
from shadowtrace_arena.mcp import MCPServer


def test_catalog_has_78_unique_capabilities() -> None:
    assert len(CAPABILITIES) == 78
    assert len({item.name for item in CAPABILITIES}) == 78


def test_execution_enabled_mcp_exposes_catalog_and_passive_tools(tmp_path) -> None:
    server = MCPServer(tmp_path, ToolExecutor(BackendConfig(kind="local")))
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {item["name"] for item in response["result"]["tools"]}
    assert len(names) == 83
    assert {"workspace_status", "nmap", "python_runtime", "command_runtime"} <= names


def test_python_runtime_uses_current_interpreter() -> None:
    executor = ToolExecutor(BackendConfig(kind="local"))
    result = executor.run("python_runtime", ["print('arena-ok')"], timeout=10)
    assert result["returncode"] == 0
    assert result["output"].strip() == "arena-ok"
    assert result["argv"][0] == sys.executable


def test_remote_backends_build_explicit_wrappers() -> None:
    wsl = ToolExecutor(BackendConfig(kind="wsl", wsl_distro="lab-linux"))
    assert wsl.command(CAPABILITIES[0], ["--version"])[:4] == [
        "wsl",
        "-d",
        "lab-linux",
        "--",
    ]
    docker = ToolExecutor(BackendConfig(kind="docker", container="lab-tools"))
    assert docker.command(CAPABILITIES[0], ["--version"])[:3] == [
        "docker",
        "exec",
        "lab-tools",
    ]
    ssh = ToolExecutor(BackendConfig(kind="ssh", ssh_target="lab@example"))
    assert "lab@example" in ssh.command(CAPABILITIES[0], ["--version"])
