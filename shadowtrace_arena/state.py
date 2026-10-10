"""CTF workspace state and passive inventory helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

STATE_TEMPLATES = {
    "progress.md": """# Progress\n\n## Current status\n\n- Phase: reconnaissance\n- Last verified action: none\n\n## Next action\n\n- Record the first passive observation.\n""",
    "findings.md": """# Findings\n\n| ID | Confidence | Evidence | Finding |\n|---|---|---|---|\n""",
    "task_plan.md": """# Task plan\n\n- [ ] Inventory the supplied artifacts\n- [ ] Identify the weakest evidence-backed path\n- [ ] Prove one end-to-end path\n- [ ] Record reproduction steps\n""",
    "notes.md": """# Notes\n\nScratchpad for hypotheses. Mark unverified ideas explicitly.\n""",
    "references/project-state.md": """# Project state\n\n## Target\n\n## Supplied artifacts\n\n## Known services\n\n## Constraints\n\n## Artifact hashes\n""",
}

IGNORED_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    exists: bool
    state_present: list[str]
    state_missing: list[str]
    file_count: int
    directory_count: int
    artifact_extensions: dict[str, int]
    newest_mtime: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _walk_files(root: Path, limit: int = 20_000) -> Iterable[Path]:
    seen = 0
    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path
            seen += 1
            if seen >= limit:
                return


def inspect_workspace(path: str | Path) -> WorkspaceSnapshot:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        return WorkspaceSnapshot(
            root=str(root),
            exists=False,
            state_present=[],
            state_missing=list(STATE_TEMPLATES),
            file_count=0,
            directory_count=0,
            artifact_extensions={},
            newest_mtime=None,
        )

    present = [name for name in STATE_TEMPLATES if (root / name).is_file()]
    missing = [name for name in STATE_TEMPLATES if name not in present]
    extension_counts: dict[str, int] = {}
    newest = 0.0
    files = list(_walk_files(root))
    for file_path in files:
        suffix = file_path.suffix.lower() or "<none>"
        extension_counts[suffix] = extension_counts.get(suffix, 0) + 1
        try:
            newest = max(newest, file_path.stat().st_mtime)
        except OSError:
            continue

    directory_count = sum(
        1
        for item in root.rglob("*")
        if item.is_dir() and not any(part in IGNORED_DIRS for part in item.parts)
    )
    newest_iso = (
        datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else None
    )
    return WorkspaceSnapshot(
        root=str(root),
        exists=True,
        state_present=present,
        state_missing=missing,
        file_count=len(files),
        directory_count=directory_count,
        artifact_extensions=dict(sorted(extension_counts.items())),
        newest_mtime=newest_iso,
    )


def initialize_workspace(path: str | Path) -> list[Path]:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for relative, content in STATE_TEMPLATES.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        destination.write_text(content, encoding="utf-8")
        created.append(destination)
    return created

