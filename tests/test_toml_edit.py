import tomllib

import pytest

from shadowtrace_arena.toml_edit import TomlEditError, find_root_string, set_root_string


def test_root_edit_preserves_multiline_values_and_tables() -> None:
    source = (
        'banner = """\n[not-a-table]\n"""\n'
        '"model_instructions_file" = "C:/old/rules.md" # managed\n'
        "\n[profiles.dev]\n"
        'model = "gpt-test"\n'
    )

    updated, previous, changed = set_root_string(
        source,
        "model_instructions_file",
        "C:/new/rules.md",
    )

    assert changed is True
    assert previous == "C:/old/rules.md"
    assert "[not-a-table]" in updated
    assert '[profiles.dev]\nmodel = "gpt-test"' in updated
    assert tomllib.loads(updated)["model_instructions_file"] == "C:/new/rules.md"


def test_root_edit_rejects_dotted_namespace_collision() -> None:
    source = "model_instructions_file.child = 1\n"
    with pytest.raises(TomlEditError, match="dotted-key namespace"):
        set_root_string(source, "model_instructions_file", "C:/rules.md")


def test_root_edit_removes_only_the_managed_statement() -> None:
    source = (
        'model = "gpt-test"\r\n'
        'model_instructions_file = "C:/rules.md"\r\n'
        "[profiles.dev]\r\n"
        'model = "gpt-test"\r\n'
    )
    updated, previous, changed = set_root_string(
        source, "model_instructions_file", None
    )
    assert changed is True
    assert previous == "C:/rules.md"
    assert find_root_string(updated, "model_instructions_file") is None
    assert "[profiles.dev]\r\n" in updated

