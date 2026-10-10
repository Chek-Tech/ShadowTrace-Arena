from shadowtrace_arena.product import PROJECT_ROOT


def test_platform_deployment_entry_points_are_complete() -> None:
    windows_wrapper = PROJECT_ROOT / "deploy-windows.bat"
    windows_script = PROJECT_ROOT / "scripts" / "deploy-windows.ps1"
    macos_script = PROJECT_ROOT / "deploy-macos.command"

    assert windows_wrapper.is_file()
    assert windows_script.is_file()
    assert macos_script.is_file()

    windows = windows_script.read_text(encoding="utf-8")
    macos = macos_script.read_text(encoding="utf-8")
    for content in (windows, macos):
        assert ".venv" in content
        assert "pip" in content
        assert "shadowtrace_arena" in content
        assert "doctor" in content
        assert "install" in content
        assert "toolchain" in content.lower()
        assert "Code Sentinel" in content


def test_runtime_launchers_prefer_the_managed_environment() -> None:
    launchers = (
        PROJECT_ROOT / "scripts" / "control-center.bat",
        PROJECT_ROOT / "scripts" / "control-center.ps1",
        PROJECT_ROOT / "scripts" / "shadowtrace.bat",
        PROJECT_ROOT / "scripts" / "shadowtrace.ps1",
        PROJECT_ROOT / "launch-macos.command",
    )
    for launcher in launchers:
        assert launcher.is_file()
        assert ".venv" in launcher.read_text(encoding="utf-8")
