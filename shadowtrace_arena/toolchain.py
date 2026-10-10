"""Deploy the bundled Code Sentinel core and optional host security tools."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .deployer import discover_codex_homes
from .product import PACKAGE_ROOT, PROJECT_ROOT

CODE_SENTINEL_VERSION = "3.9.1"
CODE_SENTINEL_ARCHIVE = (
    PACKAGE_ROOT / "data" / "vendor" / f"code-sentinel-{CODE_SENTINEL_VERSION}.zip"
)
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT / ".toolchain" / "code-sentinel"
SCOOP_INSTALL_URL = "https://get.scoop.sh"
BREW_INSTALL_URL = (
    "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
)
PROFILES = ("none", "core", "full")
VENDOR_MARKER = ".shadowtrace-vendor.json"
RUNTIME_REQUIRED = (
    "sentinel.py",
    "VERSION",
    "使用说明.md",
    "scripts/codex_inject.py",
    "scripts/ops_doctor.py",
    "scripts/ops_tool_index.py",
    "codex-bundle/skills/code-sentinel/SKILL.md",
)


@dataclass(frozen=True)
class HostPackage:
    capability: str
    profile: str
    scoop: str | None = None
    brew: str | None = None
    brew_cask: bool = False


HOST_PACKAGES = (
    HostPackage("nmap", "core", "nmap", "nmap"),
    HostPackage("openssl", "core", "openssl", "openssl@3"),
    HostPackage("adb", "core", "adb", "android-platform-tools", True),
    HostPackage("apktool", "core", "apktool", "apktool"),
    HostPackage("r2", "core", "radare2", "radare2"),
    HostPackage("yara", "core", "yara", "yara"),
    HostPackage("kubectl", "core", "kubectl", "kubernetes-cli"),
    HostPackage("cmake", "core", "cmake", "cmake"),
    HostPackage("jadx", "full", "jadx", "jadx"),
    HostPackage("ghidra", "full", "ghidra", "ghidra", True),
    HostPackage("wireshark", "full", "wireshark", "wireshark-app", True),
    HostPackage("docker", "full", "rancher-desktop", "docker", True),
    HostPackage("docker-compose", "full", "docker-compose", None),
    HostPackage("strings", "full", "sysinternals", None),
    HostPackage("objdump", "full", "gcc", "binutils"),
    HostPackage("readelf", "full", "gcc", "binutils"),
    HostPackage("gcc", "full", "gcc", "gcc"),
    HostPackage("ida", "full", "ida-free", "ida-free", True),
)

PYTHON_PACKAGES = (
    ("frida", "core", "frida-tools"),
    ("pycryptodome", "core", "pycryptodome"),
    ("pwntools", "full", "pwntools"),
)


class ToolchainError(RuntimeError):
    """Raised when the bundled core cannot be deployed safely."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _profile_includes(selected: str, required: str) -> bool:
    rank = {"none": 0, "core": 1, "full": 2}
    return rank[selected] >= rank[required]


def _zip_parts(name: str) -> tuple[str, ...]:
    portable = PurePosixPath(name)
    parts = tuple(part for part in portable.parts if part not in ("", "."))
    if not parts and name.rstrip("/") in {"", "."}:
        return ()
    if portable.is_absolute() or not parts or ".." in parts:
        raise ToolchainError(f"Unsafe Code Sentinel archive path: {name!r}")
    if ":" in parts[0]:
        raise ToolchainError(f"Drive-qualified archive path: {name!r}")
    return parts


def _extract_archive(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = _zip_parts(member.filename)
            if not parts:
                continue
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ToolchainError(
                    f"Symbolic links are not accepted in the vendor archive: "
                    f"{member.filename}"
                )
            target = destination.joinpath(*parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member, "r") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def ensure_code_sentinel(
    runtime_root: str | Path | None = None,
    archive: str | Path | None = None,
) -> dict[str, object]:
    runtime = Path(runtime_root or DEFAULT_RUNTIME_ROOT).expanduser().absolute()
    source = Path(archive or CODE_SENTINEL_ARCHIVE).expanduser().absolute()
    if not source.is_file():
        raise FileNotFoundError(f"Bundled Code Sentinel archive not found: {source}")

    archive_sha256 = _sha256(source)
    version_file = runtime / "VERSION"
    runtime_complete = all((runtime / relative).is_file() for relative in RUNTIME_REQUIRED)
    marker_matches = False
    marker_path = runtime / VENDOR_MARKER
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker_matches = (
                marker.get("version") == CODE_SENTINEL_VERSION
                and marker.get("archive_sha256") == archive_sha256
            )
        except (json.JSONDecodeError, OSError, AttributeError):
            marker_matches = False
    if version_file.is_file() and runtime_complete and marker_matches:
        if version_file.read_text(encoding="utf-8").strip() == CODE_SENTINEL_VERSION:
            return {
                "runtime": str(runtime),
                "version": CODE_SENTINEL_VERSION,
                "reused": True,
                "archive_sha256": archive_sha256,
                "backup": None,
            }

    parent = runtime.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / f".code-sentinel-staging-{uuid.uuid4().hex}"
    backup: Path | None = None
    try:
        stage.mkdir()
        _extract_archive(source, stage)
        staged_version = stage / "VERSION"
        staged_complete = all((stage / relative).is_file() for relative in RUNTIME_REQUIRED)
        if not staged_complete or not staged_version.is_file():
            raise ToolchainError("Bundled Code Sentinel entry points are missing")
        actual_version = staged_version.read_text(encoding="utf-8").strip()
        if actual_version != CODE_SENTINEL_VERSION:
            raise ToolchainError(
                f"Code Sentinel version mismatch: {actual_version!r}"
            )
        (stage / VENDOR_MARKER).write_text(
            json.dumps(
                {
                    "version": CODE_SENTINEL_VERSION,
                    "archive_sha256": archive_sha256,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if runtime.exists():
            if runtime.is_symlink() or not runtime.is_dir():
                raise ToolchainError(f"Unsafe existing toolchain runtime: {runtime}")
            backups = parent / "backups"
            backups.mkdir(exist_ok=True)
            backup = backups / f"code-sentinel-{_utc_stamp()}-{uuid.uuid4().hex[:8]}"
            runtime.replace(backup)
        stage.replace(runtime)
    finally:
        if stage.exists():
            shutil.rmtree(stage)

    return {
        "runtime": str(runtime),
        "version": CODE_SENTINEL_VERSION,
        "reused": False,
        "archive_sha256": archive_sha256,
        "backup": str(backup) if backup else None,
    }


def _loopback_proxy_is_dead(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((parsed.hostname, port), timeout=0.75):
            return False
    except (OSError, ValueError):
        return True


def _network_environment() -> dict[str, str]:
    environment = os.environ.copy()
    proxy_names = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    if any(
        _loopback_proxy_is_dead(environment[name])
        for name in proxy_names
        if environment.get(name)
    ):
        for name in proxy_names:
            environment.pop(name, None)
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _command_result(
    command: list[str],
    *,
    cwd: Path | None = None,
    environment: dict[str, str] | None = None,
    stream: bool = False,
) -> dict[str, object]:
    if stream:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
        )
        output = ""
    else:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = completed.stdout
    return {
        "command": command,
        "returncode": completed.returncode,
        "output": output,
    }


def _json_command(command: list[str], cwd: Path) -> dict[str, object]:
    result = _command_result(
        command,
        cwd=cwd,
        environment=_network_environment(),
    )
    if result["returncode"] != 0:
        raise ToolchainError(
            f"Command failed ({result['returncode']}): {' '.join(command)}\n"
            f"{result['output']}"
        )
    try:
        payload = json.loads(str(result["output"]))
    except json.JSONDecodeError as exc:
        raise ToolchainError(f"Command did not return JSON: {command}") from exc
    if not isinstance(payload, dict):
        raise ToolchainError(f"Command returned an invalid JSON payload: {command}")
    return payload


def run_code_sentinel_doctor(runtime: Path) -> dict[str, object]:
    payload = _json_command(
        [sys.executable, str(runtime / "sentinel.py"), "doctor", "--json"],
        runtime,
    )
    summary = payload.get("summary")
    if not isinstance(summary, dict) or not summary.get("pass"):
        raise ToolchainError("Code Sentinel doctor did not pass")
    return payload


def inject_code_sentinel(
    runtime: Path,
    codex_homes: Iterable[str | Path],
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    injector = runtime / "scripts" / "codex_inject.py"
    for value in codex_homes:
        home = Path(value).expanduser().resolve()
        payload = _json_command(
            [
                sys.executable,
                str(injector),
                "install",
                "--codex-home",
                str(home),
                "--project-home",
                str(runtime),
                "--json",
            ],
            runtime,
        )
        if not payload.get("ok"):
            raise ToolchainError(f"Code Sentinel injection failed for {home}")
        results.append(payload)
    return results


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ShadowTrace-Arena"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=60) as response:
        content = response.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ToolchainError(f"Package-manager bootstrap script is too large: {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _scoop_prefix() -> list[str] | None:
    candidates = [
        shutil.which("scoop"),
        str(Path.home() / "scoop" / "shims" / "scoop.ps1"),
        str(Path.home() / "scoop" / "shims" / "scoop.cmd"),
    ]
    for raw in candidates:
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        if path.suffix.lower() == ".ps1":
            shell = _powershell()
            if shell:
                return [
                    shell,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(path),
                ]
        if path.suffix.lower() in {".cmd", ".bat"}:
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(path)]
        return [str(path)]
    return None


def _bootstrap_scoop(cache: Path, stream: bool) -> dict[str, object]:
    shell = _powershell()
    if not shell:
        return {"ok": False, "message": "PowerShell is required to install Scoop"}
    installer = cache / "install-scoop.ps1"
    try:
        _download(SCOOP_INSTALL_URL, installer)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    result = _command_result(
        [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(installer),
        ],
        environment=_network_environment(),
        stream=stream,
    )
    return {"ok": result["returncode"] == 0, **result}


def _brew_path() -> str | None:
    candidates = (
        shutil.which("brew"),
        "/opt/homebrew/bin/brew",
        "/usr/local/bin/brew",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def _bootstrap_brew(cache: Path, stream: bool) -> dict[str, object]:
    installer = cache / "install-homebrew.sh"
    try:
        _download(BREW_INSTALL_URL, installer)
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    environment = _network_environment()
    environment["NONINTERACTIVE"] = "1"
    result = _command_result(
        ["/bin/bash", str(installer)],
        environment=environment,
        stream=stream,
    )
    return {"ok": result["returncode"] == 0, **result}


def _install_python_packages(
    profile: str,
    stream: bool,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for capability, required, package in PYTHON_PACKAGES:
        if not _profile_includes(profile, required):
            continue
        result = _command_result(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                package,
            ],
            environment=_network_environment(),
            stream=stream,
        )
        results.append(
            {
                "capability": capability,
                "package": package,
                "manager": "pip",
                "ok": result["returncode"] == 0,
                "returncode": result["returncode"],
                "output": str(result["output"])[-4000:],
            }
        )
    return results


def _install_windows_packages(
    profile: str,
    runtime: Path,
    bootstrap: bool,
    stream: bool,
) -> tuple[str | None, list[dict[str, object]], dict[str, object] | None]:
    prefix = _scoop_prefix()
    bootstrap_result: dict[str, object] | None = None
    if prefix is None and bootstrap:
        bootstrap_result = _bootstrap_scoop(runtime.parent / "cache", stream)
        prefix = _scoop_prefix()
    if prefix is None:
        return None, [], bootstrap_result or {
            "ok": False,
            "message": "Scoop is unavailable; system tools were not installed",
        }

    environment = _network_environment()
    helper_steps = (
        [*prefix, "install", "git"],
        [*prefix, "bucket", "add", "extras"],
    )
    for command in helper_steps:
        _command_result(command, environment=environment, stream=stream)

    results: list[dict[str, object]] = []
    installed_packages: dict[str, dict[str, object]] = {}
    for item in HOST_PACKAGES:
        if not item.scoop or not _profile_includes(profile, item.profile):
            continue
        if item.scoop not in installed_packages:
            command_result = _command_result(
                [*prefix, "install", item.scoop],
                environment=environment,
                stream=stream,
            )
            installed_packages[item.scoop] = {
                "package": item.scoop,
                "manager": "scoop",
                "ok": command_result["returncode"] == 0,
                "returncode": command_result["returncode"],
                "output": str(command_result["output"])[-4000:],
            }
        results.append(
            {
                "capability": item.capability,
                **installed_packages[item.scoop],
            }
        )
    return "scoop", results, bootstrap_result


def _install_macos_packages(
    profile: str,
    runtime: Path,
    bootstrap: bool,
    stream: bool,
) -> tuple[str | None, list[dict[str, object]], dict[str, object] | None]:
    brew = _brew_path()
    bootstrap_result: dict[str, object] | None = None
    if brew is None and bootstrap:
        bootstrap_result = _bootstrap_brew(runtime.parent / "cache", stream)
        brew = _brew_path()
    if brew is None:
        return None, [], bootstrap_result or {
            "ok": False,
            "message": "Homebrew is unavailable; system tools were not installed",
        }

    environment = _network_environment()
    results: list[dict[str, object]] = []
    installed_packages: dict[tuple[str, bool], dict[str, object]] = {}
    for item in HOST_PACKAGES:
        if not item.brew or not _profile_includes(profile, item.profile):
            continue
        key = (item.brew, item.brew_cask)
        if key not in installed_packages:
            command = [brew, "install"]
            if item.brew_cask:
                command.append("--cask")
            command.append(item.brew)
            command_result = _command_result(
                command,
                environment=environment,
                stream=stream,
            )
            installed_packages[key] = {
                "package": item.brew,
                "manager": "homebrew-cask" if item.brew_cask else "homebrew",
                "ok": command_result["returncode"] == 0,
                "returncode": command_result["returncode"],
                "output": str(command_result["output"])[-4000:],
            }
        results.append(
            {
                "capability": item.capability,
                **installed_packages[key],
            }
        )
    return "homebrew", results, bootstrap_result


def _scoop_tool_paths(home: Path | None = None) -> list[Path]:
    root = (home or Path.home()) / "scoop"
    additions = [root / "shims"]
    apps = root / "apps"
    if not apps.is_dir():
        return additions

    for package in apps.iterdir():
        current = package / "current"
        if not current.is_dir():
            continue
        additions.extend((current, current / "bin", current / "cmd"))

    rancher = apps / "rancher-desktop" / "current" / "resources" / "resources"
    additions.extend((rancher / "win32" / "bin", rancher / "linux" / "bin"))
    return additions


def _tool_environment() -> dict[str, str]:
    environment = _network_environment()
    additions = [
        Path(sys.executable).parent,
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    if os.name == "nt":
        additions.extend(
            [
                *_scoop_tool_paths(),
                Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
                / "Docker"
                / "Docker"
                / "resources"
                / "bin",
                Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
                / "Wireshark",
                Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Nmap",
            ]
        )
    current = environment.get("PATH", "")
    environment["PATH"] = os.pathsep.join(
        [str(path) for path in additions if path.is_dir()] + [current]
    )
    return environment


def refresh_tool_index(runtime: Path) -> dict[str, object]:
    return _json_command_with_environment(
        [sys.executable, str(runtime / "scripts" / "ops_tool_index.py"), "refresh"],
        runtime,
        _tool_environment(),
    )


def _json_command_with_environment(
    command: list[str],
    cwd: Path,
    environment: dict[str, str],
) -> dict[str, object]:
    result = _command_result(command, cwd=cwd, environment=environment)
    if result["returncode"] != 0:
        raise ToolchainError(
            f"Command failed ({result['returncode']}): {' '.join(command)}\n"
            f"{result['output']}"
        )
    try:
        payload = json.loads(str(result["output"]))
    except json.JSONDecodeError as exc:
        raise ToolchainError(f"Command did not return JSON: {command}") from exc
    if not isinstance(payload, dict):
        raise ToolchainError(f"Command returned an invalid JSON payload: {command}")
    return payload


def deploy(
    *,
    profile: str = "full",
    runtime_root: str | Path | None = None,
    codex_homes: Iterable[str | Path] | None = None,
    skip_codex: bool = False,
    bootstrap_package_manager: bool = True,
    stream_installs: bool = False,
    emit: Callable[[str], None] | None = None,
) -> dict[str, object]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown toolchain profile: {profile}")
    notify = emit or (lambda _message: None)

    notify("Preparing bundled Code Sentinel core")
    core = ensure_code_sentinel(runtime_root)
    runtime = Path(str(core["runtime"]))

    notify("Running Code Sentinel doctor")
    doctor = run_code_sentinel_doctor(runtime)

    homes = [] if skip_codex else list(codex_homes or discover_codex_homes())
    notify(f"Injecting Code Sentinel into {len(homes)} Codex configuration(s)")
    injections = inject_code_sentinel(runtime, homes)

    install_results: list[dict[str, object]] = []
    manager: str | None = None
    bootstrap_result: dict[str, object] | None = None
    if profile != "none":
        notify(f"Installing {profile} Python security packages")
        install_results.extend(_install_python_packages(profile, stream_installs))
        notify(f"Installing {profile} host security tools")
        if sys.platform == "win32":
            manager, host_results, bootstrap_result = _install_windows_packages(
                profile,
                runtime,
                bootstrap_package_manager,
                stream_installs,
            )
            install_results.extend(host_results)
        elif sys.platform == "darwin":
            manager, host_results, bootstrap_result = _install_macos_packages(
                profile,
                runtime,
                bootstrap_package_manager,
                stream_installs,
            )
            install_results.extend(host_results)

    notify("Refreshing the Code Sentinel tool index")
    index = refresh_tool_index(runtime)
    tools = index.get("tools") if isinstance(index.get("tools"), list) else []
    missing = [
        item
        for item in tools
        if isinstance(item, dict) and not item.get("available")
    ]
    failed = [item for item in install_results if not item.get("ok")]
    return {
        "ok": True,
        "core": core,
        "doctor": doctor.get("summary"),
        "codex_homes": [str(Path(home)) for home in homes],
        "injections": injections,
        "toolchain": {
            "profile": profile,
            "manager": manager,
            "bootstrap": bootstrap_result,
            "install_results": install_results,
            "failed_installs": failed,
            "index_summary": index.get("summary"),
            "missing": missing,
        },
    }


def compact_report(payload: dict[str, object]) -> dict[str, object]:
    core = payload.get("core") if isinstance(payload.get("core"), dict) else {}
    toolchain = (
        payload.get("toolchain")
        if isinstance(payload.get("toolchain"), dict)
        else {}
    )
    missing = toolchain.get("missing") if isinstance(toolchain, dict) else []
    failed = toolchain.get("failed_installs") if isinstance(toolchain, dict) else []
    return {
        "ok": payload.get("ok"),
        "code_sentinel_version": core.get("version"),
        "runtime": core.get("runtime"),
        "runtime_reused": core.get("reused"),
        "doctor": payload.get("doctor"),
        "codex_homes": payload.get("codex_homes"),
        "toolchain_profile": toolchain.get("profile"),
        "package_manager": toolchain.get("manager"),
        "index_summary": toolchain.get("index_summary"),
        "failed_installs": [
            {
                "capability": item.get("capability"),
                "package": item.get("package"),
                "returncode": item.get("returncode"),
            }
            for item in failed
            if isinstance(item, dict)
        ],
        "missing": [item.get("id") for item in missing if isinstance(item, dict)],
    }
