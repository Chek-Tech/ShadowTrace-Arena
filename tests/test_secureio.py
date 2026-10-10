import os
from pathlib import Path

import pytest

from shadowtrace_arena.secureio import (
    ConcurrentChangeError,
    FilesystemSafetyError,
    atomic_write_bytes,
    fingerprint,
)


def test_atomic_write_uses_private_regular_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    published = atomic_write_bytes(target, b"one", require_absent=True)
    replaced = atomic_write_bytes(target, b"two", expected=published)

    assert target.read_bytes() == b"two"
    assert replaced.sha256 == fingerprint(target).sha256
    assert replaced.links == 1


def test_absent_publication_never_overwrites_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"external")

    with pytest.raises(ConcurrentChangeError, match="appeared"):
        atomic_write_bytes(target, b"managed", require_absent=True)

    assert target.read_bytes() == b"external"


def test_hard_link_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    alias = tmp_path / "alias.txt"
    target.write_text("data", encoding="utf-8")
    os.link(target, alias)

    with pytest.raises(FilesystemSafetyError, match="Hard-linked"):
        fingerprint(target)


def test_symbolic_link_is_rejected_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    alias = tmp_path / "alias.txt"
    target.write_text("data", encoding="utf-8")
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable for this account")

    with pytest.raises(FilesystemSafetyError, match="Links and reparse"):
        fingerprint(alias)
