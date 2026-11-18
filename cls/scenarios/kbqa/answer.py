"""抽取式回答：只摘原文句子，拒绝时不生成任何自然语言结论。

拒答判定按可解释的顺序执行，并把「阈值」与「实际观测值」一起返回：
1. 没有检索结果            -> NO_HITS
2. 最高检索分低于阈值      -> LOW_SCORE
3. 没有句子的关键词覆盖达标 -> LOW_COVERAGE
4. 句子为空或无法定位      -> NO_CLAIM（正常情况下不会出现）

覆盖率按「问题关键词」计算，而不是问题里的全部二元词：
先剔除疑问词与停用词，再算 命中关键词数 / 关键词总数。
疑问词（哪些 / 多少 / 如何 / 是否……）在任何文档里都不会出现，
计入分母只会让同义改写的问题无条件掉分。
"""
from __future__ import annotations

import re
from typing import Callable

from .retrieval import tokenize

DEFAULT_MIN_SCORE = 0.12
DEFAULT_MIN_COVERAGE = 0.40
DEFAULT_MAX_CLAIMS = 2

NO_HITS = "NO_HITS"
LOW_SCORE = "LOW_SCORE"
LOW_COVERAGE = "LOW_COVERAGE"
NO_CLAIM = "NO_CLAIM"
CITATION_INVALID = "CITATION_INVALID"

_SENTENCE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")

# 疑问词：问句特有的词，正文里不会出现，不应进入覆盖率分母。
_QUESTION_WORDS = (
    "为什么", "什么时候", "什么样", "哪几种", "哪些", "哪个", "哪种", "哪里",
    "哪几", "什么", "怎么", "怎样", "如何", "为何", "是否", "能否", "可否",
    "是不是", "有没有", "能不能", "多少", "多大", "多久", "多长", "几个",
    "几", "谁", "何时",
)

# 停用词与口语化虚词：去掉后留下承载语义的关键词。
_STOP_WORDS = (
    "请问", "我们", "你们", "他们", "这个", "那个", "这些", "那些", "这套", "那套",
    "一种", "一个", "一下", "可以", "需要", "应该", "以及", "或者", "还是",
    "但是", "因为", "所以", "如果", "那么", "就是", "对于", "关于", "根据",
    "通过", "进行", "具有", "包含", "包括",
    "由", "的", "了", "吗", "呢", "啊", "是", "在", "和", "与", "或", "及",
    "等", "这", "那", "其", "之", "也", "都", "就", "被", "把", "对", "从",
    "到", "为", "以", "有", "会", "能", "要", "不", "没", "它", "他", "她",
    "该", "每", "各", "共", "约",
)

_NOISE = tuple(sorted(set(_QUESTION_WORDS + _STOP_WORDS), key=len, reverse=True))
_NOISE_RE = re.compile("|".join(re.escape(word) for word in _NOISE))
# 分词后残留的单字虚词与疑问二元词，作为第二道过滤。
_TOKEN_STOP = frozenset({
    "的", "了", "吗", "呢", "啊", "是", "在", "和", "与", "或", "及", "等",
    "这", "那", "其", "之", "也", "都", "就", "被", "把", "对", "从", "到",
    "为", "以", "有", "会", "能", "要", "不", "没", "它", "他", "她", "该",
    "个", "由", "哪些", "什么", "怎么", "如何", "是否", "多少", "哪个",
    "哪里", "为何", "能否", "可否", "多大", "多久", "多长", "谁", "何时",
})


def question_keywords(question: str) -> list[str]:
    """提取问题关键词：先剔除疑问词与停用词，再按统一口径分词并去重保序。"""
    stripped = _NOISE_RE.sub(" ", (question or "").lower())
    tokens = [t for t in tokenize(stripped) if t not in _TOKEN_STOP]
    if not tokens:
        tokens = [t for t in tokenize(question) if t not in _TOKEN_STOP]
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def split_sentences(text: str, base: int = 0) -> list[tuple[str, int, int]]:
    """把片段拆成句子，返回 (句子, 绝对起点, 绝对终点)。"""
    out: list[tuple[str, int, int]] = []
    for match in _SENTENCE.finditer(text):
        left, right = match.start(), match.end()
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            out.append((text[left:right], base + left, base + right))
    return out


def _refusal(reason: str, detail: str, thresholds: dict,
             top_score: float, best_coverage: float) -> dict:
    return {
        "refused": True,
        "reason_code": reason,
        "detail": detail,
        "thresholds": thresholds,
        "observed": {
            "top_score": round(float(top_score), 4),
            "best_coverage": round(float(best_coverage), 4),
        },
    }


def extract_answer(question: str, hits: list[dict],
                   text_of: Callable[[str], str],
                   min_score: float = DEFAULT_MIN_SCORE,
                   min_coverage: float = DEFAULT_MIN_COVERAGE,
                   max_claims: int = DEFAULT_MAX_CLAIMS) -> dict:
    thresholds = {
        "min_score": round(float(min_score), 4),
        "min_coverage": round(float(min_coverage), 4),
        "max_claims": int(max_claims),
    }
    top_score = float(hits[0].get("score", 0.0)) if hits else 0.0

    if not hits:
        return {
            "question": question, "answered": False, "answer": "",
            "claims": [],
            "refusal": _refusal(NO_HITS, "检索没有返回任何片段", thresholds,
                                top_score, 0.0),
            "retrieval": {"hit_count": 0, "top_score": 0.0},
        }
    if top_score < float(min_score):
        return {
            "question": question, "answered": False, "answer": "",
            "claims": [],
            "refusal": _refusal(
                LOW_SCORE,
                f"最高检索分 {top_score:.4f} 低于阈值 {float(min_score):.4f}",
                thresholds, top_score, 0.0),
            "retrieval": {"hit_count": len(hits), "top_score": round(top_score, 4)},
        }

    keywords = question_keywords(question)
    q_terms = set(keywords)
    if not q_terms:
        return {
            "question": question, "answered": False, "answer": "",
            "claims": [],
            "refusal": _refusal(NO_CLAIM, "问题没有可检索的词", thresholds,
                                top_score, 0.0),
            "retrieval": {"hit_count": len(hits), "top_score": round(top_score, 4)},
        }

    best_coverage = 0.0
    seen: set[str] = set()
    candidates: list[dict] = []
    for hit in hits:
        doc_text = text_of(hit.get("doc_id", "")) or ""
        span = hit.get("span") or {}
        start = int(span.get("start", 0))
        end = int(span.get("end", 0))
        if not doc_text or end <= start:
            continue
        for sent, sent_start, sent_end in split_sentences(doc_text[start:end], start):
            if len(sent) < 6 or sent in seen:
                continue
            coverage = len(q_terms & set(tokenize(sent))) / len(q_terms)
            best_coverage = max(best_coverage, coverage)
            if coverage < float(min_coverage):
                continue
            seen.add(sent)
            rank = 0.65 * coverage + 0.35 * float(hit.get("score", 0.0))
            candidates.append({
                "text": sent,
                "quote": sent,
                "doc_id": hit.get("doc_id", ""),
                "chunk_id": hit.get("chunk_id", ""),
                "span": {"start": sent_start, "end": sent_end},
                "coverage": round(coverage, 4),
                "retrieval_score": round(float(hit.get("score", 0.0)), 4),
                "rank": round(rank, 4),
            })

    if not candidates:
        return {
            "question": question, "answered": False, "answer": "",
            "claims": [],
            "refusal": _refusal(
                LOW_COVERAGE,
                f"最佳句子关键词覆盖 {best_coverage:.4f} 低于阈值 "
                f"{float(min_coverage):.4f}（关键词 {len(keywords)} 个）",
                thresholds, top_score, best_coverage),
            "retrieval": {"hit_count": len(hits), "top_score": round(top_score, 4),
                          "keywords": keywords},
        }

    candidates.sort(key=lambda c: (-c["rank"], c["span"]["start"]))
    claims = candidates[:max(1, int(max_claims))]
    for i, claim in enumerate(claims):
        claim["claim_id"] = f"c{i + 1}"

    return {
        "question": question,
        "answered": True,
        "answer": "\n".join(c["text"] for c in claims),
        "claims": claims,
        "refusal": None,
        "retrieval": {
            "hit_count": len(hits),
            "top_score": round(top_score, 4),
            "top_chunk_id": hits[0].get("chunk_id", ""),
            "best_coverage": round(best_coverage, 4),
            "keywords": keywords,
        },
    }
