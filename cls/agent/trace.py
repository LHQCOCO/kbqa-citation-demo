"""JSONL trace：事件白名单 + 内容脱敏，逐条 flush。

脱敏原则：trace 只服务于回放与排障，不保存原始文档、文件名、绝对路径。
调用方传 ``args`` 时自动折算为 8 位摘要；任何字符串超过 64 字符只留摘要与长度。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

EVENT_NAMES = frozenset({
    "run.start", "plan.created", "step.start", "tool.call", "tool.result",
    "role.message", "verify.result", "step.end", "run.end",
})

# 公共字段（允许写入 trace 的键）
SAFE_FIELDS = frozenset({
    "ts", "run_id", "seq", "event", "step_id", "role", "tool", "args_digest",
    "latency_ms", "tokens_in", "tokens_out", "ok", "error_code", "fallback",
    # 事件专用但同样安全的标量
    "stop_reason", "status", "count", "version", "reason", "code", "detail",
    "verdict", "reason_code", "rule_id", "truncated", "steps", "tool_calls", "tokens",
    "mode", "format", "sha8", "span_start", "span_end", "attempt", "phase",
    "confirmed", "downgraded", "rejected",
})

# 明令禁止的键：出现即折算，绝不落原文
_BLOCKED_KEYS = frozenset({
    "text", "content", "quote", "filename", "path", "file", "doc_text",
    "contract_text", "messages", "prompt", "args_raw", "data", "user_data",
})


def _digest(value: Any) -> str:
    if isinstance(value, (dict, list)):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _looks_like_path(value: str) -> bool:
    return value.startswith(("/", "~/", "\\\\")) or (
        len(value) > 2 and value[1] == ":" and value[2] in ("\\", "/")
    )


def _safe_scalar(key: str, value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return f"sha8:{_digest(value)}"
    text = str(value)
    if _looks_like_path(text):
        return f"path_sha8:{_digest(text)}"
    if len(text) > 64:
        return f"sha8:{_digest(text)} len:{len(text)}"
    return text.replace("\n", " ").replace("\r", " ")


def _sanitize(fields: dict) -> dict:
    out: dict = {}
    for key, value in fields.items():
        if key == "args":
            out["args_digest"] = _digest(value)
            continue
        if key in _BLOCKED_KEYS:
            out[f"{key}_sha8"] = _digest(value)
            continue
        if key not in SAFE_FIELDS:
            continue  # 未登记字段直接丢弃，防止未来误传敏感内容
        out[key] = _safe_scalar(key, value)
    return out


class Trace:
    def __init__(self, path: str | Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0
        self.path.touch(exist_ok=True)

    def event(self, name: str, **fields: Any) -> dict:
        if name not in EVENT_NAMES:
            raise ValueError(f"未登记的 trace 事件：{name}")
        with self._lock:
            self._seq += 1
            record = {
                "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                "run_id": self.run_id,
                "seq": self._seq,
                "event": name,
            }
            record.update(_sanitize(fields))
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return record

    @contextmanager
    def span(self, name: str, **fields: Any) -> Iterator["Trace"]:
        """计时上下文：退出时写一条带 latency_ms 的事件。"""
        started = time.perf_counter()
        ok = True
        try:
            yield self
        except Exception:
            ok = False
            raise
        finally:
            self.event(
                name,
                latency_ms=int((time.perf_counter() - started) * 1000),
                ok=ok,
                **fields,
            )

    def dump(self, run_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        target = run_id or self.run_id
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("run_id") == target:
                events.append(item)
        return events
