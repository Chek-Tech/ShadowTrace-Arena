"""Deterministic local health checks for ShadowTrace Arena."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass

from .capabilities import CAPABILITIES
from .continuity import CONTINUITY_RULES
from .deployer import status
from .deployment_engine import MANIFEST_SCHEMA, TRANSACTION_SCHEMA
from .product import (
    BRIDGE_SOURCE,
    PRODUCT_NAME,
    SKILLPACK_SOURCE,
    VERSION,
    PROJECT_ROOT,
)
from .toolchain import (
    CODE_SENTINEL_ARCHIVE,
    CODE_SENTINEL_VERSION,
    DEFAULT_RUNTIME_ROOT,
    RUNTIME_REQUIRED,
)


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    message: str


def collect_checks(codex_home: str | None = None) -> list[Check]:
    checks: list[Check] = []
    python_ok = sys.version_info >= (3, 11)
    checks.append(
        Check(
            "python",
            "pass" if python_ok else "fail",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )
    checks.append(
        Check(
            "bridge",
            "pass" if BRIDGE_SOURCE.is_file() else "fail",
            str(BRIDGE_SOURCE),
        )
    )
    bridge_text = (
        BRIDGE_SOURCE.read_text(encoding="utf-8") if BRIDGE_SOURCE.is_file() else ""
    )
    activation_markers = (
        "`皮卡丘`",
        "皮卡丘已就位，破甲模式已激活。",
        "`皮卡丘退下`",
        "`ST-OFF`",
    )
    activation_ok = all(marker in bridge_text for marker in activation_markers)
    checks.append(
        Check(
            "activation_trigger",
            "pass" if activation_ok else "fail",
            "Pikachu trigger -> ShadowTrace FULL mode",
        )
    )
    bundle_ok = CODE_SENTINEL_ARCHIVE.is_file() and CODE_SENTINEL_ARCHIVE.stat().st_size > 0
    checks.append(
        Check(
            "code_sentinel_bundle",
            "pass" if bundle_ok else "fail",
            f"Code Sentinel {CODE_SENTINEL_VERSION} vendor archive",
        )
    )
    runtime_exists = DEFAULT_RUNTIME_ROOT.exists()
    runtime_ok = runtime_exists and all(
        (DEFAULT_RUNTIME_ROOT / relative).is_file() for relative in RUNTIME_REQUIRED
    )
    runtime_level = "pass" if runtime_ok else ("fail" if runtime_exists else "info")
    runtime_message = (
        str(DEFAULT_RUNTIME_ROOT)
        if runtime_ok
        else ("runtime incomplete" if runtime_exists else "deployed by one-click setup")
    )
    checks.append(
        Check(
            "code_sentinel_runtime",
            runtime_level,
            runtime_message,
        )
    )
    skill_count = (
        sum(1 for path in SKILLPACK_SOURCE.iterdir() if (path / "SKILL.md").is_file())
        if SKILLPACK_SOURCE.is_dir()
        else 0
    )
    checks.append(
        Check(
            "skillpacks",
            "pass" if skill_count == 28 else "fail",
            f"{skill_count} original packs",
        )
    )
    checks.append(
        Check(
            "capabilities",
            "pass" if len(CAPABILITIES) == 78 else "fail",
            f"{len(CAPABILITIES)} named integrations",
        )
    )
    checks.append(
        Check(
            "continuity_rules",
            "pass" if len(CONTINUITY_RULES) == 23 else "fail",
            f"{len(CONTINUITY_RULES)} original rules",
        )
    )
    httpx_available = importlib.util.find_spec("httpx") is not None
    checks.append(
        Check(
            "relay_dependency",
            "pass" if httpx_available else "warn",
            "httpx available" if httpx_available else "pip install -e .",
        )
    )
    tkinter_available = importlib.util.find_spec("tkinter") is not None
    checks.append(
        Check(
            "control_center",
            "pass" if tkinter_available else "warn",
            "tkinter available" if tkinter_available else "Tkinter is not installed",
        )
    )
    deployment_schemas_ok = MANIFEST_SCHEMA == 2 and TRANSACTION_SCHEMA == 2
    checks.append(
        Check(
            "guarded_deployment",
            "pass" if deployment_schemas_ok else "fail",
            (
                f"manifest v{MANIFEST_SCHEMA}, transaction v{TRANSACTION_SCHEMA}, "
                "hooks and race-aware I/O"
            ),
        )
    )
    launchers = (
        PROJECT_ROOT / "deploy-windows.bat",
        PROJECT_ROOT / "scripts" / "deploy-windows.ps1",
        PROJECT_ROOT / "deploy-macos.command",
        PROJECT_ROOT / "scripts" / "control-center.bat",
        PROJECT_ROOT / "scripts" / "control-center.ps1",
        PROJECT_ROOT / "launch-macos.command",
    )
    launcher_count = sum(path.is_file() for path in launchers)
    checks.append(
        Check(
            "platform_launchers",
            "pass" if launcher_count == len(launchers) else "fail",
            f"{launcher_count}/{len(launchers)} Windows/macOS launchers",
        )
    )
    deployment = status(codex_home)
    checks.append(
        Check(
            "codex_deployment",
            "pass" if deployment.installed else "info",
            deployment.message,
        )
    )
    return checks


def report(codex_home: str | None = None) -> dict[str, object]:
    checks = collect_checks(codex_home)
    return {
        "product": PRODUCT_NAME,
        "version": VERSION,
        "ok": not any(check.level == "fail" for check in checks),
        "checks": [asdict(check) for check in checks],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Verify {PRODUCT_NAME}")
    parser.add_argument("--codex-home")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = report(args.codex_home)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{PRODUCT_NAME} {VERSION}")
        for item in payload["checks"]:
            print(f"[{item['level'].upper():4}] {item['name']}: {item['message']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
