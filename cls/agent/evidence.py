"""证据校验：原文比对、来源存在性、候选集合归属。

只做定位与一致性判断，不参与规则评分。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

OK = "OK"
SPAN_MISMATCH = "SPAN_MISMATCH"
OFFSET_DRIFT = "OFFSET_DRIFT"
SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
NOT_IN_CANDIDATES = "NOT_IN_CANDIDATES"


@dataclass
class CheckResult:
    ok: bool
    code: str
    detail: str = ""
    span: dict | None = field(default=None)


def _compact_with_map(text: str) -> tuple[str, list[int]]:
    """去掉空白并保留「压缩后位置 → 原文位置」映射。"""
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        chars.append(ch)
        index.append(i)
    return "".join(chars), index


class EvidenceChecker:
    @staticmethod
    def check_span(doc_text: str, quote: str, offset: int | None = None,
                   tolerance: int = 2) -> CheckResult:
        quote = (quote or "").strip()
        doc_text = doc_text or ""
        if not quote:
            return CheckResult(False, SPAN_MISMATCH, "引用为空")
        if not doc_text:
            return CheckResult(False, SPAN_MISMATCH, "原文为空")

        pos = doc_text.find(quote)
        start, end = (pos, pos + len(quote)) if pos >= 0 else (-1, -1)

        if pos < 0:
            # 原文可能换行/多空格，压缩后重试
            compact_doc, mapping = _compact_with_map(doc_text)
            compact_quote, _ = _compact_with_map(quote)
            cpos = compact_doc.find(compact_quote)
            if cpos < 0:
                return CheckResult(False, SPAN_MISMATCH, "原文中找不到该引用")
            start = mapping[cpos]
            end = mapping[cpos + len(compact_quote) - 1] + 1

        span = {"start": start, "end": end}
        if offset is not None:
            try:
                drift = abs(int(offset) - start)
            except (TypeError, ValueError):
                drift = 0
            if drift > tolerance:
                return CheckResult(False, OFFSET_DRIFT,
                                   f"偏移漂移 {drift} 字，实际起点 {start}", span)
        return CheckResult(True, OK, "", span)

    @staticmethod
    def check_source(source_id: str, resolver: Callable[[str], object] | None) -> CheckResult:
        if not source_id:
            return CheckResult(False, SOURCE_NOT_FOUND, "来源 ID 为空")
        if resolver is None:
            return CheckResult(False, SOURCE_NOT_FOUND, "没有可用的来源解析器")
        try:
            found = resolver(source_id)
        except Exception:  # noqa: BLE001
            found = None
        if not found:
            return CheckResult(False, SOURCE_NOT_FOUND, f"来源不存在：{source_id}")
        return CheckResult(True, OK, "")

    @staticmethod
    def check_membership(item_id: str, candidates: Iterable[str]) -> CheckResult:
        pool = {str(x) for x in (candidates or [])}
        if item_id in pool:
            return CheckResult(True, OK, "")
        return CheckResult(False, NOT_IN_CANDIDATES,
                           f"{item_id} 不在候选集合（前 5 条检索结果）内")
