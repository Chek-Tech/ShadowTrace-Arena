from pathlib import Path

from shadowtrace_arena.state import initialize_workspace, inspect_workspace


def test_initialize_workspace_is_idempotent(tmp_path: Path) -> None:
    created = initialize_workspace(tmp_path)
    assert len(created) == 5
    progress = tmp_path / "progress.md"
    progress.write_text("custom progress\n", encoding="utf-8")

    assert initialize_workspace(tmp_path) == []
    assert progress.read_text(encoding="utf-8") == "custom progress\n"

    snapshot = inspect_workspace(tmp_path)
    assert snapshot.exists is True
    assert snapshot.state_missing == []
    assert snapshot.file_count == 5


def test_missing_workspace_returns_structured_snapshot(tmp_path: Path) -> None:
    missing = inspect_workspace(tmp_path / "missing")
    assert missing.exists is False
    assert "progress.md" in missing.state_missing

