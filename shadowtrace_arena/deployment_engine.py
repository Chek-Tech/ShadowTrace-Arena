"""Original transactional deployment engine for ShadowTrace Arena."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .product import BRIDGE_SOURCE, PRODUCT_SLUG, SKILLPACK_SOURCE, VERSION
from .secureio import (
    ConcurrentChangeError,
    DirectoryLock,
    DirectoryLockSet,
    FilesystemSafetyError,
    Fingerprint,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    copy_plain_tree,
    copy_regular_file,
    fingerprint,
    fingerprint_or_none,
    private_directory,
    read_regular_bytes,
    read_regular_text,
    require_plain_directory,
    require_regular_file,
    safe_remove_tree,
    tree_inventory,
)
from .toml_edit import TomlEditError, find_root_string, set_root_string


ROOT_KEY = "model_instructions_file"
MANIFEST_SCHEMA = 2
TRANSACTION_SCHEMA = 2
TRANSACTION_PREFIX = f".{PRODUCT_SLUG}-txn-"
LEGACY_TRANSACTION = "transaction.json"
HOOKS_ACTIVE = "hooks.json"
HOOKS_DISABLED = "hooks.json.disabled"
HOOKS_BACKUP = "hooks.disabled.before"
MANIFEST_NAME = "manifest.json"
RELAY_RECORD = "relay-config.json"
SKILL_RE = re.compile(rf"^{re.escape(PRODUCT_SLUG)}-[a-z0-9][a-z0-9-]*$")
BASE_URL_RE = re.compile(r'^(\s*base_url\s*=\s*)"([^"]+)"(\s*(?:#.*)?)$')


class DeploymentError(RuntimeError):
    """Base error for deployment state and ownership failures."""


class DeploymentConflict(DeploymentError):
    """Raised when managed state has drifted or ownership is ambiguous."""


class RecoveryRequired(DeploymentError):
    """Raised when an interrupted transaction must be handled first."""


@dataclass(frozen=True)
class DeploymentResult:
    action: str
    codex_home: str
    installed: bool
    skills: list[str]
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "codex_home": self.codex_home,
            "installed": self.installed,
            "skills": self.skills,
            "message": self.message,
        }


@dataclass(frozen=True)
class DeploymentInspection:
    state: str
    codex_home: str
    issues: list[str]
    skills: list[str]
    hooks: dict[str, object] = field(default_factory=dict)
    transactions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "codex_home": self.codex_home,
            "issues": self.issues,
            "skills": self.skills,
            "hooks": self.hooks,
            "transactions": self.transactions,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _product_root(home: Path) -> Path:
    return home / PRODUCT_SLUG


def _manifest_path(home: Path) -> Path:
    return _product_root(home) / MANIFEST_NAME


def _bridge_path(home: Path) -> Path:
    return _product_root(home) / "bridge.md"


def _relay_manifest_path(home: Path) -> Path:
    return _product_root(home) / RELAY_RECORD


def _legacy_transaction_path(home: Path) -> Path:
    return _product_root(home) / LEGACY_TRANSACTION


def _hook_path(home: Path, name: str) -> Path:
    return home / name


def _candidate_homes() -> list[Path]:
    candidates: list[Path] = []
    if os.environ.get("CODEX_HOME"):
        candidates.append(Path(os.environ["CODEX_HOME"]).expanduser())
    candidates.append(Path.home() / ".codex")
    if os.name == "nt":
        if os.environ.get("USERPROFILE"):
            candidates.append(Path(os.environ["USERPROFILE"]) / ".codex")
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex")
    else:
        candidates.append(Path("/root/.codex"))
    return candidates


def discover_codex_homes() -> list[Path]:
    found: dict[str, Path] = {}
    for candidate in _candidate_homes():
        try:
            root = require_plain_directory(candidate)
            config = root / "config.toml"
            if config.exists():
                fingerprint(config)
            elif not _transaction_directories(root):
                continue
        except OSError:
            continue
        found[os.path.normcase(str(root.resolve(strict=True)))] = root
    return [found[key] for key in sorted(found)]


def locate_codex_home(explicit: str | Path | None = None) -> Path | None:
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        try:
            root = require_plain_directory(candidate)
            fingerprint(root / "config.toml")
            return root
        except (FileNotFoundError, NotADirectoryError):
            return None
    homes = discover_codex_homes()
    return homes[0] if homes else None


def _resolve_home(explicit: str | Path | None) -> Path:
    home = locate_codex_home(explicit)
    if home is None:
        raise FileNotFoundError("Codex config.toml not found")
    return home


def _relative_path(home: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise DeploymentConflict("Managed path must be a non-empty string")
    portable = PurePosixPath(value)
    if portable.is_absolute() or ".." in portable.parts:
        raise DeploymentConflict(f"Managed path escapes Codex home: {value}")
    candidate = home.joinpath(*portable.parts).absolute()
    if candidate == home or home not in candidate.parents:
        raise DeploymentConflict(f"Managed path escapes Codex home: {value}")
    return candidate


def _skill_name(value: object) -> str:
    if not isinstance(value, str) or not SKILL_RE.fullmatch(value):
        raise DeploymentConflict(f"Invalid managed skill name: {value!r}")
    return value


def _fingerprint_dict(value: object, *, optional: bool = False) -> Fingerprint | None:
    if value is None and optional:
        return None
    return Fingerprint.from_dict(value)


def _normalize_manifest(raw: object, home: Path) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise DeploymentConflict("Deployment manifest must be an object")
    if raw.get("product") != PRODUCT_SLUG:
        raise DeploymentConflict("Deployment manifest has a different owner")

    schema = raw.get("schema", 1)
    if schema not in {1, MANIFEST_SCHEMA}:
        raise DeploymentConflict(f"Unsupported deployment manifest schema: {schema}")
    skills_value = raw.get("installed_skills")
    if not isinstance(skills_value, list):
        raise DeploymentConflict("Deployment manifest skill list is invalid")
    skills = [_skill_name(item) for item in skills_value]
    if len(skills) != len(set(skills)):
        raise DeploymentConflict("Deployment manifest contains duplicate skills")

    managed_value = raw.get("managed_files")
    if not isinstance(managed_value, dict) or not managed_value:
        raise DeploymentConflict("Deployment manifest managed hashes are invalid")
    managed: dict[str, str] = {}
    for relative, digest in managed_value.items():
        _relative_path(home, relative)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DeploymentConflict(f"Invalid managed digest: {relative}")
        managed[str(relative)] = digest

    previous = raw.get("previous_instruction")
    if previous is None and isinstance(raw.get("previous_instruction_line"), str):
        legacy_line = str(raw["previous_instruction_line"])
        match = re.match(r'^\s*model_instructions_file\s*=\s*"(.*)"\s*$', legacy_line)
        previous = match.group(1) if match else None
    if previous is not None and not isinstance(previous, str):
        raise DeploymentConflict("Previous instruction reference is invalid")

    hooks = raw.get("hooks")
    if schema == 1 or not isinstance(hooks, dict):
        hooks = {
            "mode": "legacy",
            "active_before": None,
            "disabled_before": None,
            "isolated": None,
            "backup": None,
            "backup_sha256": None,
        }
    else:
        required = {
            "mode",
            "active_before",
            "disabled_before",
            "isolated",
            "backup",
            "backup_sha256",
        }
        if set(hooks) != required:
            raise DeploymentConflict("Manifest hook record fields are invalid")
        if hooks["mode"] not in {"isolated", "absent", "skipped", "restored", "legacy"}:
            raise DeploymentConflict("Manifest hook mode is invalid")
        for key in ("active_before", "disabled_before", "isolated"):
            _fingerprint_dict(hooks[key], optional=True)
        if hooks["backup"] is not None:
            backup = _relative_path(home, hooks["backup"])
            if backup.parent != _product_root(home):
                raise DeploymentConflict("Hook backup path is outside the product root")
            if not isinstance(hooks["backup_sha256"], str):
                raise DeploymentConflict("Hook backup hash is invalid")

    return {
        "schema": MANIFEST_SCHEMA,
        "product": PRODUCT_SLUG,
        "version": str(raw.get("version", "unknown")),
        "installed_at": str(raw.get("installed_at", "")),
        "config_path": str(raw.get("config_path", home / "config.toml")),
        "bridge_path": str(raw.get("bridge_path", _bridge_path(home))),
        "previous_instruction": previous,
        "previous_instruction_line": (
            f'{ROOT_KEY} = {json.dumps(previous, ensure_ascii=False)}'
            if previous is not None
            else None
        ),
        "installed_skills": skills,
        "managed_files": managed,
        "hooks": hooks,
    }


def _load_manifest(home: Path) -> dict[str, object] | None:
    path = _manifest_path(home)
    if not path.exists():
        return None
    content, _ = read_regular_text(path)
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeploymentConflict("Deployment manifest is not valid JSON") from exc
    return _normalize_manifest(raw, home)


def _transaction_directories(home: Path) -> list[Path]:
    result: list[Path] = []
    with os.scandir(home) as entries:
        for entry in entries:
            if not entry.name.startswith(TRANSACTION_PREFIX):
                continue
            node = entry.stat(follow_symlinks=False)
            reparse = bool(
                getattr(node, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if not stat.S_ISDIR(node.st_mode) or entry.is_symlink() or reparse:
                raise DeploymentConflict(f"Unsafe transaction residue: {entry.path}")
            result.append(home / entry.name)
    return sorted(result)


def _ensure_no_transaction(home: Path) -> None:
    residues = _transaction_directories(home)
    if _legacy_transaction_path(home).exists():
        residues.append(_legacy_transaction_path(home))
    if residues:
        raise RecoveryRequired(f"Deployment recovery is required: {residues[0]}")


def _tree_snapshot(source: Path, destination: Path) -> dict[str, str] | None:
    if not source.exists():
        return None
    copy_plain_tree(source, destination)
    return tree_inventory(destination)


@dataclass
class _Transaction:
    home: Path
    root: Path
    data: dict[str, object]
    journal_fingerprint: Fingerprint

    @property
    def journal_path(self) -> Path:
        return self.root / "journal.json"

    @property
    def snapshot(self) -> Path:
        return self.root / "snapshot"

    @property
    def stage(self) -> Path:
        return self.root / "stage"

    @classmethod
    def begin(
        cls,
        home: Path,
        action: str,
        manifest: dict[str, object] | None,
        target_skills: list[str],
    ) -> "_Transaction":
        config_content, config_fingerprint = read_regular_bytes(home / "config.toml")
        identifier = uuid.uuid4().hex
        root = private_directory(home / f"{TRANSACTION_PREFIX}{identifier}")
        try:
            snapshot = private_directory(root / "snapshot")
            private_directory(root / "stage")
            atomic_write_bytes(snapshot / "config.before", config_content, require_absent=True)

            product_inventory = _tree_snapshot(
                _product_root(home), snapshot / "product"
            )
            skill_snapshot_root = private_directory(snapshot / "skills")
            owned_skills = list((manifest or {}).get("installed_skills", []))
            skill_inventories: dict[str, dict[str, str]] = {}
            for item in owned_skills:
                name = _skill_name(item)
                source = home / "skills" / name
                if source.exists():
                    copied = _tree_snapshot(source, skill_snapshot_root / name)
                    skill_inventories[name] = copied or {}

            hooks_snapshot = private_directory(snapshot / "hooks")
            hook_records: dict[str, object] = {}
            for name in (HOOKS_ACTIVE, HOOKS_DISABLED):
                source = home / name
                current = fingerprint_or_none(source)
                hook_records[name] = current.to_dict() if current else None
                if current:
                    copy_regular_file(source, hooks_snapshot / name)

            data: dict[str, object] = {
                "schema": TRANSACTION_SCHEMA,
                "product": PRODUCT_SLUG,
                "id": identifier,
                "action": action,
                "phase": "prepared",
                "created_at": _utc_now(),
                "home": str(home),
                "target_skills": target_skills,
                "before": {
                    "config": config_fingerprint.to_dict(),
                    "product": product_inventory,
                    "skills": skill_inventories,
                    "hooks": hook_records,
                },
                "planned": {},
            }
            journal_fingerprint = atomic_write_json(
                root / "journal.json", data, require_absent=True
            )
            return cls(home, root, data, journal_fingerprint)
        except BaseException:
            with contextlib.suppress(OSError):
                safe_remove_tree(root, home)
            raise

    def update(self, *, phase: str | None = None, planned: object | None = None) -> None:
        if phase is not None:
            self.data["phase"] = phase
        if planned is not None:
            self.data["planned"] = planned
        self.journal_fingerprint = atomic_write_json(
            self.journal_path,
            self.data,
            expected=self.journal_fingerprint,
        )

    def cleanup(self) -> None:
        safe_remove_tree(self.root, self.home)


def _validate_inventory(value: object, label: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DeploymentConflict(f"Transaction {label} inventory is invalid")
    result: dict[str, str] = {}
    for path, digest in value.items():
        if not isinstance(path, str) or not isinstance(digest, str):
            raise DeploymentConflict(f"Transaction {label} inventory is invalid")
        portable = PurePosixPath(path)
        if portable.is_absolute() or ".." in portable.parts:
            raise DeploymentConflict(f"Transaction {label} path is unsafe")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DeploymentConflict(f"Transaction {label} digest is invalid")
        result[path] = digest
    return result


def _load_transaction(home: Path) -> _Transaction | None:
    residues = _transaction_directories(home)
    if not residues:
        return None
    if len(residues) != 1:
        raise DeploymentConflict("Multiple deployment transactions require manual review")
    root = residues[0]
    text, journal_fingerprint = read_regular_text(root / "journal.json")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeploymentConflict("Transaction journal is not valid JSON") from exc
    required = {
        "schema",
        "product",
        "id",
        "action",
        "phase",
        "created_at",
        "home",
        "target_skills",
        "before",
        "planned",
    }
    if not isinstance(data, dict) or set(data) != required:
        raise DeploymentConflict("Transaction journal fields are invalid")
    if data["schema"] != TRANSACTION_SCHEMA or data["product"] != PRODUCT_SLUG:
        raise DeploymentConflict("Transaction journal owner or schema is invalid")
    if data["id"] != root.name.removeprefix(TRANSACTION_PREFIX):
        raise DeploymentConflict("Transaction directory identity is invalid")
    if data["home"] != str(home) or data["action"] not in {
        "install",
        "remove",
        "restore-hooks",
    }:
        raise DeploymentConflict("Transaction target or action is invalid")
    if data["phase"] not in {
        "prepared",
        "staged",
        "hooks-published",
        "resources-published",
        "config-published",
        "ready",
        "committed",
        "rolling-back",
    }:
        raise DeploymentConflict("Transaction phase is invalid")
    if not isinstance(data["target_skills"], list):
        raise DeploymentConflict("Transaction skill list is invalid")
    data["target_skills"] = [_skill_name(item) for item in data["target_skills"]]
    before = data["before"]
    if not isinstance(before, dict) or set(before) != {"config", "product", "skills", "hooks"}:
        raise DeploymentConflict("Transaction before-state is invalid")
    _fingerprint_dict(before["config"])
    _validate_inventory(before["product"], "product")
    if not isinstance(before["skills"], dict) or not isinstance(before["hooks"], dict):
        raise DeploymentConflict("Transaction snapshots are invalid")
    for name, inventory in before["skills"].items():
        _skill_name(name)
        _validate_inventory(inventory, name)
    for name in (HOOKS_ACTIVE, HOOKS_DISABLED):
        _fingerprint_dict(before["hooks"].get(name), optional=True)
    if not isinstance(data["planned"], dict):
        raise DeploymentConflict("Transaction planned state is invalid")
    return _Transaction(home, root, data, journal_fingerprint)


def _known_inventory(
    current: dict[str, str] | None,
    before: dict[str, str] | None,
    planned: dict[str, str] | None,
) -> bool:
    if current is None:
        return True
    before = before or {}
    planned = planned or {}
    return all(
        digest in {before.get(path), planned.get(path)}
        for path, digest in current.items()
    )


def _unlink_known_file(path: Path, expected: Fingerprint | None = None) -> None:
    current = fingerprint(path)
    if expected is not None and current != expected:
        raise ConcurrentChangeError(f"File changed before removal: {path}")
    path.unlink()


def _replace_tree_from_snapshot(
    destination: Path,
    snapshot: Path,
    boundary: Path,
) -> None:
    if destination.exists():
        safe_remove_tree(destination, boundary)
    if snapshot.exists():
        copy_plain_tree(snapshot, destination)


def _restore_snapshot_file(
    snapshot: Path,
    destination: Path,
    before: Fingerprint | None,
    allowed_hashes: set[str],
) -> None:
    current = fingerprint_or_none(destination)
    if current is not None and current.sha256 not in allowed_hashes:
        raise DeploymentConflict(f"Unknown external change blocks recovery: {destination}")
    if snapshot.exists():
        content, _ = read_regular_bytes(snapshot)
        atomic_write_bytes(
            destination,
            content,
            expected=current,
            require_absent=current is None,
        )
    elif current is not None:
        _unlink_known_file(destination, current)


def _rollback_transaction(transaction: _Transaction) -> dict[str, object]:
    data = transaction.data
    before = data["before"]
    planned = data.get("planned") or {}
    assert isinstance(before, dict) and isinstance(planned, dict)
    transaction.update(phase="rolling-back")

    config_before = _fingerprint_dict(before["config"])
    assert config_before is not None
    config_current = fingerprint(transaction.home / "config.toml")
    config_allowed = {config_before.sha256}
    if isinstance(planned.get("config_sha256"), str):
        config_allowed.add(str(planned["config_sha256"]))
    if config_current.sha256 not in config_allowed:
        raise DeploymentConflict("Unknown config.toml change blocks transaction recovery")
    config_snapshot, _ = read_regular_bytes(transaction.snapshot / "config.before")
    atomic_write_bytes(
        transaction.home / "config.toml",
        config_snapshot,
        expected=config_current,
    )

    before_product = _validate_inventory(before["product"], "product")
    planned_product = _validate_inventory(planned.get("product"), "planned product")
    current_product = tree_inventory(_product_root(transaction.home))
    if not _known_inventory(current_product, before_product, planned_product):
        raise DeploymentConflict("Unknown product-tree change blocks recovery")

    before_skills = before["skills"]
    planned_skills = planned.get("skills", {})
    if not isinstance(before_skills, dict) or not isinstance(planned_skills, dict):
        raise DeploymentConflict("Transaction skill inventories are invalid")
    names = set(before_skills) | set(planned_skills) | set(data["target_skills"])
    for name in names:
        skill = _skill_name(name)
        current = tree_inventory(transaction.home / "skills" / skill)
        old = _validate_inventory(before_skills.get(skill), f"before {skill}")
        new = _validate_inventory(planned_skills.get(skill), f"planned {skill}")
        if not _known_inventory(current, old, new):
            raise DeploymentConflict(f"Unknown skill change blocks recovery: {skill}")

    before_hooks = before["hooks"]
    planned_hooks = planned.get("hooks", {})
    if not isinstance(before_hooks, dict) or not isinstance(planned_hooks, dict):
        raise DeploymentConflict("Transaction hook state is invalid")
    for name in (HOOKS_ACTIVE, HOOKS_DISABLED):
        old = _fingerprint_dict(before_hooks.get(name), optional=True)
        new = _fingerprint_dict(planned_hooks.get(name), optional=True)
        allowed = {item.sha256 for item in (old, new) if item is not None}
        _restore_snapshot_file(
            transaction.snapshot / "hooks" / name,
            transaction.home / name,
            old,
            allowed,
        )

    _replace_tree_from_snapshot(
        _product_root(transaction.home),
        transaction.snapshot / "product",
        transaction.home,
    )
    for name in names:
        skill = _skill_name(name)
        _replace_tree_from_snapshot(
            transaction.home / "skills" / skill,
            transaction.snapshot / "skills" / skill,
            transaction.home / "skills",
        )
    transaction.cleanup()
    return {
        "action": "recover",
        "recovered": True,
        "completed": f"{data['action']}-rollback",
    }


def _source_skills() -> list[tuple[str, Path]]:
    if not BRIDGE_SOURCE.is_file() or not SKILLPACK_SOURCE.is_dir():
        raise FileNotFoundError("Product bridge or skillpacks are missing")
    result: list[tuple[str, Path]] = []
    for source in sorted(SKILLPACK_SOURCE.iterdir()):
        if source.is_dir() and (source / "SKILL.md").is_file():
            name = _skill_name(f"{PRODUCT_SLUG}-{source.name}")
            result.append((name, source))
    return result


def _instruction_reference(home: Path) -> str:
    return _bridge_path(home).absolute().as_posix()


def _toml_path_line(path: Path) -> str:
    return f'{ROOT_KEY} = {json.dumps(path.absolute().as_posix(), ensure_ascii=False)}'


def _active_root_instruction(lines: list[str]) -> tuple[int | None, str | None]:
    content = "".join(lines)
    reference = find_root_string(content, ROOT_KEY)
    if reference is None:
        return None, None
    for index, line in enumerate(lines):
        if line.strip().startswith(ROOT_KEY) and "=" in line:
            return index, line.rstrip("\r\n")
    return None, _toml_path_line(Path(reference))


def _rewrite_root_instruction(
    text: str,
    replacement: str | None,
) -> tuple[str, str | None]:
    value: str | None = None
    if replacement is not None:
        probe = f"{replacement}\n"
        value = find_root_string(probe, ROOT_KEY)
        if value is None:
            raise TomlEditError("Replacement does not define model_instructions_file")
    updated, previous, _ = set_root_string(text, ROOT_KEY, value)
    previous_line = (
        f'{ROOT_KEY} = {json.dumps(previous, ensure_ascii=False)}'
        if previous is not None
        else None
    )
    return updated, previous_line


def _assert_product_namespace(
    home: Path,
    manifest: dict[str, object] | None,
) -> None:
    root = _product_root(home)
    inventory = tree_inventory(root)
    if inventory is None:
        return
    allowed = {MANIFEST_NAME, RELAY_RECORD, HOOKS_BACKUP}
    if manifest:
        prefix = f"{PRODUCT_SLUG}/"
        for relative in manifest["managed_files"]:
            if str(relative).startswith(prefix):
                allowed.add(str(relative)[len(prefix) :])
    unknown = sorted(set(inventory) - allowed)
    if unknown:
        raise DeploymentConflict(
            f"Product namespace contains unowned files: {', '.join(unknown)}"
        )


def _bridge_source(explicit: str | Path | None) -> Path:
    source = BRIDGE_SOURCE if explicit is None else require_regular_file(explicit)
    read_regular_text(source)
    return source


def _stage_product_base(transaction: _Transaction, bridge_source: Path) -> Path:
    product = private_directory(transaction.stage / "product")
    copy_regular_file(bridge_source, product / "bridge.md")
    relay = _relay_manifest_path(transaction.home)
    if relay.exists():
        copy_regular_file(relay, product / RELAY_RECORD)
    return product


def _stage_skills(
    transaction: _Transaction,
    sources: list[tuple[str, Path]],
) -> Path:
    root = private_directory(transaction.stage / "skills")
    for name, source in sources:
        copy_plain_tree(source, root / name)
    return root


def _hook_fingerprints(home: Path) -> tuple[Fingerprint | None, Fingerprint | None]:
    return (
        fingerprint_or_none(_hook_path(home, HOOKS_ACTIVE)),
        fingerprint_or_none(_hook_path(home, HOOKS_DISABLED)),
    )


def inspect_hooks(codex_home: str | Path | None = None) -> dict[str, object]:
    home = _resolve_home(codex_home)
    active, disabled = _hook_fingerprints(home)
    return {
        "active": active is not None,
        "disabled": disabled is not None,
        "active_sha256": active.sha256 if active else None,
        "disabled_sha256": disabled.sha256 if disabled else None,
    }


def _plan_hooks(
    home: Path,
    stage_product: Path,
    skip_hooks_isolation: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    active, disabled = _hook_fingerprints(home)
    if skip_hooks_isolation:
        record = {
            "mode": "skipped",
            "active_before": active.to_dict() if active else None,
            "disabled_before": disabled.to_dict() if disabled else None,
            "isolated": None,
            "backup": None,
            "backup_sha256": None,
        }
        planned = {
            HOOKS_ACTIVE: active.to_dict() if active else None,
            HOOKS_DISABLED: disabled.to_dict() if disabled else None,
        }
        return record, planned
    if active is None:
        record = {
            "mode": "absent",
            "active_before": None,
            "disabled_before": disabled.to_dict() if disabled else None,
            "isolated": None,
            "backup": None,
            "backup_sha256": None,
        }
        planned = {
            HOOKS_ACTIVE: None,
            HOOKS_DISABLED: disabled.to_dict() if disabled else None,
        }
        return record, planned

    backup_relative: str | None = None
    backup_sha256: str | None = None
    if disabled is not None:
        backup = stage_product / HOOKS_BACKUP
        copy_regular_file(home / HOOKS_DISABLED, backup)
        backup_relative = f"{PRODUCT_SLUG}/{HOOKS_BACKUP}"
        backup_sha256 = fingerprint(backup).sha256
    record = {
        "mode": "isolated",
        "active_before": active.to_dict(),
        "disabled_before": disabled.to_dict() if disabled else None,
        "isolated": active.to_dict(),
        "backup": backup_relative,
        "backup_sha256": backup_sha256,
    }
    planned = {HOOKS_ACTIVE: None, HOOKS_DISABLED: active.to_dict()}
    return record, planned


def _move_file_no_replace(
    source: Path,
    destination: Path,
    expected: Fingerprint,
) -> Fingerprint:
    content, current = read_regular_bytes(source, expected)
    published = atomic_write_bytes(destination, content, require_absent=True)
    latest = fingerprint(source)
    if latest != current:
        with contextlib.suppress(OSError):
            _unlink_known_file(destination, published)
        raise ConcurrentChangeError(f"Source changed during move: {source}")
    _unlink_known_file(source, latest)
    return published


def _apply_hook_isolation(
    transaction: _Transaction,
    record: dict[str, object],
) -> None:
    if record["mode"] != "isolated":
        return
    active = _fingerprint_dict(record["active_before"])
    disabled = _fingerprint_dict(record["disabled_before"], optional=True)
    assert active is not None
    active_path = transaction.home / HOOKS_ACTIVE
    disabled_path = transaction.home / HOOKS_DISABLED
    if disabled is not None:
        retired = transaction.root / "hooks.disabled.retired"
        _move_file_no_replace(disabled_path, retired, disabled)
    published = _move_file_no_replace(active_path, disabled_path, active)
    if published.sha256 != active.sha256:
        raise ConcurrentChangeError("Isolated hooks content changed during publication")


def _stage_managed_hashes(
    home: Path,
    stage_product: Path,
    stage_skills: Path,
    skill_names: list[str],
) -> dict[str, str]:
    managed: dict[str, str] = {}
    product_inventory = tree_inventory(stage_product) or {}
    for relative, digest in product_inventory.items():
        if relative in {MANIFEST_NAME, RELAY_RECORD}:
            continue
        managed[f"{PRODUCT_SLUG}/{relative}"] = digest
    for name in skill_names:
        inventory = tree_inventory(stage_skills / name) or {}
        for relative, digest in inventory.items():
            managed[f"skills/{name}/{relative}"] = digest
    return managed


def _build_manifest(
    home: Path,
    previous_instruction: str | None,
    skill_names: list[str],
    managed_files: dict[str, str],
    hooks: dict[str, object],
) -> dict[str, object]:
    return {
        "schema": MANIFEST_SCHEMA,
        "product": PRODUCT_SLUG,
        "version": VERSION,
        "installed_at": _utc_now(),
        "config_path": str(home / "config.toml"),
        "bridge_path": str(_bridge_path(home)),
        "previous_instruction": previous_instruction,
        "previous_instruction_line": (
            f'{ROOT_KEY} = {json.dumps(previous_instruction, ensure_ascii=False)}'
            if previous_instruction is not None
            else None
        ),
        "installed_skills": skill_names,
        "managed_files": managed_files,
        "hooks": hooks,
    }


def _publish_tree(stage: Path, destination: Path, boundary: Path) -> None:
    if destination.exists():
        safe_remove_tree(destination, boundary)
    copy_plain_tree(stage, destination)


def _publish_resources(
    transaction: _Transaction,
    manifest: dict[str, object],
    old_skills: set[str],
) -> None:
    home = transaction.home
    stage_product = transaction.stage / "product"
    stage_skills = transaction.stage / "skills"
    _publish_tree(stage_product, _product_root(home), home)
    skill_root = home / "skills"
    if not skill_root.exists():
        private_directory(skill_root)
    else:
        require_plain_directory(skill_root)
    target = set(manifest["installed_skills"])
    for name in sorted(target):
        _publish_tree(stage_skills / name, skill_root / name, skill_root)
    for name in sorted(old_skills - target):
        destination = skill_root / _skill_name(name)
        if destination.exists():
            safe_remove_tree(destination, skill_root)


def _verify_managed_files(home: Path, manifest: dict[str, object]) -> list[str]:
    issues: list[str] = []
    for relative, expected in manifest["managed_files"].items():
        path = _relative_path(home, relative)
        try:
            current = fingerprint(path)
        except FileNotFoundError:
            issues.append(f"managed file missing: {relative}")
            continue
        except FilesystemSafetyError as exc:
            issues.append(f"managed file unsafe: {relative}: {exc}")
            continue
        if current.sha256 != expected:
            issues.append(f"managed file changed: {relative}")
    return issues


def _install_locked(
    home: Path,
    *,
    skip_hooks_isolation: bool,
    bridge_file: str | Path | None = None,
    defer_commit: bool = False,
) -> tuple[DeploymentResult, _Transaction | None]:
    _ensure_no_transaction(home)
    manifest = _load_manifest(home)
    _assert_product_namespace(home, manifest)
    sources = _source_skills()
    skill_names = [name for name, _source in sources]
    old_skills = set(manifest["installed_skills"] if manifest else [])
    skill_root = home / "skills"
    if skill_root.exists():
        require_plain_directory(skill_root)
    for name in skill_names:
        destination = skill_root / name
        if destination.exists() and name not in old_skills:
            raise FileExistsError(
                f"Refusing to replace unowned skill directory: {destination}"
            )

    config_text, config_before = read_regular_text(home / "config.toml")
    reference = _instruction_reference(home)
    updated_config, current_previous, _ = set_root_string(
        config_text, ROOT_KEY, reference
    )
    previous_instruction = (
        manifest["previous_instruction"] if manifest else current_previous
    )
    bridge_source = _bridge_source(bridge_file)
    transaction = _Transaction.begin(home, "install", manifest, skill_names)
    try:
        stage_product = _stage_product_base(transaction, bridge_source)
        stage_skills = _stage_skills(transaction, sources)
        hook_record, planned_hooks = _plan_hooks(
            home, stage_product, skip_hooks_isolation
        )
        managed = _stage_managed_hashes(
            home, stage_product, stage_skills, skill_names
        )
        new_manifest = _build_manifest(
            home,
            previous_instruction,
            skill_names,
            managed,
            hook_record,
        )
        atomic_write_json(
            stage_product / MANIFEST_NAME,
            new_manifest,
            require_absent=True,
        )
        planned = {
            "config_sha256": hashlib.sha256(updated_config.encode("utf-8")).hexdigest(),
            "product": tree_inventory(stage_product),
            "skills": {
                name: tree_inventory(stage_skills / name) for name in skill_names
            },
            "hooks": planned_hooks,
        }
        transaction.update(phase="staged", planned=planned)
        _apply_hook_isolation(transaction, hook_record)
        transaction.update(phase="hooks-published")
        _publish_resources(transaction, new_manifest, old_skills)
        transaction.update(phase="resources-published")
        atomic_write_text(
            home / "config.toml",
            updated_config,
            expected=config_before,
        )
        transaction.update(phase="config-published")

        config_after = fingerprint(home / "config.toml")
        if config_after.sha256 != planned["config_sha256"]:
            raise ConcurrentChangeError("Published config does not match the plan")
        issues = _verify_managed_files(home, new_manifest)
        if issues:
            raise ConcurrentChangeError("; ".join(issues))
        transaction.update(phase="ready")
        result = DeploymentResult(
            "install",
            str(home),
            True,
            skill_names,
            "Installed with guarded hooks and recoverable transactions",
        )
        if defer_commit:
            return result, transaction
        transaction.update(phase="committed")
        transaction.cleanup()
        return result, None
    except BaseException as primary:
        try:
            _rollback_transaction(transaction)
        except BaseException as rollback_error:
            raise RecoveryRequired(
                f"Install failed and rollback needs review: {primary}; {rollback_error}"
            ) from primary
        raise


def install(
    codex_home: str | Path | None = None,
    *,
    skip_hooks_isolation: bool = False,
    bridge_file: str | Path | None = None,
) -> DeploymentResult:
    home = _resolve_home(codex_home)
    with DirectoryLock(home):
        result, _ = _install_locked(
            home,
            skip_hooks_isolation=skip_hooks_isolation,
            bridge_file=bridge_file,
        )
        return result


def _planned_restored_hooks(manifest: dict[str, object]) -> dict[str, object]:
    hooks = manifest["hooks"]
    assert isinstance(hooks, dict)
    if hooks["mode"] != "isolated":
        active = _fingerprint_dict(hooks["active_before"], optional=True)
        disabled = _fingerprint_dict(hooks["disabled_before"], optional=True)
        return {
            HOOKS_ACTIVE: active.to_dict() if active else None,
            HOOKS_DISABLED: disabled.to_dict() if disabled else None,
        }
    active = _fingerprint_dict(hooks["isolated"])
    disabled = _fingerprint_dict(hooks["disabled_before"], optional=True)
    return {
        HOOKS_ACTIVE: active.to_dict() if active else None,
        HOOKS_DISABLED: disabled.to_dict() if disabled else None,
    }


def _restore_managed_hooks_locked(
    home: Path,
    manifest: dict[str, object],
) -> bool:
    hooks = manifest["hooks"]
    assert isinstance(hooks, dict)
    if hooks["mode"] != "isolated":
        return False
    active_path = home / HOOKS_ACTIVE
    disabled_path = home / HOOKS_DISABLED
    if fingerprint_or_none(active_path) is not None:
        raise DeploymentConflict("Active hooks.json blocks managed hook restoration")
    current_disabled = fingerprint_or_none(disabled_path)
    isolated = _fingerprint_dict(hooks["isolated"])
    if current_disabled is None or isolated is None:
        raise DeploymentConflict("Managed isolated hooks.json is missing")
    if current_disabled.sha256 != isolated.sha256:
        raise DeploymentConflict("Managed isolated hooks.json has changed")

    isolated_content, _ = read_regular_bytes(disabled_path, current_disabled)
    backup_value = hooks["backup"]
    if backup_value is None:
        _move_file_no_replace(disabled_path, active_path, current_disabled)
        return True

    backup_path = _relative_path(home, backup_value)
    backup_content, backup_fingerprint = read_regular_bytes(backup_path)
    if backup_fingerprint.sha256 != hooks["backup_sha256"]:
        raise DeploymentConflict("Original disabled hooks backup has changed")
    atomic_write_bytes(active_path, isolated_content, require_absent=True)
    atomic_write_bytes(
        disabled_path,
        backup_content,
        expected=current_disabled,
    )
    return True


def _relay_restore_text(home: Path, text: str) -> tuple[str, bool]:
    record_path = _relay_manifest_path(home)
    if not record_path.exists():
        return text, False
    record_text, _ = read_regular_text(record_path)
    try:
        record = json.loads(record_text)
    except json.JSONDecodeError as exc:
        raise DeploymentConflict("Relay configuration record is invalid") from exc
    if not isinstance(record, dict) or record.get("product") != PRODUCT_SLUG:
        raise DeploymentConflict("Relay configuration record has another owner")
    expected = record.get("relay_line")
    previous = record.get("previous_line")
    if not isinstance(expected, str) or not isinstance(previous, str):
        raise DeploymentConflict("Relay configuration record fields are invalid")
    lines = text.splitlines(keepends=True)
    matches = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == expected
    ]
    if not matches:
        previous_matches = [
            line
            for line in lines
            if line.rstrip("\r\n") == previous
        ]
        if len(previous_matches) == 1:
            return text, False
        raise DeploymentConflict("Managed relay line changed; restore is blocked")
    if len(matches) != 1:
        raise DeploymentConflict("Relay configuration line is ambiguous")
    index = matches[0]
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    lines[index] = previous + newline
    restored = "".join(lines)
    # set_root_string performs a complete TOML validation later in remove().
    return restored, True


def _remove_locked(
    home: Path,
    *,
    defer_commit: bool = False,
) -> tuple[DeploymentResult, _Transaction | None]:
    _ensure_no_transaction(home)
    manifest = _load_manifest(home)
    if manifest is None:
        return (
            DeploymentResult("remove", str(home), False, [], "Nothing to remove"),
            None,
        )
    _assert_product_namespace(home, manifest)
    issues = _verify_managed_files(home, manifest)
    if issues:
        raise DeploymentConflict("; ".join(issues))

    config_text, config_before = read_regular_text(home / "config.toml")
    current_reference = find_root_string(config_text, ROOT_KEY)
    if current_reference != _instruction_reference(home):
        raise DeploymentConflict("Managed instruction reference changed")
    relay_restored_text, _ = _relay_restore_text(home, config_text)
    updated_config, _, _ = set_root_string(
        relay_restored_text,
        ROOT_KEY,
        manifest["previous_instruction"],
    )
    skills = list(manifest["installed_skills"])
    transaction = _Transaction.begin(home, "remove", manifest, skills)
    try:
        planned_hooks = _planned_restored_hooks(manifest)
        planned = {
            "config_sha256": hashlib.sha256(updated_config.encode("utf-8")).hexdigest(),
            "product": None,
            "skills": {name: None for name in skills},
            "hooks": planned_hooks,
        }
        transaction.update(phase="staged", planned=planned)
        _restore_managed_hooks_locked(home, manifest)
        transaction.update(phase="hooks-published")
        atomic_write_text(
            home / "config.toml",
            updated_config,
            expected=config_before,
        )
        transaction.update(phase="config-published")
        skill_root = home / "skills"
        for name in skills:
            destination = skill_root / _skill_name(name)
            if destination.exists():
                safe_remove_tree(destination, skill_root)
        if _product_root(home).exists():
            safe_remove_tree(_product_root(home), home)
        transaction.update(phase="resources-published")
        transaction.update(phase="ready")
        result = DeploymentResult(
            "remove",
            str(home),
            False,
            skills,
            "Removed managed resources and restored configuration and hooks",
        )
        if defer_commit:
            return result, transaction
        transaction.update(phase="committed")
        transaction.cleanup()
        return result, None
    except BaseException as primary:
        try:
            _rollback_transaction(transaction)
        except BaseException as rollback_error:
            raise RecoveryRequired(
                f"Removal failed and rollback needs review: {primary}; {rollback_error}"
            ) from primary
        raise


def remove(codex_home: str | Path | None = None) -> DeploymentResult:
    home = _resolve_home(codex_home)
    with DirectoryLock(home):
        result, _ = _remove_locked(home)
        return result


def _hooks_issues(home: Path, manifest: dict[str, object]) -> list[str]:
    issues: list[str] = []
    hooks = manifest["hooks"]
    assert isinstance(hooks, dict)
    active, disabled = _hook_fingerprints(home)
    if hooks["mode"] == "isolated":
        isolated = _fingerprint_dict(hooks["isolated"])
        if active is not None:
            issues.append("active hooks.json appeared after isolation")
        if disabled is None:
            issues.append("isolated hooks.json.disabled is missing")
        elif isolated is None or disabled.sha256 != isolated.sha256:
            issues.append("isolated hooks.json.disabled changed")
    return issues


def inspect_deployment(
    codex_home: str | Path | None = None,
) -> DeploymentInspection:
    home = locate_codex_home(codex_home)
    if home is None:
        return DeploymentInspection("missing-codex", "", ["config.toml not found"], [])
    try:
        transactions = _transaction_directories(home)
        legacy = _legacy_transaction_path(home)
        if legacy.exists():
            transactions.append(legacy)
        if transactions:
            state = "recovery-required"
            try:
                active = _load_transaction(home)
                if active and active.data["phase"] == "committed":
                    state = "cleanup-required"
            except DeploymentError:
                state = "blocked"
            return DeploymentInspection(
                state,
                str(home),
                ["transaction residue is present"],
                [],
                inspect_hooks(home),
                [str(item) for item in transactions],
            )
        manifest = _load_manifest(home)
        if manifest is None:
            return DeploymentInspection(
                "not-installed", str(home), [], [], inspect_hooks(home), []
            )
        issues: list[str] = []
        config_text, _ = read_regular_text(home / "config.toml")
        if find_root_string(config_text, ROOT_KEY) != _instruction_reference(home):
            issues.append("managed instruction reference changed")
        issues.extend(_verify_managed_files(home, manifest))
        issues.extend(_hooks_issues(home, manifest))
        return DeploymentInspection(
            "conflict" if issues else "healthy",
            str(home),
            issues,
            list(manifest["installed_skills"]),
            inspect_hooks(home),
            [],
        )
    except (DeploymentError, FilesystemSafetyError, TomlEditError, OSError) as exc:
        return DeploymentInspection(
            "blocked", str(home), [str(exc)], [], {}, []
        )


def status(codex_home: str | Path | None = None) -> DeploymentResult:
    inspection = inspect_deployment(codex_home)
    if not inspection.codex_home:
        return DeploymentResult("status", "", False, [], "Codex config not found")
    message = inspection.state
    if inspection.issues:
        message += f": {'; '.join(inspection.issues)}"
    return DeploymentResult(
        "status",
        inspection.codex_home,
        inspection.state == "healthy",
        inspection.skills,
        message,
    )


def preview_install(
    codex_home: str | Path | None = None,
    *,
    skip_hooks_isolation: bool = False,
    bridge_file: str | Path | None = None,
) -> dict[str, object]:
    home = _resolve_home(codex_home)
    bridge_source = _bridge_source(bridge_file)
    active, disabled = _hook_fingerprints(home)
    return {
        "action": "install",
        "state": inspect_deployment(home).state,
        "config": str(home / "config.toml"),
        "instruction": _toml_path_line(_bridge_path(home)),
        "bridge": str(_bridge_path(home)),
        "bridge_source": str(bridge_source),
        "skills": [name for name, _source in _source_skills()],
        "hooks": {
            "active": active is not None,
            "disabled": disabled is not None,
            "action": (
                "leave-active"
                if skip_hooks_isolation
                else "isolate-active" if active else "no-change"
            ),
        },
        "transaction": "snapshot + staged publish + recoverable rollback",
    }


def preview_remove(codex_home: str | Path | None = None) -> dict[str, object]:
    home = _resolve_home(codex_home)
    manifest = _load_manifest(home)
    inspection = inspect_deployment(home)
    return {
        "action": "remove",
        "state": inspection.state,
        "blockers": inspection.issues,
        "restore_instruction": (
            manifest["previous_instruction"] if manifest else None
        ),
        "skills": list(manifest["installed_skills"] if manifest else []),
        "restore_relay": _relay_manifest_path(home).exists(),
        "restore_hooks": bool(
            manifest and manifest["hooks"]["mode"] == "isolated"
        ),
    }


def restore_hooks(codex_home: str | Path | None = None) -> dict[str, object]:
    home = _resolve_home(codex_home)
    with DirectoryLock(home):
        _ensure_no_transaction(home)
        manifest = _load_manifest(home)
        if manifest is None:
            active, disabled = _hook_fingerprints(home)
            if active is not None or disabled is None:
                return {"action": "restore-hooks", "restored": False}
            _move_file_no_replace(
                home / HOOKS_DISABLED,
                home / HOOKS_ACTIVE,
                disabled,
            )
            return {"action": "restore-hooks", "restored": True, "managed": False}

        transaction = _Transaction.begin(
            home,
            "restore-hooks",
            manifest,
            list(manifest["installed_skills"]),
        )
        try:
            stage_product = transaction.stage / "product"
            copy_plain_tree(_product_root(home), stage_product)
            updated_manifest = dict(manifest)
            hooks = dict(manifest["hooks"])
            restored = hooks["mode"] == "isolated"
            hooks["mode"] = "restored" if restored else hooks["mode"]
            updated_manifest["hooks"] = hooks
            current_manifest = fingerprint(stage_product / MANIFEST_NAME)
            atomic_write_json(
                stage_product / MANIFEST_NAME,
                updated_manifest,
                expected=current_manifest,
            )
            before = transaction.data["before"]
            assert isinstance(before, dict)
            planned = {
                "config_sha256": _fingerprint_dict(before["config"]).sha256,
                "product": tree_inventory(stage_product),
                "skills": dict(before["skills"]),
                "hooks": _planned_restored_hooks(manifest),
            }
            transaction.update(phase="staged", planned=planned)
            if restored:
                _restore_managed_hooks_locked(home, manifest)
            transaction.update(phase="hooks-published")
            _publish_tree(stage_product, _product_root(home), home)
            transaction.update(phase="resources-published")
            transaction.update(phase="ready")
            transaction.update(phase="committed")
            transaction.cleanup()
            return {
                "action": "restore-hooks",
                "restored": restored,
                "managed": True,
            }
        except BaseException as primary:
            try:
                _rollback_transaction(transaction)
            except BaseException as rollback_error:
                raise RecoveryRequired(
                    f"Hook restore failed and rollback needs review: {primary}; "
                    f"{rollback_error}"
                ) from primary
            raise


def _recover_legacy_transaction(home: Path) -> dict[str, object]:
    path = _legacy_transaction_path(home)
    text, path_fingerprint = read_regular_text(path)
    try:
        transaction = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeploymentConflict("Legacy transaction is not valid JSON") from exc
    if not isinstance(transaction, dict) or transaction.get("product") != PRODUCT_SLUG:
        raise DeploymentConflict("Legacy transaction has another owner")
    action = transaction.get("action")
    if action == "remove":
        _unlink_known_file(path, path_fingerprint)
        result, _ = _remove_locked(home)
        return {
            "action": "recover",
            "recovered": True,
            "completed": "remove",
            "result": result.to_dict(),
        }
    if action != "install":
        raise DeploymentConflict(f"Unknown legacy transaction action: {action}")
    existing = transaction.get("existing_manifest")
    if isinstance(existing, dict):
        _unlink_known_file(path, path_fingerprint)
        result, _ = _install_locked(home, skip_hooks_isolation=False)
        return {
            "action": "recover",
            "recovered": True,
            "completed": "install",
            "result": result.to_dict(),
        }

    config_text, config_fingerprint = read_regular_text(home / "config.toml")
    current = find_root_string(config_text, ROOT_KEY)
    if current == _instruction_reference(home):
        previous_line = transaction.get("previous_instruction_line")
        previous: str | None = None
        if isinstance(previous_line, str):
            previous = find_root_string(previous_line + "\n", ROOT_KEY)
        restored, _, _ = set_root_string(config_text, ROOT_KEY, previous)
        atomic_write_text(
            home / "config.toml", restored, expected=config_fingerprint
        )
    skill_root = home / "skills"
    for value in transaction.get("target_skills", []):
        name = _skill_name(value)
        destination = skill_root / name
        if destination.exists():
            safe_remove_tree(destination, skill_root)
    if _product_root(home).exists():
        safe_remove_tree(_product_root(home), home)
    return {
        "action": "recover",
        "recovered": True,
        "completed": "install-rollback",
    }


def recover_transaction(codex_home: str | Path | None = None) -> dict[str, object]:
    home = _resolve_home(codex_home)
    with DirectoryLock(home):
        if _legacy_transaction_path(home).exists():
            if _transaction_directories(home):
                raise DeploymentConflict("Legacy and current transactions overlap")
            return _recover_legacy_transaction(home)
        transaction = _load_transaction(home)
        if transaction is None:
            return {
                "action": "recover",
                "recovered": False,
                "message": "No transaction",
            }
        if transaction.data["phase"] == "committed":
            transaction.cleanup()
            return {
                "action": "recover",
                "recovered": True,
                "completed": "cleanup",
            }
        return _rollback_transaction(transaction)


def repair(codex_home: str | Path | None = None) -> DeploymentResult:
    home = _resolve_home(codex_home)
    inspection = inspect_deployment(home)
    if inspection.state in {"recovery-required", "cleanup-required"}:
        recover_transaction(home)
        inspection = inspect_deployment(home)
    if inspection.state not in {"healthy", "conflict", "not-installed"}:
        raise DeploymentConflict(f"Cannot repair deployment state: {inspection.state}")
    return install(home)


def _resolve_many(codex_homes: Iterable[str | Path] | None) -> list[Path]:
    candidates = (
        [_resolve_home(item) for item in codex_homes]
        if codex_homes is not None
        else discover_codex_homes()
    )
    unique = {
        os.path.normcase(str(item.resolve(strict=True))): item for item in candidates
    }
    result = [unique[key] for key in sorted(unique)]
    if not result:
        raise FileNotFoundError("No Codex configuration directories were found")
    return result


def _coordinated_operation(
    homes: list[Path],
    operation: Callable[[Path], tuple[DeploymentResult, _Transaction | None]],
) -> list[DeploymentResult]:
    active: list[_Transaction] = []
    results: list[DeploymentResult] = []
    with DirectoryLockSet(homes):
        try:
            for home in homes:
                result, transaction = operation(home)
                results.append(result)
                if transaction is not None:
                    active.append(transaction)
            for transaction in active:
                transaction.update(phase="committed")
            for transaction in active:
                transaction.cleanup()
            return results
        except BaseException as primary:
            errors: list[str] = []
            for transaction in reversed(active):
                try:
                    _rollback_transaction(transaction)
                except BaseException as exc:
                    errors.append(str(exc))
            if errors:
                raise RecoveryRequired(
                    f"Coordinated operation failed: {primary}; rollback: "
                    + "; ".join(errors)
                ) from primary
            raise


def install_many(
    codex_homes: Iterable[str | Path] | None = None,
    *,
    skip_hooks_isolation: bool = False,
    bridge_file: str | Path | None = None,
) -> list[DeploymentResult]:
    homes = _resolve_many(codex_homes)
    return _coordinated_operation(
        homes,
        lambda home: _install_locked(
            home,
            skip_hooks_isolation=skip_hooks_isolation,
            bridge_file=bridge_file,
            defer_commit=True,
        ),
    )


def remove_many(
    codex_homes: Iterable[str | Path] | None = None,
) -> list[DeploymentResult]:
    homes = _resolve_many(codex_homes)
    return _coordinated_operation(
        homes,
        lambda home: _remove_locked(home, defer_commit=True),
    )


def _find_base_url_line(text: str) -> tuple[int | None, str | None, str | None]:
    # Parse the whole document first, then preserve the exact matching line.
    find_root_string(text, ROOT_KEY)
    matches: list[tuple[int, str, str]] = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        if line.lstrip().startswith("#"):
            continue
        raw = line.rstrip("\r\n")
        match = BASE_URL_RE.match(raw)
        if match:
            matches.append((index, raw, match.group(2)))
    if not matches:
        return None, None, None
    if len(matches) > 1:
        raise DeploymentConflict("Multiple active base_url values are ambiguous")
    return matches[0]


def discover_upstream(codex_home: str | Path | None = None) -> str | None:
    home = locate_codex_home(codex_home)
    if home is None:
        return None
    record_path = _relay_manifest_path(home)
    if record_path.exists():
        try:
            record = json.loads(read_regular_text(record_path)[0])
            previous = record.get("previous_url")
            if record.get("product") == PRODUCT_SLUG and isinstance(previous, str):
                return previous
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    text, _ = read_regular_text(home / "config.toml")
    return _find_base_url_line(text)[2]


def _ensure_product_directory(home: Path) -> Path:
    root = _product_root(home)
    if root.exists():
        require_plain_directory(root)
    else:
        private_directory(root)
    return root


def configure_relay(
    codex_home: str | Path | None = None,
    relay_url: str = "http://127.0.0.1:8080/v1",
) -> dict[str, object]:
    home = _resolve_home(codex_home)
    with DirectoryLock(home):
        _ensure_no_transaction(home)
        record_path = _relay_manifest_path(home)
        if record_path.exists():
            record = json.loads(read_regular_text(record_path)[0])
            if record.get("relay_url") == relay_url:
                return {"configured": True, "relay_url": relay_url, "changed": False}
            raise DeploymentConflict("Restore the existing relay configuration first")
        config_path = home / "config.toml"
        text, before = read_regular_text(config_path)
        index, previous_line, previous_url = _find_base_url_line(text)
        if index is None or previous_line is None or previous_url is None:
            raise DeploymentConflict("No unambiguous active base_url was found")
        if previous_url == relay_url:
            return {"configured": True, "relay_url": relay_url, "changed": False}
        lines = text.splitlines(keepends=True)
        newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
        match = BASE_URL_RE.match(previous_line)
        assert match is not None
        relay_line = f'{match.group(1)}"{relay_url}"{match.group(3)}'
        lines[index] = relay_line + newline
        rewritten = "".join(lines)
        find_root_string(rewritten, ROOT_KEY)
        after = atomic_write_text(config_path, rewritten, expected=before)
        try:
            _ensure_product_directory(home)
            atomic_write_json(
                record_path,
                {
                    "schema": 1,
                    "product": PRODUCT_SLUG,
                    "config_path": str(config_path),
                    "previous_line": previous_line,
                    "previous_url": previous_url,
                    "relay_line": relay_line,
                    "relay_url": relay_url,
                },
                require_absent=True,
            )
        except BaseException:
            atomic_write_text(config_path, text, expected=after)
            raise
        return {
            "configured": True,
            "relay_url": relay_url,
            "upstream": previous_url,
            "changed": True,
        }


def restore_relay(codex_home: str | Path | None = None) -> dict[str, object]:
    home = _resolve_home(codex_home)
    with DirectoryLock(home):
        _ensure_no_transaction(home)
        record_path = _relay_manifest_path(home)
        if not record_path.exists():
            return {"configured": False, "changed": False}
        record_fingerprint = fingerprint(record_path)
        config_path = home / "config.toml"
        text, before = read_regular_text(config_path)
        restored, changed = _relay_restore_text(home, text)
        if changed:
            find_root_string(restored, ROOT_KEY)
            atomic_write_text(config_path, restored, expected=before)
        _unlink_known_file(record_path, record_fingerprint)
        root = _product_root(home)
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
        return {"configured": False, "changed": changed}
