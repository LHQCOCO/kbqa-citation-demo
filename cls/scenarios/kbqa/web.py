"""知识库问答演示页：每次请求都真实跑一遍入库 → 检索 → 作答 → 引用校验。

只做展示，不改场景与内核：
- 复用 Flask 应用与 app.css 的版式；
- 结论全部来自 AgentRuntime 的 artifacts，不读预置结果、不做示例数据兜底；
- 运行失败时把错误原样显示在页面上。
"""
from __future__ import annotations

import uuid

from flask import Blueprint, render_template, request

from ... import config
from ...agent.runtime import AgentRuntime
from ...agent.types import Budget
from .answer import (DEFAULT_MAX_CLAIMS, DEFAULT_MIN_COVERAGE, DEFAULT_MIN_SCORE,
                     LOW_COVERAGE, LOW_SCORE, NO_HITS)
from .chunking import chunk_text, read_document
from .documents import DOCS, collect_paths
from .manifest import KBQA_SCENARIO

kbqa_bp = Blueprint("kbqa", __name__)

DEFAULT_TOP_K = 8
DEFAULT_CHUNK_CHARS = 400
DEFAULT_OVERLAP = 60
MAX_QUESTION_CHARS = 300
CONTEXT_CHARS = 100  # 引用展开时额外展示的上下文字符数

REASON_TEXT = {
    NO_HITS: "检索没有任何命中片段",
    LOW_SCORE: "最高检索分低于 min_score",
    LOW_COVERAGE: "最佳句子的关键词覆盖率低于 min_coverage",
}

EXAMPLE_QUESTIONS = [
    {"question": "默认预算包含多少个步骤和多少次工具调用？",
     "note": "成功案例：答案整句来自 agent_runtime.md"},
    {"question": "默认拒答阈值的 min_score 和 min_coverage 是多少？",
     "note": "成功案例：答案整句来自 kbqa_design.md"},
    {"question": "第一版支持哪些文档格式？",
     "note": "成功案例：问法换了词，关键词覆盖 0.57 仍达标"},
    {"question": "Agent 内核由哪些模块构成",
     "note": "失败案例：原文用「运行时 / 组件」，关键词覆盖 0.20 低于阈值 → LOW_COVERAGE"},
    {"question": "知识库是否支持多租户隔离？",
     "note": "失败案例：域外问题，关键词只对上四分之一 → LOW_COVERAGE"},
    {"question": "知识库问答和工单系统这两个场景，默认预算哪个更宽松？",
     "note": "跨文档问题：词能命中单篇，但没有一篇答得了 → LOW_COVERAGE"},
    {"question": "今天天气怎么样？",
     "note": "拒答路径：完全没有命中 → NO_HITS"},
]

BOUNDARIES = {
    "top_k": DEFAULT_TOP_K,
    "min_score": DEFAULT_MIN_SCORE,
    "min_coverage": DEFAULT_MIN_COVERAGE,
    "max_claims": DEFAULT_MAX_CLAIMS,
    "chunk_chars": DEFAULT_CHUNK_CHARS,
    "overlap": DEFAULT_OVERLAP,
}


def corpus_stats() -> dict:
    """统计页面实际使用的语料规模（samples/kb），不参与检索。"""
    paths = collect_paths([config.SAMPLES_DIR / "kb"])
    chunks = 0
    for path in paths:
        _name, text = read_document(path)
        chunks += len(chunk_text("kb-stat", text))
    return {"docs": len(paths), "chunks": chunks, "dir": "samples/kb"}


def _document_name(doc_id: str, name_of: dict) -> str:
    if doc_id in name_of:
        return name_of[doc_id]
    doc = DOCS.get(doc_id)
    return doc.name if doc else doc_id


def _hit_views(hits: list[dict], name_of: dict) -> list[dict]:
    views = []
    for rank, hit in enumerate(hits, 1):
        span = hit.get("span") or {}
        views.append({
            "rank": rank,
            "doc_name": _document_name(hit.get("doc_id", ""), name_of),
            "doc_id": hit.get("doc_id", ""),
            "chunk_id": hit.get("chunk_id", ""),
            "heading": hit.get("heading", ""),
            "start": span.get("start"),
            "end": span.get("end"),
            "score": hit.get("score"),
            "keyword_score": hit.get("keyword_score"),
            "vector_score": hit.get("vector_score"),
            "coverage": hit.get("coverage"),
        })
    return views


def _claim_views(claims: list[dict], name_of: dict) -> list[dict]:
    views = []
    for claim in claims:
        doc = DOCS.get(claim.get("doc_id", ""))
        text = doc.text if doc else ""
        span = claim.get("span") or {}
        start = int(span.get("start", 0))
        end = int(span.get("end", 0))
        views.append({
            "claim_id": claim.get("claim_id", ""),
            "doc_name": _document_name(claim.get("doc_id", ""), name_of),
            "doc_id": claim.get("doc_id", ""),
            "start": start,
            "end": end,
            "quote": claim.get("quote", ""),
            "before": text[max(0, start - CONTEXT_CHARS):start],
            "after": text[end:min(len(text), end + CONTEXT_CHARS)],
            "coverage": claim.get("coverage"),
            "retrieval_score": claim.get("retrieval_score"),
        })
    return views


def run_question(question: str, top_k: int = DEFAULT_TOP_K) -> dict:
    """真实跑一次 KBQA 场景，把 artifacts 整理成页面视图。"""
    docs_dir = config.SAMPLES_DIR / "kb"
    run_id = "kbqa-page-" + uuid.uuid4().hex[:8]
    runtime = AgentRuntime(KBQA_SCENARIO, data_dir=config.DATA_DIR / "agent_traces")
    run = runtime.run(
        {
            "paths": [str(docs_dir)],
            "question": question,
            "top_k": top_k,
            "chunk_chars": DEFAULT_CHUNK_CHARS,
            "overlap": DEFAULT_OVERLAP,
            "min_score": DEFAULT_MIN_SCORE,
            "min_coverage": DEFAULT_MIN_COVERAGE,
            "max_claims": DEFAULT_MAX_CLAIMS,
            "write": False,
        },
        budget=Budget(max_steps=10, max_tool_calls=24),
        run_id=run_id,
    )

    artifacts = run.artifacts or {}
    ingest = artifacts.get("s1") or {}
    search = artifacts.get("s2") or {}
    answer = artifacts.get("s3") or {}
    if not answer:
        raise RuntimeError(
            f"运行未产出答案（status={run.status}, stop_reason={run.stop_reason}）"
        )

    name_of = {d.get("doc_id", ""): d.get("name", "")
               for d in ingest.get("docs") or []}
    refusal = answer.get("refusal") or None
    retrieval = answer.get("retrieval") or {}
    observed = (refusal or {}).get("observed") or {}
    best_coverage = retrieval.get("best_coverage")
    if best_coverage is None:
        best_coverage = observed.get("best_coverage")

    return {
        "run_id": run.run_id,
        "status": run.status,
        "stop_reason": run.stop_reason,
        "metrics": run.metrics,
        "question": answer.get("question", question),
        "answered": bool(answer.get("answered")),
        "answer": answer.get("answer", ""),
        "claims": _claim_views(answer.get("claims") or [], name_of),
        "refusal": refusal,
        "reason_text": REASON_TEXT.get((refusal or {}).get("reason_code", ""), ""),
        "retrieval": retrieval,
        "best_coverage": best_coverage,
        "hits": _hit_views(search.get("hits") or [], name_of),
        "corpus": ingest.get("stats") or {},
        "verification": answer.get("verification") or {},
    }


def _render(result=None, error="", question="", status=200):
    return render_template(
        "kbqa.html",
        result=result, error=error, question=question,
        examples=EXAMPLE_QUESTIONS, boundaries=BOUNDARIES,
        corpus=corpus_stats(), max_question_chars=MAX_QUESTION_CHARS,
    ), status


@kbqa_bp.get("/kbqa")
def kbqa_page():
    return _render()


@kbqa_bp.post("/kbqa/ask")
def kbqa_ask():
    question = (request.form.get("question") or "").strip()
    if not question:
        return _render(error="请输入要问的问题", status=400)
    if len(question) > MAX_QUESTION_CHARS:
        return _render(
            question=question,
            error=f"问题超过 {MAX_QUESTION_CHARS} 字上限",
            status=400,
        )
    try:
        result = run_question(question)
    except Exception as exc:  # noqa: BLE001 - 页面必须把真实错误暴露出来
        return _render(
            question=question,
            error=f"运行失败：{exc.__class__.__name__}: {exc}",
            status=500,
        )
    return _render(result=result, question=question)
