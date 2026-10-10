"""Race-aware filesystem primitives for managed Codex deployments."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator


class FilesystemSafetyError(OSError):
    """Raised when a filesystem node cannot be handled without ambiguity."""


class ConcurrentChangeError(FilesystemSafetyError):
    """Raised when a node changes between inspection and mutation."""


@dataclass(frozen=True)
class Fingerprint:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    links: int
    sha256: str

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> "Fingerprint":
        if not isinstance(value, dict):
            raise FilesystemSafetyError("Fingerprint must be an object")
        expected = {"device", "inode", "mode", "size", "mtime_ns", "links", "sha256"}
        if set(value) != expected:
            raise FilesystemSafetyError("Fingerprint fields are invalid")
        digest = value["sha256"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise FilesystemSafetyError("Fingerprint digest is invalid")
        numbers = {key: value[key] for key in expected - {"sha256"}}
        if any(not isinstance(item, int) or item < 0 for item in numbers.values()):
            raise FilesystemSafetyError("Fingerprint numeric fields are invalid")
        return cls(sha256=digest, **numbers)

    def same_content(self, other: "Fingerprint") -> bool:
        return self.size == other.size and self.sha256 == other.sha256


def _has_reparse_attribute(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & marker)


def _identity(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(file_stat.st_dev),
        int(file_stat.st_ino),
        stat.S_IFMT(file_stat.st_mode),
        int(file_stat.st_size),
        int(file_stat.st_mtime_ns),
    )


def _require_plain_stat(path: Path, *, directory: bool | None = None) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(file_stat.st_mode) or _has_reparse_attribute(file_stat):
        raise FilesystemSafetyError(f"Links and reparse points are not allowed: {path}")
    if directory is True and not stat.S_ISDIR(file_stat.st_mode):
        raise NotADirectoryError(f"Expected a directory: {path}")
    if directory is False and not stat.S_ISREG(file_stat.st_mode):
        raise FilesystemSafetyError(f"Expected a regular file: {path}")
    return file_stat


def require_plain_directory(path: str | Path) -> Path:
    candidate = Path(path).expanduser().absolute()
    _require_plain_stat(candidate, directory=True)
    return candidate


def require_regular_file(path: str | Path) -> Path:
    candidate = Path(path).expanduser().absolute()
    _require_plain_stat(candidate, directory=False)
    return candidate


def _open_regular(path: Path) -> tuple[int, os.stat_result]:
    before = _require_plain_stat(path, directory=False)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise FilesystemSafetyError(f"Opened node is not a regular file: {path}")
        if _identity(before) != _identity(opened):
            raise ConcurrentChangeError(f"File changed while opening: {path}")
        if opened.st_nlink != 1:
            raise FilesystemSafetyError(f"Hard-linked files are not accepted: {path}")
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def read_regular_bytes(
    path: str | Path,
    expected: Fingerprint | None = None,
) -> tuple[bytes, Fingerprint]:
    candidate = Path(path).absolute()
    descriptor, opened = _open_regular(candidate)
    try:
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _identity(opened) != _identity(after):
            raise ConcurrentChangeError(f"File changed while reading: {candidate}")
        result = Fingerprint(
            device=int(after.st_dev),
            inode=int(after.st_ino),
            mode=int(after.st_mode),
            size=int(after.st_size),
            mtime_ns=int(after.st_mtime_ns),
            links=int(after.st_nlink),
            sha256=digest.hexdigest(),
        )
        if expected is not None and result != expected:
            raise ConcurrentChangeError(f"File fingerprint changed: {candidate}")
        return b"".join(chunks), result
    finally:
        os.close(descriptor)


def read_regular_text(
    path: str | Path,
    expected: Fingerprint | None = None,
) -> tuple[str, Fingerprint]:
    content, fingerprint = read_regular_bytes(path, expected)
    try:
        return content.decode("utf-8"), fingerprint
    except UnicodeDecodeError as exc:
        raise FilesystemSafetyError(f"File is not UTF-8: {path}") from exc


def fingerprint(path: str | Path) -> Fingerprint:
    return read_regular_bytes(path)[1]


def fingerprint_or_none(path: str | Path) -> Fingerprint | None:
    try:
        return fingerprint(path)
    except FileNotFoundError:
        return None


def _apply_windows_private_acl(path: Path) -> None:
    if os.name != "nt":
        return
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = ctypes.c_int
    set_security = advapi32.SetFileSecurityW
    set_security.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    set_security.restype = ctypes.c_int
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    # Owner, Administrators, and SYSTEM retain full control; inheritance is disabled.
    sddl = "D:P(A;;FA;;;OW)(A;;FA;;;BA)(A;;FA;;;SY)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise FilesystemSafetyError(
            f"Cannot create a private Windows security descriptor: {ctypes.get_last_error()}"
        )
    try:
        dacl_security_information = 0x00000004 | 0x80000000
        if not set_security(str(path), dacl_security_information, descriptor):
            raise FilesystemSafetyError(
                f"Cannot apply a private Windows ACL to {path}: {ctypes.get_last_error()}"
            )
    finally:
        local_free(descriptor)


def apply_private_permissions(path: str | Path) -> None:
    candidate = Path(path)
    os.chmod(candidate, 0o700 if candidate.is_dir() else 0o600)
    _apply_windows_private_acl(candidate)


def private_directory(path: str | Path) -> Path:
    candidate = Path(path).absolute()
    candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        apply_private_permissions(candidate)
        _require_plain_stat(candidate, directory=True)
    except BaseException:
        with contextlib.suppress(OSError):
            candidate.rmdir()
        raise
    return candidate


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_expected(path: Path, expected: Fingerprint | None, absent: bool) -> None:
    current = fingerprint_or_none(path)
    if absent:
        if current is not None:
            raise ConcurrentChangeError(f"Target appeared after preflight: {path}")
        return
    if expected is not None and current != expected:
        raise ConcurrentChangeError(f"Target changed after preflight: {path}")


def _publish_no_replace(source: Path, destination: Path) -> None:
    try:
        if os.name == "nt":
            os.rename(source, destination)
        else:
            os.link(source, destination, follow_symlinks=False)
            source.unlink()
    except FileExistsError as exc:
        raise ConcurrentChangeError(
            f"Target appeared during publication: {destination}"
        ) from exc


def atomic_write_bytes(
    path: str | Path,
    content: bytes,
    *,
    expected: Fingerprint | None = None,
    require_absent: bool = False,
    private: bool = True,
) -> Fingerprint:
    target = Path(path).absolute()
    parent = require_plain_directory(target.parent)
    _verify_expected(target, expected, require_absent)
    temporary = parent / f".{target.name}.shadow-{secrets.token_hex(12)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = -1
        if private:
            apply_private_permissions(temporary)
        prepared = fingerprint(temporary)
        _verify_expected(target, expected, require_absent)
        if require_absent:
            _publish_no_replace(temporary, target)
        else:
            os.replace(temporary, target)
        _fsync_directory(parent)
        published = fingerprint(target)
        if not prepared.same_content(published):
            raise ConcurrentChangeError(f"Published content differs from staging file: {target}")
        return published
    finally:
        if descriptor >= 0:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary.unlink()


def atomic_write_text(
    path: str | Path,
    content: str,
    **kwargs: object,
) -> Fingerprint:
    return atomic_write_bytes(path, content.encode("utf-8"), **kwargs)


def atomic_write_json(
    path: str | Path,
    payload: object,
    **kwargs: object,
) -> Fingerprint:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return atomic_write_text(path, encoded, **kwargs)


def _ensure_within(path: Path, root: Path) -> None:
    candidate = path.absolute()
    boundary = root.absolute()
    if candidate == boundary or boundary not in candidate.parents:
        raise FilesystemSafetyError(f"Managed path escapes its boundary: {candidate}")


def _plain_tree_entries(root: Path) -> Iterator[Path]:
    _require_plain_stat(root, directory=True)
    with os.scandir(root) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        path = root / name
        node = path.lstat()
        if stat.S_ISLNK(node.st_mode) or _has_reparse_attribute(node):
            raise FilesystemSafetyError(f"Unsafe node in managed tree: {path}")
        if stat.S_ISDIR(node.st_mode):
            yield from _plain_tree_entries(path)
            yield path
        elif stat.S_ISREG(node.st_mode):
            if node.st_nlink != 1:
                raise FilesystemSafetyError(f"Hard-linked file in managed tree: {path}")
            yield path
        else:
            raise FilesystemSafetyError(f"Unsupported node in managed tree: {path}")


def safe_remove_tree(path: str | Path, boundary: str | Path) -> None:
    target = Path(path).absolute()
    root = Path(boundary).absolute()
    _ensure_within(target, root)
    try:
        _require_plain_stat(target, directory=True)
    except FileNotFoundError:
        return
    entries = list(_plain_tree_entries(target))
    for item in entries:
        node = item.lstat()
        if stat.S_ISDIR(node.st_mode):
            item.rmdir()
        else:
            item.unlink()
    target.rmdir()
    _fsync_directory(target.parent)


def copy_regular_file(source: str | Path, destination: str | Path) -> Fingerprint:
    content, _ = read_regular_bytes(source)
    target = Path(destination).absolute()
    require_plain_directory(target.parent)
    return atomic_write_bytes(target, content, require_absent=True)


def copy_plain_tree(source: str | Path, destination: str | Path) -> None:
    source_root = require_plain_directory(source)
    target_root = private_directory(destination)
    try:
        for current_root, directories, files in os.walk(source_root, followlinks=False):
            current = Path(current_root)
            relative = current.relative_to(source_root)
            target = target_root / relative
            if relative != Path("."):
                target.mkdir(mode=0o700, exist_ok=False)
                apply_private_permissions(target)
            for name in list(directories):
                _require_plain_stat(current / name, directory=True)
            for name in files:
                copy_regular_file(current / name, target / name)
    except BaseException:
        with contextlib.suppress(OSError):
            safe_remove_tree(target_root, target_root.parent)
        raise


def tree_digest(path: str | Path) -> str | None:
    root = Path(path).absolute()
    if not root.exists():
        return None
    digest = hashlib.sha256()
    _require_plain_stat(root, directory=True)
    files = [item for item in _plain_tree_entries(root) if item.is_file()]
    for item in sorted(files):
        relative = item.relative_to(root).as_posix().encode("utf-8")
        content, _ = read_regular_bytes(item)
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def tree_inventory(path: str | Path) -> dict[str, str] | None:
    root = Path(path).absolute()
    if not root.exists():
        return None
    _require_plain_stat(root, directory=True)
    result: dict[str, str] = {}
    files = [item for item in _plain_tree_entries(root) if item.is_file()]
    for item in sorted(files):
        result[item.relative_to(root).as_posix()] = fingerprint(item).sha256
    return result


class DirectoryLock:
    def __init__(self, directory: str | Path):
        self.directory = require_plain_directory(directory)
        key = os.path.normcase(str(self.directory.resolve(strict=True))).encode("utf-8")
        digest = hashlib.sha256(key).hexdigest()
        self.lock_root = Path(tempfile.gettempdir()) / "shadowtrace-arena-locks"
        self.lock_path = self.lock_root / f"{digest}.lock"
        self.handle: BinaryIO | None = None
        self.identity = _require_plain_stat(self.directory, directory=True)

    def __enter__(self) -> "DirectoryLock":
        self.lock_root.mkdir(mode=0o700, exist_ok=True)
        require_plain_directory(self.lock_root)
        apply_private_permissions(self.lock_root)
        before = fingerprint_or_none(self.lock_path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        current = self.lock_path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or _has_reparse_attribute(current)
            or _identity(opened) != _identity(current)
            or opened.st_nlink != 1
        ):
            os.close(descriptor)
            raise FilesystemSafetyError(f"Unsafe deployment lock: {self.lock_path}")
        if before is not None and (
            before.device,
            before.inode,
            before.size,
            before.mtime_ns,
        ) != (
            int(opened.st_dev),
            int(opened.st_ino),
            int(opened.st_size),
            int(opened.st_mtime_ns),
        ):
            os.close(descriptor)
            raise ConcurrentChangeError(f"Deployment lock changed: {self.lock_path}")
        self.handle = os.fdopen(descriptor, "r+b")
        if self.lock_path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        apply_private_permissions(self.lock_path)
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        current = _require_plain_stat(self.directory, directory=True)
        if _identity(current) != _identity(self.identity):
            self.__exit__(None, None, None)
            raise ConcurrentChangeError(f"Directory changed while locking: {self.directory}")
        return self

    def __exit__(self, *_args: object) -> None:
        if self.handle is None:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            with contextlib.suppress(OSError):
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            with contextlib.suppress(OSError):
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


class DirectoryLockSet:
    def __init__(self, directories: Iterable[str | Path]):
        unique = {
            os.path.normcase(str(require_plain_directory(item).resolve(strict=True))): Path(item)
            for item in directories
        }
        self.locks = [DirectoryLock(unique[key]) for key in sorted(unique)]

    def __enter__(self) -> "DirectoryLockSet":
        acquired: list[DirectoryLock] = []
        try:
            for lock in self.locks:
                lock.__enter__()
                acquired.append(lock)
        except BaseException:
            for lock in reversed(acquired):
                lock.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        for lock in reversed(self.locks):
            lock.__exit__(None, None, None)
