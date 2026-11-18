"""可复现的小样本评测。

指标口径（全部可用下面的函数复算）：
- 检索命中率 = top-k 片段中，存在一个片段的字符区间覆盖答案原文起点 / 问题总数
- 引用正确率 = 通过「引用区间切片等于引用原文」校验的结论数 / 结论总数
- 答案命中率 = 已作答且至少一条结论包含答案原文 / 问题总数
- 不可回答问题拒答率 = 拒答数 / 不可回答问题数

问答集与语料都在仓库内，索引构造无随机数；同一份代码重复运行结果一致。
这只是自建小样本，不能外推到真实业务分布。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ...agent.runtime import AgentRuntime
from ...agent.types import Budget
from .answer import DEFAULT_MAX_CLAIMS, DEFAULT_MIN_COVERAGE, DEFAULT_MIN_SCORE
from .documents import DOCS, collect_paths
from .manifest import KBQA_SCENARIO


def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _covers_position(hit: dict, doc_id: str, position: int) -> bool:
    span = hit.get("span") or {}
    return (hit.get("doc_id") == doc_id
            and int(span.get("start", -1)) <= position < int(span.get("end", -1)))


def _run(runtime: AgentRuntime, docs: list[str], item: dict, trace_dir: Path,
         top_k: int, **thresholds) -> tuple[dict, dict, dict]:
    run_id = f"eval-{item.get('id', 'q')}"
    inputs = {
        "paths": docs,
        "question": item.get("question", ""),
        "top_k": top_k,
        "write": False,
        **thresholds,
    }
    result = runtime.run(inputs, budget=Budget(max_steps=10, max_tool_calls=24),
                         run_id=run_id, trace_path=trace_dir / f"{run_id}.jsonl")
    return (result.artifacts.get("s1") or {},
            result.artifacts.get("s2") or {},
            result.artifacts.get("s3") or {})


def evaluate(docs, qa_path: str | Path, unanswerable_path: str | Path | None = None,
             top_k: int = 8, min_score: float = DEFAULT_MIN_SCORE,
             min_coverage: float = DEFAULT_MIN_COVERAGE,
             max_claims: int = DEFAULT_MAX_CLAIMS) -> dict:
    doc_paths = [str(p) for p in collect_paths(docs)]
    questions = load_jsonl(qa_path)
    if not questions:
        raise ValueError("问答集为空")

    thresholds = {"min_score": min_score, "min_coverage": min_coverage,
                  "max_claims": max_claims}
    rows: list[dict] = []
    hits = answered = answer_ok = claims_total = claims_valid = 0

    with tempfile.TemporaryDirectory(prefix="kbqa-eval-") as tmp:
        trace_dir = Path(tmp)
        runtime = AgentRuntime(KBQA_SCENARIO, data_dir=trace_dir)

        for item in questions:
            ingest, search, answer = _run(runtime, doc_paths, item, trace_dir, top_k,
                                          **thresholds)
            name_to_id = {d["name"]: d["doc_id"] for d in ingest.get("docs", [])}
            doc_id = name_to_id.get(item.get("doc", ""), "")
            doc = DOCS.get(doc_id)
            if doc is None:
                raise ValueError(f"{item.get('id')}: 期望文档 {item.get('doc')} 未入库")

            answer_text = item.get("answer_text", "")
            pos = doc.text.find(answer_text)
            if pos < 0:
                raise ValueError(f"{item.get('id')}: 答案原文不在 {doc.name} 中")

            candidates = search.get("hits") or []
            retrieval_hit = any(
                _covers_position(hit, doc_id, pos) for hit in candidates
            )

            claims = answer.get("claims") or []
            valid_claims = 0
            for claim in claims:
                claim_doc = DOCS.get(claim.get("doc_id", ""))
                span = claim.get("span") or {}
                if (claim_doc is not None
                        and claim_doc.text[int(span.get("start", 0)):int(span.get("end", 0))]
                        == claim.get("quote", "")):
                    valid_claims += 1
            correct = bool(answer.get("answered")) and any(
                answer_text in (c.get("quote") or "") for c in claims)

            hits += int(retrieval_hit)
            answered += int(bool(answer.get("answered")))
            answer_ok += int(correct)
            claims_total += len(claims)
            claims_valid += valid_claims
            rows.append({
                "id": item.get("id", ""),
                "question": item.get("question", ""),
                "doc": doc.name,
                "retrieval_hit": retrieval_hit,
                "answered": bool(answer.get("answered")),
                "answer_correct": correct,
                "claims": len(claims),
                "valid_claims": valid_claims,
                "top_score": (answer.get("retrieval") or {}).get("top_score", 0.0),
                "best_coverage": (
                    (answer.get("retrieval") or {}).get("best_coverage")
                    or ((answer.get("refusal") or {}).get("observed") or {}).get(
                        "best_coverage", 0.0)
                ),
                "refusal": (answer.get("refusal") or {}).get("reason_code", ""),
            })

        refusal = {"total": 0, "refused": 0, "reasons": {}, "rows": []}
        if unanswerable_path:
            for item in load_jsonl(unanswerable_path):
                _, _, answer = _run(runtime, doc_paths, item, trace_dir, top_k,
                                    **thresholds)
                code = (answer.get("refusal") or {}).get("reason_code", "")
                is_refused = not bool(answer.get("answered"))
                observed = (answer.get("refusal") or {}).get("observed") or {}
                retrieval = answer.get("retrieval") or {}
                refusal["total"] += 1
                refusal["refused"] += int(is_refused)
                if is_refused:
                    refusal["reasons"][code] = refusal["reasons"].get(code, 0) + 1
                refusal["rows"].append({
                    "id": item.get("id", ""),
                    "question": item.get("question", ""),
                    "category": item.get("category", ""),
                    "refused": is_refused,
                    "refusal": code,
                    "top_score": retrieval.get("top_score", 0.0),
                    "best_coverage": observed.get(
                        "best_coverage", retrieval.get("best_coverage", 0.0)),
                })

    total = len(questions)
    return {
        "questions": total,
        "retrieval_hits": hits,
        "retrieval_hit_rate": _ratio(hits, total),
        "answered": answered,
        "answer_correct": answer_ok,
        "answer_rate": _ratio(answer_ok, total),
        "claims": claims_total,
        "valid_claims": claims_valid,
        "citation_accuracy": _ratio(claims_valid, claims_total),
        "unanswerable": {
            **refusal,
            "refusal_rate": _ratio(refusal["refused"], refusal["total"]),
        },
        "top_k": top_k,
        "thresholds": thresholds,
        "corpus": {"docs": len(doc_paths)},
        "rows": rows,
    }


def format_report(stats: dict) -> str:
    thresholds = stats.get("thresholds") or {}
    lines = [
        f"问题数：{stats['questions']}，top_k={stats['top_k']}，"
        f"min_score={thresholds.get('min_score')}，"
        f"min_coverage={thresholds.get('min_coverage')}",
        f"检索命中：{stats['retrieval_hits']}/{stats['questions']}"
        f"（{stats['retrieval_hit_rate'] * 100:.1f}%）",
        f"已回答：{stats['answered']}/{stats['questions']}，"
        f"答案命中：{stats['answer_correct']}/{stats['questions']}"
        f"（{stats['answer_rate'] * 100:.1f}%）",
        f"引用正确：{stats['valid_claims']}/{stats['claims']}"
        f"（{stats['citation_accuracy'] * 100:.1f}%）",
    ]
    unanswerable = stats.get("unanswerable") or {}
    if unanswerable.get("total"):
        lines.append(
            f"不可回答问题拒答：{unanswerable['refused']}/{unanswerable['total']}"
            f"（{unanswerable['refusal_rate'] * 100:.1f}%），"
            f"原因分布：{unanswerable.get('reasons', {})}"
        )
    lines.append("注：问答集与语料均为仓库内自建小样本，数字只用于回归，不能外推。")
    return "\n".join(lines)
