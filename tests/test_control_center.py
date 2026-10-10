import sys

from shadowtrace_arena.control_center import relay_command


def test_relay_command_is_bounded_and_complete() -> None:
    command = relay_command("https://api.example.test", True, 99, 8090)
    assert command[:4] == [sys.executable, "-m", "shadowtrace_arena", "relay"]
    assert command[command.index("--max-retries") + 1] == "8"
    assert command[command.index("--dashboard-port") + 1] == "8090"
    assert "--continuity" in command
