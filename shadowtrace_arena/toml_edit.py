"""Formatting-preserving TOML edits for narrowly managed keys."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass


class TomlEditError(ValueError):
    """Raised when a TOML document cannot be changed without ambiguity."""


@dataclass(frozen=True)
class RootStatement:
    start: int
    end: int
    path: tuple[str, ...]


@dataclass(frozen=True)
class RootDocument:
    statements: tuple[RootStatement, ...]
    first_table: int | None
    newline: str


def _line_end(content: str, start: int) -> int:
    index = content.find("\n", start)
    return len(content) if index < 0 else index + 1


def _first_unquoted_equals(line: str) -> int | None:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if quote == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif quote == "'":
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "#":
            return None
        elif character == "=":
            return index
    return None


def _single_assignment_path(raw_key: str) -> tuple[str, ...]:
    try:
        parsed = tomllib.loads(f"{raw_key} = 0\n")
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(f"Unsupported TOML key syntax: {raw_key.strip()}") from exc
    path: list[str] = []
    value: object = parsed
    while isinstance(value, dict) and len(value) == 1:
        key, value = next(iter(value.items()))
        path.append(key)
    if value != 0 or not path:
        raise TomlEditError(f"Ambiguous TOML key syntax: {raw_key.strip()}")
    return tuple(path)


def _complete_statement(content: str, start: int) -> int:
    end = _line_end(content, start)
    while True:
        candidate = content[start:end]
        try:
            tomllib.loads(candidate)
            return end
        except tomllib.TOMLDecodeError:
            if end >= len(content):
                raise TomlEditError("A top-level TOML statement is incomplete")
            end = _line_end(content, end)


def analyze_root(content: str) -> RootDocument:
    if "\r" in content.replace("\r\n", ""):
        raise TomlEditError("Standalone CR newlines are not supported")
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(f"config.toml is invalid: {exc}") from exc

    newline = "\r\n" if "\r\n" in content else "\n"
    statements: list[RootStatement] = []
    first_table: int | None = None
    cursor = 0
    while cursor < len(content):
        end = _line_end(content, cursor)
        physical = content[cursor:end]
        leading = len(physical) - len(physical.lstrip(" \t"))
        stripped = physical[leading:]
        if not stripped.strip() or stripped.startswith("#"):
            cursor = end
            continue
        if stripped.startswith("["):
            first_table = cursor
            break
        equals = _first_unquoted_equals(physical.rstrip("\r\n"))
        if equals is None:
            raise TomlEditError("Unrecognized top-level TOML statement")
        path = _single_assignment_path(physical[:equals].strip())
        statement_end = _complete_statement(content, cursor)
        statements.append(RootStatement(cursor, statement_end, path))
        cursor = statement_end
    return RootDocument(tuple(statements), first_table, newline)


def _root_value(content: str, key: str) -> object | None:
    parsed = tomllib.loads(content)
    return parsed.get(key)


def find_root_string(content: str, key: str) -> str | None:
    analysis = analyze_root(content)
    for statement in analysis.statements:
        if statement.path[0] == key and len(statement.path) > 1:
            raise TomlEditError(f"{key} is already used as a dotted-key namespace")
    value = _root_value(content, key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TomlEditError(f"{key} must be a string")
    return value


def set_root_string(
    content: str,
    key: str,
    value: str | None,
) -> tuple[str, str | None, bool]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise TomlEditError(f"Managed key must be bare: {key}")
    analysis = analyze_root(content)
    target: RootStatement | None = None
    for statement in analysis.statements:
        if statement.path[0] != key:
            continue
        if len(statement.path) > 1:
            raise TomlEditError(f"{key} is already used as a dotted-key namespace")
        target = statement
    previous = find_root_string(content, key)
    if previous == value:
        return content, previous, False

    if target is not None:
        replacement = "" if value is None else f"{key} = {json.dumps(value, ensure_ascii=False)}{analysis.newline}"
        updated = content[: target.start] + replacement + content[target.end :]
    elif value is None:
        return content, previous, False
    else:
        insertion = analysis.first_table if analysis.first_table is not None else len(content)
        prefix = ""
        if insertion > 0 and not content[:insertion].endswith(("\n", "\r")):
            prefix = analysis.newline
        line = f"{key} = {json.dumps(value, ensure_ascii=False)}{analysis.newline}"
        updated = content[:insertion] + prefix + line + content[insertion:]

    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(f"Managed edit produced invalid TOML: {exc}") from exc
    return updated, previous, True
