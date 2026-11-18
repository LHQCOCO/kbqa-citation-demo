"""运行上下文与记忆存储。

MemoryStore 只需 SQLite 实现：单表 memory，召回用 TF + 长度归一，不引入向量库。
"""
from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .trace import Trace
from .types import Budget


@runtime_checkable
class MemoryStore(Protocol):
    def write(self, kind: str, content: str, meta: dict | None = None) -> int: ...

    def recall(self, query: str, k: int = 5, kind: str | None = None) -> list[dict]: ...

    def compact(self, max_tokens: int) -> int: ...


def _tokens(text: str) -> list[str]:
    """中文按双字切片，拉丁按词；足够做轻量 TF 召回。"""
    text = (text or "").lower()
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    words = re.findall(r"[a-z0-9_]{2,}", text)
    grams: list[str] = list(words)
    for run in cjk:
        if len(run) == 1:
            grams.append(run)
        for i in range(len(run) - 1):
            grams.append(run[i:i + 2])
    return grams


def _estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z0-9_]+", text or ""))
    return cjk + latin


class SQLiteMemory:
    """单表记忆库。写入即持久化，进程重启可复用。"""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS memory (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   kind TEXT NOT NULL,
                   content TEXT NOT NULL,
                   meta TEXT NOT NULL DEFAULT '{}',
                   tokens INTEGER NOT NULL DEFAULT 0,
                   ts REAL NOT NULL
               )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind)")
        self._conn.commit()

    def write(self, kind: str, content: str, meta: dict | None = None) -> int:
        import json

        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memory(kind, content, meta, tokens, ts) VALUES (?,?,?,?,?)",
                (kind, content, json.dumps(meta or {}, ensure_ascii=False),
                 _estimate_tokens(content), time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recall(self, query: str, k: int = 5, kind: str | None = None) -> list[dict]:
        q = set(_tokens(query))
        if not q:
            return []
        sql = "SELECT id, kind, content, meta, tokens, ts FROM memory"
        args: list = []
        if kind:
            sql += " WHERE kind = ?"
            args.append(kind)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()

        scored = []
        for rid, rkind, content, meta, tokens, ts in rows:
            terms = _tokens(content)
            if not terms:
                continue
            tf: dict[str, int] = {}
            for t in terms:
                tf[t] = tf.get(t, 0) + 1
            hit = sum(tf.get(t, 0) for t in q)
            if not hit:
                continue
            # TF / 长度归一：避免长文本仅凭篇幅占优
            score = hit / math.sqrt(max(1, len(terms)))
            scored.append((score, rid, rkind, content, meta, tokens, ts))

        scored.sort(key=lambda x: (-x[0], x[6]))
        out = []
        for score, rid, rkind, content, meta, tokens, ts in scored[:max(0, k)]:
            import json

            out.append({
                "id": rid, "kind": rkind, "content": content,
                "meta": json.loads(meta or "{}"), "tokens": tokens,
                "score": round(score, 4),
            })
        return out

    def compact(self, max_tokens: int) -> int:
        """按写入时间从旧到新累加，超过预算的旧记忆删除。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, tokens FROM memory ORDER BY ts DESC"
            ).fetchall()
            keep, total, drop = [], 0, []
            for rid, tokens in rows:
                if total + tokens <= max_tokens:
                    keep.append(rid)
                    total += tokens
                else:
                    drop.append(rid)
            if drop:
                self._conn.executemany("DELETE FROM memory WHERE id = ?",
                                       [(i,) for i in drop])
                self._conn.commit()
            return len(drop)


@dataclass
class RunContext:
    run_id: str
    trace: Trace
    budget: Budget
    state: dict = field(default_factory=dict)
    memory: MemoryStore | None = None
    scopes: tuple[str, ...] = ()

    def emit(self, event: str, **fields) -> dict:
        record = self.trace.event(event, **fields)
        observer = self.state.get("observer")
        if callable(observer):
            try:
                observer(event, dict(fields))
            except Exception:  # noqa: BLE001 - 观察者失败不应影响主流程
                pass
        return record

    def charge_tokens(self, tokens: int) -> None:
        self.state["tokens"] = int(self.state.get("tokens", 0)) + max(0, int(tokens))
