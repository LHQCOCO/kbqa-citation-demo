"""知识库问答场景的 5 个工具（K1–K5）。

全部本地计算：入库只读本地 md/txt，检索用自实现 BM25 + 字符 n-gram 向量，
答案只摘原文句子；每条引用在返回前都用 EvidenceChecker 复核一次。
"""
from __future__ import annotations

from ... import config
from ...agent.evidence import EvidenceChecker
from ...agent.types import INVALID_ARGS, SOURCE_NOT_FOUND, ToolResult, ToolSpec
from .answer import (DEFAULT_MAX_CLAIMS, DEFAULT_MIN_COVERAGE, DEFAULT_MIN_SCORE,
                     extract_answer)
from .documents import DOCS, collect_paths, ingest_path
from .retrieval import HybridIndex


# ------------------------------------------------------------------ K1
def ingest_documents(args: dict, ctx) -> ToolResult:
    paths = collect_paths(args.get("paths"))
    if not paths:
        return ToolResult(False, error_code=INVALID_ARGS,
                          message="没有可入库的文件")
    chunk_chars = int(args.get("chunk_chars", 400))
    overlap = int(args.get("overlap", 60))

    docs: list[dict] = []
    try:
        for path in paths:
            doc = ingest_path(str(path), chunk_chars=chunk_chars, overlap=overlap)
            docs.append({
                "doc_id": doc.doc_id,
                "name": doc.name,
                "chars": len(doc.text),
                "chunks": len(doc.chunks),
            })
    except FileNotFoundError as exc:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message=str(exc))
    except ValueError as exc:
        return ToolResult(False, error_code=INVALID_ARGS, message=str(exc))

    if not docs:
        return ToolResult(False, error_code=INVALID_ARGS,
                          message="目录下没有 md/txt 文件")
    return ToolResult(True, data={
        "doc_ids": [d["doc_id"] for d in docs],
        "docs": docs,
        "stats": {
            "docs": len(docs),
            "chunks": sum(d["chunks"] for d in docs),
            "chars": sum(d["chars"] for d in docs),
        },
        "chunk_params": {"chunk_chars": chunk_chars, "overlap": overlap},
    })


# ------------------------------------------------------------------ K2
def search_kb(args: dict, ctx) -> ToolResult:
    query = (args.get("query") or "").strip()
    if not query:
        return ToolResult(False, error_code=INVALID_ARGS, message="查询不能为空")
    doc_ids = [str(x) for x in (args.get("doc_ids") or [])]
    chunks = DOCS.chunks(doc_ids or None)
    if not chunks:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND,
                          message="知识库为空，请先调用 ingest_documents")
    top_k = int(args.get("top_k", 8))
    index = HybridIndex(chunks)
    hits = index.search(query, top_k=top_k, doc_ids=doc_ids or None)
    return ToolResult(True, data={
        "query": query,
        "hits": hits,
        "stats": {"docs": len({c.doc_id for c in chunks}),
                  "chunks": len(chunks), "top_k": top_k},
    })


# ------------------------------------------------------------------ K3
def answer_kb(args: dict, ctx) -> ToolResult:
    question = (args.get("question") or "").strip()
    if not question:
        return ToolResult(False, error_code=INVALID_ARGS, message="问题不能为空")
    hits = args.get("hits")
    if not isinstance(hits, list):
        return ToolResult(False, error_code=INVALID_ARGS,
                          message="hits 必须是检索结果数组")
    data = extract_answer(
        question, hits,
        text_of=lambda doc_id: (DOCS.get(doc_id).text if DOCS.get(doc_id) else ""),
        min_score=float(args.get("min_score", DEFAULT_MIN_SCORE)),
        min_coverage=float(args.get("min_coverage", DEFAULT_MIN_COVERAGE)),
        max_claims=int(args.get("max_claims", DEFAULT_MAX_CLAIMS)),
    )
    return ToolResult(True, data=data)


# ------------------------------------------------------------------ K4
def verify_citations(args: dict, ctx) -> ToolResult:
    claims = args.get("claims")
    if not isinstance(claims, list):
        return ToolResult(False, error_code=INVALID_ARGS,
                          message="claims 必须是数组")
    candidates = {str(x) for x in (args.get("candidate_chunk_ids") or [])}
    verified: list[dict] = []
    rejected: list[dict] = []

    for claim in claims:
        if not isinstance(claim, dict):
            rejected.append({"claim_id": "", "reason_code": "SCHEMA_INVALID",
                             "detail": "claim 不是对象"})
            continue
        claim_id = str(claim.get("claim_id", ""))
        doc_id = str(claim.get("doc_id", ""))
        chunk_id = str(claim.get("chunk_id", ""))
        quote = str(claim.get("quote") or claim.get("text") or "")

        source = EvidenceChecker.check_source(doc_id, lambda i: DOCS.get(i))
        if not source.ok:
            rejected.append({"claim_id": claim_id, "reason_code": source.code,
                             "detail": source.detail})
            continue
        if candidates:
            member = EvidenceChecker.check_membership(chunk_id, candidates)
            if not member.ok:
                rejected.append({"claim_id": claim_id, "reason_code": member.code,
                                 "detail": member.detail})
                continue

        doc = DOCS.get(doc_id)
        offset = (claim.get("span") or {}).get("start")
        check = EvidenceChecker.check_span(doc.text, quote, offset)
        if not check.ok:
            rejected.append({"claim_id": claim_id, "reason_code": check.code,
                             "detail": check.detail})
            continue
        verified.append({**claim, "quote": quote, "span": check.span})

    return ToolResult(
        ok=not rejected,
        data={"verified": verified, "rejected": rejected,
              "counts": {"verified": len(verified), "rejected": len(rejected)}},
        error_code=None if not rejected else "CITATION_INVALID",
        message="" if not rejected else f"{len(rejected)} 条引用未通过校验",
    )


# ------------------------------------------------------------------ K5
def write_answer(args: dict, ctx) -> ToolResult:
    results = ctx.state.get("results", {})
    answer = results.get("s3") or {}
    ingest = results.get("s1") or {}
    question = answer.get("question", "")
    claims = answer.get("claims") or []
    refusal = answer.get("refusal") or {}

    lines = [
        "# 知识库问答结果",
        "",
        f"- 问题：{question}",
        f"- 结论：{'已回答' if answer.get('answered') else '已拒答'}",
        f"- 入库文档：{len(ingest.get('doc_ids') or [])} 篇 / "
        f"{(ingest.get('stats') or {}).get('chunks', 0)} 个片段",
        f"- 命中片段：{(answer.get('retrieval') or {}).get('hit_count', 0)} 个",
    ]
    if refusal:
        thresholds = refusal.get("thresholds") or {}
        observed = refusal.get("observed") or {}
        lines += [
            f"- 拒答原因：{refusal.get('reason_code', '')}（{refusal.get('detail', '')}）",
            f"- 阈值：min_score={thresholds.get('min_score')} "
            f"min_coverage={thresholds.get('min_coverage')}",
            f"- 实测：top_score={observed.get('top_score')} "
            f"best_coverage={observed.get('best_coverage')}",
        ]

    lines += ["", "## 答案", ""]
    lines.append(answer.get("answer") or "（无依据，拒绝作答）")
    lines += ["", "## 引用", ""]
    if not claims:
        lines.append("（无引用）")
    for i, claim in enumerate(claims, 1):
        doc = DOCS.get(claim.get("doc_id", ""))
        span = claim.get("span") or {}
        lines.append(f"{i}. [{doc.name if doc else claim.get('doc_id', '')}] "
                     f"字符 {span.get('start')}–{span.get('end')}：{claim.get('quote', '')}")

    body = "\n".join(lines) + "\n"
    config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = (args.get("run_id") or "").strip() or ctx.run_id
    path = config.EXPORTS_DIR / f"{run_id}-answer.md"
    path.write_text(body, encoding="utf-8")
    return ToolResult(True, data={"path": str(path), "bytes": len(body.encode("utf-8")),
                                  "citations": len(claims)})


# ------------------------------------------------------------------ specs
TOOL_SPECS: list[tuple[ToolSpec, callable]] = [
    (ToolSpec(
        name="ingest_documents",
        description="把本地 md/txt 文件或目录切片入库，保留原文绝对字符偏移；检索前必须调用",
        params={"type": "object", "required": ["paths"],
                "properties": {
                    "paths": {"type": "array", "items": {"type": "string"}},
                    "chunk_chars": {"type": "integer"},
                    "overlap": {"type": "integer"},
                },
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="none",
        timeout_s=30.0, max_calls_per_run=3,
    ), ingest_documents),
    (ToolSpec(
        name="search_kb",
        description="混合检索（BM25 + 字符 n-gram 向量），返回命中原文片段、偏移与分通道得分",
        params={"type": "object", "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                    "doc_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="none", timeout_s=10.0,
    ), search_kb),
    (ToolSpec(
        name="answer_kb",
        description="从检索片段中抽取原文句子作答；无依据时按阈值拒答，并返回原因与实测值",
        params={"type": "object", "required": ["question", "hits"],
                "properties": {
                    "question": {"type": "string"},
                    "hits": {"type": "array", "items": {"type": "object"}},
                    "min_score": {"type": "number"},
                    "min_coverage": {"type": "number"},
                    "max_claims": {"type": "integer"},
                },
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="none", timeout_s=10.0,
    ), answer_kb),
    (ToolSpec(
        name="verify_citations",
        description="用 EvidenceChecker 复核每条结论的来源、候选归属、引用原文与偏移",
        params={"type": "object", "required": ["claims"],
                "properties": {
                    "claims": {"type": "array", "items": {"type": "object"}},
                    "candidate_chunk_ids": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="none", timeout_s=10.0,
    ), verify_citations),
    (ToolSpec(
        name="write_answer",
        description="把问答结论、拒答原因与逐条引用写成 Markdown 文件",
        params={"type": "object", "required": ["run_id"],
                "properties": {"run_id": {"type": "string"},
                               "fmt": {"type": "string", "enum": ["md"]}},
                "additionalProperties": False},
        returns={"type": "object"}, side_effect="local_write",
        timeout_s=30.0, max_calls_per_run=1,
    ), write_answer),
]
