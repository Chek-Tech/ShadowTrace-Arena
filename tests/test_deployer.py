import json
import tomllib
from pathlib import Path

from shadowtrace_arena import deployer
from shadowtrace_arena.product import BRIDGE_SOURCE


def make_codex_home(tmp_path: Path, config: str) -> Path:
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_text(config, encoding="utf-8")
    unrelated = home / "skills" / "user-owned-skill"
    unrelated.mkdir(parents=True)
    (unrelated / "marker.txt").write_text("keep", encoding="utf-8")
    return home


def test_install_and_remove_preserve_unrelated_skills_and_previous_config(
    tmp_path: Path,
) -> None:
    home = make_codex_home(
        tmp_path,
        'model = "gpt-test"\nmodel_instructions_file = "C:/old/bridge.md"\n',
    )

    installed = deployer.install(home)
    assert installed.installed is True
    assert len(installed.skills) == 28
    assert (home / "skills" / "user-owned-skill" / "marker.txt").is_file()
    assert all(name.startswith("shadowtrace-arena-") for name in installed.skills)
    assert (home / "shadowtrace-arena" / "bridge.md").read_bytes() == (
        BRIDGE_SOURCE.read_bytes()
    )

    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert config["model_instructions_file"].endswith(
        "shadowtrace-arena/bridge.md"
    )
    manifest = json.loads(
        (home / "shadowtrace-arena" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["previous_instruction_line"].endswith('"C:/old/bridge.md"')

    removed = deployer.remove(home)
    assert removed.installed is False
    assert (home / "skills" / "user-owned-skill" / "marker.txt").is_file()
    assert not (home / "shadowtrace-arena").exists()
    restored = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert restored["model_instructions_file"] == "C:/old/bridge.md"


def test_install_and_remove_when_no_previous_instruction(tmp_path: Path) -> None:
    home = make_codex_home(tmp_path, 'model = "gpt-test"\n[profiles.dev]\nmodel = "gpt-test"\n')
    deployer.install(home)
    deployer.remove(home)

    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert "model_instructions_file" not in config
    assert config["profiles"]["dev"]["model"] == "gpt-test"


def test_install_refuses_unowned_namespace_collision(tmp_path: Path) -> None:
    home = make_codex_home(tmp_path, 'model = "gpt-test"\n')
    collision = home / "skills" / "shadowtrace-arena-ctf-workspace"
    collision.mkdir()

    try:
        deployer.install(home)
    except FileExistsError as exc:
        assert "unowned skill directory" in str(exc)
    else:
        raise AssertionError("Expected an unowned namespace collision")
    assert collision.is_dir()
    assert not (home / "shadowtrace-arena").exists()
    assert not (home / "skills" / "shadowtrace-arena-artifact-triage").exists()
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert "model_instructions_file" not in config


def test_relay_configuration_round_trip(tmp_path: Path) -> None:
    home = make_codex_home(
        tmp_path,
        'model = "gpt-test"\n[model_providers.lab]\nbase_url = "https://relay.test/v1"\n',
    )

    configured = deployer.configure_relay(home)
    assert configured["upstream"] == "https://relay.test/v1"
    assert deployer.discover_upstream(home) == "https://relay.test/v1"
    changed = (home / "config.toml").read_text(encoding="utf-8")
    assert 'base_url = "http://127.0.0.1:8080/v1"' in changed

    restored = deployer.restore_relay(home)
    assert restored["changed"] is True
    original = (home / "config.toml").read_text(encoding="utf-8")
    assert 'base_url = "https://relay.test/v1"' in original


def test_inspection_detects_managed_content_conflict_and_repair(tmp_path: Path) -> None:
    home = make_codex_home(tmp_path, 'model = "gpt-test"\n')
    deployer.install(home)
    healthy = deployer.inspect_deployment(home)
    assert healthy.state == "healthy"

    bridge = home / "shadowtrace-arena" / "bridge.md"
    bridge.write_text("changed", encoding="utf-8")
    conflict = deployer.inspect_deployment(home)
    assert conflict.state == "conflict"
    assert any("managed file changed" in issue for issue in conflict.issues)

    preview = deployer.preview_install(home)
    assert preview["state"] == "conflict"
    assert len(preview["skills"]) == 28
    deployer.repair(home)
    assert deployer.inspect_deployment(home).state == "healthy"


def test_existing_install_transaction_can_be_completed(tmp_path: Path) -> None:
    home = make_codex_home(tmp_path, 'model = "gpt-test"\n')
    deployer.install(home)
    manifest_path = home / "shadowtrace-arena" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    transaction = home / "shadowtrace-arena" / "transaction.json"
    transaction.write_text(
        json.dumps(
            {
                "product": "shadowtrace-arena",
                "action": "install",
                "phase": "resources-written",
                "existing_manifest": manifest,
                "target_skills": manifest["installed_skills"],
            }
        ),
        encoding="utf-8",
    )

    assert deployer.inspect_deployment(home).state == "recovery-required"
    recovered = deployer.recover_transaction(home)
    assert recovered["completed"] == "install"
    assert deployer.inspect_deployment(home).state == "healthy"
