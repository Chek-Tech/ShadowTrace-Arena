"""Persistent relay learning state with bounded, atomic storage."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


class MemoryStore:
    def __init__(self, path: str | Path, limit: int = 200):
        self.path = Path(path).expanduser().resolve()
        self.limit = max(1, min(int(limit), 10_000))
        self._lock = threading.Lock()

    @staticmethod
    def empty() -> dict[str, object]:
        return {"version": 1, "stats": {"total": 0, "rewritten": 0}, "recent": []}

    def load(self) -> dict[str, object]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else self.empty()
        except (OSError, json.JSONDecodeError):
            return self.empty()

    def record(
        self,
        category: str,
        request_text: str,
        response_text: str,
        rule_ids: list[str] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            payload = self.load()
            stats = payload.setdefault("stats", {})
            recent = payload.setdefault("recent", [])
            stats["total"] = int(stats.get("total", 0)) + 1
            stats[category] = int(stats.get(category, 0)) + 1
            if rule_ids:
                stats["rewritten"] = int(stats.get("rewritten", 0)) + 1
            recent.append(
                {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "category": category,
                    "request_hash": hashlib.sha256(request_text.encode()).hexdigest()[:16],
                    "request_preview": request_text[:200],
                    "response_preview": response_text[:300],
                    "rules": list(rule_ids or []),
                }
            )
            payload["recent"] = recent[-self.limit :]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
            return payload


def categorize(text: str) -> str:
    lowered = text.lower()
    groups = {
        "license": ("license", "serial", "activate", "注册", "激活", "授权"),
        "reverse": ("reverse", "binary", "disassemble", "逆向", "反汇编"),
        "web": ("web", "http", "sqli", "xss", "网站", "接口"),
        "network": ("network", "port", "service", "网络", "端口"),
    }
    for category, words in groups.items():
        if any(word in lowered for word in words):
            return category
    return "general"
