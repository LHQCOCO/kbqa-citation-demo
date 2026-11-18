"""离线混合检索：BM25（关键词）+ 字符 n-gram 哈希向量（向量通道）。

本机不能下载 embedding 权重，因此向量通道不承诺语义泛化能力：
它把字符 2/3-gram 的 TF-IDF 用带符号哈希压到固定维度，再做余弦相似度，
作用是把「字面相近但词边界不同」的片段也捞出来。代价是无法处理同义改写，
这一点在场景说明与回报里标注为已知限制。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from .chunking import Chunk

DIM = 512
DEFAULT_KEYWORD_WEIGHT = 0.5
DEFAULT_VECTOR_WEIGHT = 0.5

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-z0-9][a-z0-9_+#.\-]*")


def tokenize(text: str) -> list[str]:
    """中文按双字切片，拉丁按词；与合同场景的轻量口径一致，不依赖 jieba。"""
    text = (text or "").lower()
    out: list[str] = []
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    out.extend(_LATIN.findall(text))
    return out


def _char_ngrams(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", (text or "").lower())
    grams: list[str] = []
    for n in (2, 3):
        grams.extend(compact[i:i + n] for i in range(len(compact) - n + 1))
    return grams


def _bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % DIM, (1.0 if value & (1 << 63) else -1.0)


def _vector(tokens: list[str], idf: dict[str, float]) -> list[float]:
    vec = [0.0] * DIM
    for token, count in Counter(tokens).items():
        idx, sign = _bucket(token)
        vec[idx] += sign * (1.0 + math.log(count)) * idf.get(token, 1.0)
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def _cosine(a: list[float], b: list[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


class HybridIndex:
    """在给定 chunk 集合上建一次索引；检索结果确定、可复现。"""

    def __init__(self, chunks: list[Chunk], keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
                 vector_weight: float = DEFAULT_VECTOR_WEIGHT, k1: float = 1.2,
                 b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self.keyword_weight = float(keyword_weight)
        self.vector_weight = float(vector_weight)
        self.k1 = float(k1)
        self.b = float(b)

        self.tokens = [tokenize(c.text) for c in self.chunks]
        self.ngrams = [_char_ngrams(c.text) for c in self.chunks]
        self.lengths = [len(t) for t in self.tokens]
        self.avgdl = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

        self.tf: list[Counter] = [Counter(t) for t in self.tokens]
        self.df: Counter = Counter()
        for tokens in self.tokens:
            self.df.update(set(tokens))
        n = len(self.chunks)
        self.idf = {
            term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in self.df.items()
        }

        ng_df: Counter = Counter()
        for grams in self.ngrams:
            ng_df.update(set(grams))
        self.ng_idf = {
            term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in ng_df.items()
        }
        self.vectors = [_vector(grams, self.ng_idf) for grams in self.ngrams]

    def _bm25(self, q_terms: set[str], i: int) -> float:
        tf = self.tf[i]
        dl = self.lengths[i] or 1
        denom_base = self.k1 * (1.0 - self.b + self.b * dl / (self.avgdl or 1.0))
        total = 0.0
        for term in q_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            total += (self.idf.get(term, 0.0) * freq * (self.k1 + 1.0)
                      / (freq + denom_base))
        return total

    def search(self, query: str, top_k: int = 8,
               doc_ids: list[str] | None = None) -> list[dict]:
        q_terms = set(tokenize(query))
        if not q_terms:
            return []
        q_ngrams = _char_ngrams(query)
        q_vec = _vector(q_ngrams, self.ng_idf)
        allowed = set(doc_ids or [])
        q_count = len(q_terms)

        scored: list[tuple[float, dict]] = []
        for i, chunk in enumerate(self.chunks):
            if allowed and chunk.doc_id not in allowed:
                continue
            raw = self._bm25(q_terms, i)
            if raw <= 0:
                continue
            keyword = raw / (raw + 3.0)
            vector = max(0.0, _cosine(q_vec, self.vectors[i]))
            score = (self.keyword_weight * keyword
                     + self.vector_weight * vector)
            coverage = len(q_terms & set(self.tokens[i])) / q_count
            scored.append((score, {
                "doc_id": chunk.doc_id,
                "chunk_id": chunk.chunk_id,
                "index": chunk.index,
                "heading": chunk.heading,
                "span": {"start": chunk.start, "end": chunk.end},
                "quote": chunk.text,
                "score": round(score, 4),
                "keyword_score": round(keyword, 4),
                "vector_score": round(vector, 4),
                "coverage": round(coverage, 4),
            }))

        scored.sort(key=lambda x: (-x[0], x[1]["doc_id"], x[1]["chunk_id"]))
        return [item for _, item in scored[:max(1, int(top_k))]]
