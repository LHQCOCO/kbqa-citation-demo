"""进程内知识库：文档正文 + 切片。只读本地文件，不落盘、不联网。"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .chunking import Chunk, chunk_text, read_document


def _sha8(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


@dataclass
class KBDocument:
    doc_id: str
    name: str
    path: str
    text: str
    chunks: list[Chunk] = field(default_factory=list)

    def chunk(self, chunk_id: str) -> Chunk | None:
        for item in self.chunks:
            if item.chunk_id == chunk_id:
                return item
        return None


class KBStore:
    def __init__(self) -> None:
        self._items: dict[str, KBDocument] = {}
        self._lock = threading.RLock()

    def add(self, name: str, path: str, text: str, chunk_chars: int = 400,
            overlap: int = 60) -> KBDocument:
        doc_id = "kb-" + _sha8(text)
        chunks = chunk_text(doc_id, text, chunk_chars=chunk_chars, overlap=overlap)
        record = KBDocument(doc_id=doc_id, name=name, path=path, text=text, chunks=chunks)
        with self._lock:
            self._items[doc_id] = record
        return record

    def get(self, doc_id: str) -> KBDocument | None:
        with self._lock:
            return self._items.get(doc_id or "")

    def all(self) -> list[KBDocument]:
        with self._lock:
            return [self._items[k] for k in sorted(self._items)]

    def chunks(self, doc_ids: list[str] | None = None) -> list[Chunk]:
        allowed = set(doc_ids or [])
        with self._lock:
            docs = [self._items[k] for k in sorted(self._items)]
        out: list[Chunk] = []
        for doc in docs:
            if allowed and doc.doc_id not in allowed:
                continue
            out.extend(doc.chunks)
        return out

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


DOCS = KBStore()


def collect_paths(raw_paths) -> list[Path]:
    """把文件/目录参数展开成文件列表；目录只取下一层 md/txt。"""
    if isinstance(raw_paths, (str, Path)):
        raw_paths = [raw_paths]
    out: list[Path] = []
    for raw in raw_paths or []:
        p = Path(str(raw)).expanduser()
        if p.is_dir():
            out.extend(sorted(p.glob("*.md")))
            out.extend(sorted(p.glob("*.txt")))
        else:
            out.append(p)
    return out


def ingest_path(path: str, chunk_chars: int = 400, overlap: int = 60) -> KBDocument:
    name, text = read_document(path)
    return DOCS.add(name=name, path=str(path), text=text,
                    chunk_chars=chunk_chars, overlap=overlap)
