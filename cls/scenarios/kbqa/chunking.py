"""本地文档切片：只支持 md/txt，保留原文绝对字符偏移。

设计约束：
- 不引入分词/向量模型，纯标准库；
- 每个 chunk 都满足 ``doc.text[start:end] == chunk.text``，引用可直接回溯；
- 先按标题分段，再按字符预算滑动切分，优先在段落/句末断开。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = (".md", ".txt")

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_BOUNDARIES = ("\n\n", "。", "！", "？", "；", "\n")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    index: int
    text: str
    start: int
    end: int
    heading: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "index": self.index,
            "heading": self.heading,
            "span": {"start": self.start, "end": self.end},
            "chars": len(self.text),
        }


def read_document(path: str | Path) -> tuple[str, str]:
    """读取 md/txt，返回 (显示名, 规范化后的正文)。

    统一换行到 ``\\n``；偏移以返回的正文为基准，因此不影响可回溯性。
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"文件不存在：{p}")
    if p.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"仅支持 md/txt，收到：{p.suffix or '(无扩展名)'}")
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    return p.name, text.replace("\r\n", "\n").replace("\r", "\n")


def _sections(text: str) -> list[tuple[int, int, str]]:
    """按 Markdown 标题切段；无标题时整篇为一段。"""
    matches = list(_HEADING.finditer(text))
    if not matches:
        return [(0, len(text), "")]
    out: list[tuple[int, int, str]] = []
    first = matches[0].start()
    if first > 0 and text[:first].strip():
        out.append((0, first, ""))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((match.start(), end, match.group(2).strip()))
    return out


def _cut_point(text: str, lower: int, hard_end: int) -> int:
    """在 [lower, hard_end] 里找最靠后的自然断点，找不到就用硬边界。"""
    window = text[lower:hard_end]
    best = -1
    for delim in _BOUNDARIES:
        pos = window.rfind(delim)
        if pos >= 0:
            best = max(best, lower + pos + len(delim))
    return best if best > lower else hard_end


def chunk_text(doc_id: str, text: str, chunk_chars: int = 400,
               overlap: int = 60) -> list[Chunk]:
    """把正文切成带绝对偏移的 chunk；参数非法时抛 ValueError。"""
    chunk_chars = int(chunk_chars)
    overlap = int(overlap)
    if chunk_chars < 80:
        raise ValueError("chunk_chars 不能小于 80")
    if overlap < 0 or overlap >= chunk_chars:
        raise ValueError("overlap 必须 >= 0 且小于 chunk_chars")

    chunks: list[Chunk] = []
    for sec_start, sec_end, heading in _sections(text):
        start, end = sec_start, sec_end
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start >= end:
            continue

        pos = start
        while pos < end:
            hard_end = min(pos + chunk_chars, end)
            if hard_end >= end:
                cut = end
            else:
                lower = pos + max(chunk_chars // 2, overlap + 1)
                cut = _cut_point(text, min(lower, hard_end), hard_end)

            left, right = pos, cut
            while left < right and text[left].isspace():
                left += 1
            while right > left and text[right - 1].isspace():
                right -= 1
            if right > left:
                chunks.append(Chunk(
                    chunk_id=f"{doc_id}#c{len(chunks):03d}",
                    doc_id=doc_id,
                    index=len(chunks),
                    text=text[left:right],
                    start=left,
                    end=right,
                    heading=heading,
                ))

            if cut >= end:
                break
            pos = max(cut - overlap, pos + 1)
    return chunks
