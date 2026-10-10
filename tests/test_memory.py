from shadowtrace_arena.memory import MemoryStore, categorize


def test_memory_store_is_bounded_and_persistent(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory.json", limit=2)
    store.record("web", "request one", "response one")
    store.record("reverse", "request two", "response two", ["C01"])
    payload = store.record("general", "request three", "response three")

    assert payload["stats"]["total"] == 3
    assert payload["stats"]["rewritten"] == 1
    assert len(payload["recent"]) == 2
    assert store.load()["recent"][-1]["request_preview"] == "request three"


def test_memory_category_routing() -> None:
    assert categorize("analyze this binary") == "reverse"
    assert categorize("inspect HTTP routes") == "web"
    assert categorize("plain note") == "general"
