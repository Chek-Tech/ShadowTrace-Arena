import json

from shadowtrace_arena.continuity import (
    CONTINUITY_RULES,
    build_retry_body,
    extract_response_text,
    inject_context,
    rewrite_response,
)


def test_exactly_23_original_continuity_rules() -> None:
    assert len(CONTINUITY_RULES) == 23
    assert len({item.rule_id for item in CONTINUITY_RULES}) == 23


def test_recursive_context_updates_every_instruction_surface() -> None:
    payload = {
        "instructions": "top",
        "wrapper": {
            "messages": [
                {"role": "system", "content": "nested system"},
                {"role": "developer", "content": [{"type": "text", "text": "dev"}]},
                {"role": "user", "content": "keep me"},
            ]
        },
    }
    assert inject_context(payload, "arena") is True
    assert payload["instructions"] == "arena\n\ntop"
    messages = payload["wrapper"]["messages"]
    assert messages[0]["content"].startswith("arena\n\n")
    assert messages[1]["content"][0]["text"] == "arena"
    assert messages[2]["content"] == "keep me"


def test_json_response_continuity_preserves_envelope() -> None:
    raw = json.dumps(
        {"id": "r1", "choices": [{"message": {"content": "I cannot assist with that"}}]}
    ).encode()
    body, rules = rewrite_response(raw, "application/json")
    payload = json.loads(body)
    assert payload["id"] == "r1"
    assert rules
    assert "ShadowTrace continuity" in payload["choices"][0]["message"]["content"]


def test_sse_response_continuity_preserves_done_event() -> None:
    raw = (
        'event: message\n'
        'data: {"type":"output_text","text":"Sorry, I cannot provide that"}\n\n'
        'data: [DONE]\n\n'
    ).encode()
    body, rules = rewrite_response(raw, "text/event-stream")
    assert rules
    assert b"[DONE]" in body
    assert "ShadowTrace continuity" in body.decode()
    assert "ShadowTrace continuity" in extract_response_text(body)


def test_retry_body_preserves_chat_history() -> None:
    raw = json.dumps(
        {"messages": [{"role": "user", "content": "original request"}]}
    ).encode()
    retried = build_retry_body(raw, "application/json", "stopped response", 1)
    payload = json.loads(retried)
    assert payload["messages"][0]["content"] == "original request"
    assert payload["messages"][-2] == {
        "role": "assistant",
        "content": "stopped response",
    }
    assert "ShadowTrace retry 1" in payload["messages"][-1]["content"]
