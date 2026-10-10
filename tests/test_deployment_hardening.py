import json
from pathlib import Path

import pytest

from shadowtrace_arena import deployer
from shadowtrace_arena import deployment_engine as engine


def make_home(tmp_path: Path, name: str = ".codex") -> Path:
    home = tmp_path / name
    home.mkdir()
    (home / "config.toml").write_text(
        'model = "gpt-test"\nmodel_instructions_file = "C:/old/rules.md"\n',
        encoding="utf-8",
    )
    return home


def test_hooks_round_trip_preserves_both_original_files(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    (home / "hooks.json").write_bytes(b'{"active":true}\n')
    (home / "hooks.json.disabled").write_bytes(b'{"old":true}\n')

    deployer.install(home)

    assert not (home / "hooks.json").exists()
    assert (home / "hooks.json.disabled").read_bytes() == b'{"active":true}\n'
    assert (home / "shadowtrace-arena" / "hooks.disabled.before").read_bytes() == (
        b'{"old":true}\n'
    )
    assert deployer.inspect_deployment(home).state == "healthy"

    deployer.remove(home)

    assert (home / "hooks.json").read_bytes() == b'{"active":true}\n'
    assert (home / "hooks.json.disabled").read_bytes() == b'{"old":true}\n'
    assert deployer.inspect_deployment(home).state == "not-installed"


def test_explicit_hook_restore_updates_managed_state(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    (home / "hooks.json").write_bytes(b'{"active":true}\n')
    deployer.install(home)

    result = deployer.restore_hooks(home)

    assert result["restored"] is True
    assert (home / "hooks.json").read_bytes() == b'{"active":true}\n'
    assert not (home / "hooks.json.disabled").exists()
    assert deployer.inspect_deployment(home).state == "healthy"
    deployer.remove(home)
    assert (home / "hooks.json").read_bytes() == b'{"active":true}\n'


def test_skip_hook_isolation_is_explicit_and_reversible(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    (home / "hooks.json").write_bytes(b'{"active":true}\n')

    deployer.install(home, skip_hooks_isolation=True)
    manifest = json.loads(
        (home / "shadowtrace-arena" / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["hooks"]["mode"] == "skipped"
    assert (home / "hooks.json").is_file()
    deployer.remove(home)
    assert (home / "hooks.json").read_bytes() == b'{"active":true}\n'


def test_interrupted_install_leaves_recoverable_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = make_home(tmp_path)
    original_write = engine.atomic_write_text
    original_rollback = engine._rollback_transaction

    def fail_config(path: str | Path, content: str, **kwargs: object):
        if Path(path) == home / "config.toml":
            raise OSError("fault injection")
        return original_write(path, content, **kwargs)

    def fail_rollback(_transaction: object):
        raise OSError("simulated power loss")

    monkeypatch.setattr(engine, "atomic_write_text", fail_config)
    monkeypatch.setattr(engine, "_rollback_transaction", fail_rollback)
    with pytest.raises(deployer.RecoveryRequired):
        deployer.install(home)

    assert deployer.inspect_deployment(home).state == "recovery-required"
    monkeypatch.setattr(engine, "atomic_write_text", original_write)
    monkeypatch.setattr(engine, "_rollback_transaction", original_rollback)

    recovered = deployer.recover_transaction(home)
    assert recovered["completed"] == "install-rollback"
    assert deployer.inspect_deployment(home).state == "not-installed"
    assert "C:/old/rules.md" in (home / "config.toml").read_text(encoding="utf-8")


def test_unknown_config_change_blocks_recovery(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    transaction = engine._Transaction.begin(home, "install", None, [])
    transaction.update(
        phase="staged",
        planned={
            "config_sha256": "0" * 64,
            "product": None,
            "skills": {},
            "hooks": {"hooks.json": None, "hooks.json.disabled": None},
        },
    )
    (home / "config.toml").write_text('model = "external-change"\n', encoding="utf-8")

    with pytest.raises(deployer.DeploymentConflict, match="Unknown config"):
        deployer.recover_transaction(home)

    assert deployer.inspect_deployment(home).state == "recovery-required"


def test_multi_home_install_rolls_back_earlier_participant(tmp_path: Path) -> None:
    first = make_home(tmp_path, "first")
    second = make_home(tmp_path, "second")
    collision = second / "skills" / "shadowtrace-arena-ctf-workspace"
    collision.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        deployer.install_many([first, second])

    assert deployer.inspect_deployment(first).state == "not-installed"
    assert "C:/old/rules.md" in (first / "config.toml").read_text(encoding="utf-8")
    assert collision.is_dir()


def test_manifest_path_escape_is_blocked(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    deployer.install(home)
    manifest_path = home / "shadowtrace-arena" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["managed_files"]["../outside.txt"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    inspection = deployer.inspect_deployment(home)
    assert inspection.state == "blocked"
    assert any("escapes" in issue for issue in inspection.issues)


def test_relay_restore_preserves_unknown_external_change(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    with (home / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('[model_providers.lab]\nbase_url = "https://upstream.test/v1"\n')
    deployer.configure_relay(home)
    config = (home / "config.toml").read_text(encoding="utf-8")
    config = config.replace(
        "http://127.0.0.1:8080/v1", "https://external-change.test/v1"
    )
    (home / "config.toml").write_text(config, encoding="utf-8")

    with pytest.raises(deployer.DeploymentConflict, match="relay line changed"):
        deployer.restore_relay(home)

    assert (home / "shadowtrace-arena" / "relay-config.json").is_file()
    assert "external-change.test" in (home / "config.toml").read_text(
        encoding="utf-8"
    )


def test_custom_bridge_is_validated_and_managed(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    bridge = tmp_path / "operator-bridge.md"
    bridge.write_text("# Local operator contract\n\nEvidence first.\n", encoding="utf-8")

    deployer.install(home, bridge_file=bridge)

    installed = home / "shadowtrace-arena" / "bridge.md"
    assert installed.read_bytes() == bridge.read_bytes()
    assert deployer.inspect_deployment(home).state == "healthy"
    deployer.remove(home)
