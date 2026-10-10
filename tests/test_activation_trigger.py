from shadowtrace_arena.doctor import report
from shadowtrace_arena.product import BRIDGE_SOURCE


def test_pikachu_activation_contract_is_packaged() -> None:
    bridge = BRIDGE_SOURCE.read_text(encoding="utf-8")

    assert "exactly `皮卡丘`" in bridge
    assert "皮卡丘已就位，破甲模式已激活。" in bridge
    assert "exactly `皮卡丘退下` or `ST-OFF`" in bridge
    assert "破甲模式已关闭。" in bridge


def test_doctor_checks_the_activation_contract() -> None:
    checks = {item["name"]: item for item in report()["checks"]}

    assert checks["activation_trigger"]["level"] == "pass"
