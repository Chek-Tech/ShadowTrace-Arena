import json
import os
import zipfile
from pathlib import Path

import pytest

from shadowtrace_arena import deployer, toolchain


def make_codex_home(tmp_path: Path) -> Path:
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    return home


def test_code_sentinel_vendor_archive_is_complete() -> None:
    assert toolchain.CODE_SENTINEL_ARCHIVE.is_file()
    with zipfile.ZipFile(toolchain.CODE_SENTINEL_ARCHIVE) as bundle:
        names = set(bundle.namelist())

    for required in toolchain.RUNTIME_REQUIRED:
        assert required in names
    assert "ops-lab/ctf-world/docker-compose.yml" in names
    assert not any("__pycache__" in name for name in names)
    assert not any(name.startswith("ops-lab/tmp/") for name in names)


def test_vendor_extraction_rejects_path_escape(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    destination = tmp_path / "runtime"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", "blocked")

    with pytest.raises(toolchain.ToolchainError, match="Unsafe"):
        toolchain._extract_archive(archive, destination)

    assert not (tmp_path / "outside.txt").exists()


def test_scoop_tool_paths_include_package_bins(tmp_path: Path) -> None:
    scoop = tmp_path / "scoop"
    (scoop / "shims").mkdir(parents=True)
    package = scoop / "apps" / "gcc" / "current"
    (package / "bin").mkdir(parents=True)
    (package / "cmd").mkdir()

    paths = toolchain._scoop_tool_paths(tmp_path)

    assert scoop / "shims" in paths
    assert package in paths
    assert package / "bin" in paths
    assert package / "cmd" in paths


def test_tool_environment_includes_virtualenv_scripts() -> None:
    environment = toolchain._tool_environment()
    path_entries = environment["PATH"].split(os.pathsep)

    assert str(Path(toolchain.sys.executable).parent) in path_entries


def test_dual_stack_deployment_preserves_shadowtrace_bridge(tmp_path: Path) -> None:
    home = make_codex_home(tmp_path)
    deployer.install(home)
    runtime = tmp_path / ".toolchain" / "code-sentinel"

    payload = toolchain.deploy(
        profile="none",
        runtime_root=runtime,
        codex_homes=[home],
        bootstrap_package_manager=False,
    )

    assert payload["doctor"] == {"ok": 37, "total": 37, "pass": True}
    assert payload["toolchain"]["index_summary"]["total"] == 89
    assert (runtime / "sentinel.py").is_file()
    assert (runtime / "使用说明.md").is_file()
    assert toolchain.ensure_code_sentinel(runtime)["reused"] is True

    config = (home / "config.toml").read_text(encoding="utf-8")
    assert "shadowtrace-arena/bridge.md" in config
    agents = (home / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- CODE-SENTINEL:BEGIN -->" in agents
    assert "豆包大人" in agents

    metadata = json.loads(
        (home / "code-sentinel.meta.json").read_text(encoding="utf-8")
    )
    assert metadata["CODE_SENTINEL_HOME"] == str(runtime.resolve())
    assert len(metadata["skills"]) == 9
    assert all((home / "skills" / name / "SKILL.md").is_file() for name in metadata["skills"])
