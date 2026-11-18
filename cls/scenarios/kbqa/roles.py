"""知识库问答的回答角色：先抽取，再用 EvidenceChecker 复核每条引用。

角色只做编排，判定逻辑在工具里；没有模型参与，输出完全确定。
"""
from __future__ import annotations

import time

from ...agent.roles import Role, call_tool_for_role
from ...agent.types import ROLE_ERROR
from .answer import (CITATION_INVALID, DEFAULT_MAX_CLAIMS,
                     DEFAULT_MIN_COVERAGE, DEFAULT_MIN_SCORE)

ANSWERER_ROLE = Role(
    name="answerer",
    system_prompt=(
        "你是知识库问答助手。只依据检索片段中的原句作答，每条结论必须带"
        "doc_id、chunk_id、原文 quote 与字符偏移；没有依据时明确拒答。"
    ),
    allowed_tools=("answer_kb", "verify_citations"),
    output_schema={
        "type": "object", "required": ["question", "answered", "claims"],
        "properties": {
            "question": {"type": "string"},
            "answered": {"type": "boolean"},
            "answer": {"type": "string"},
            "claims": {"type": "array", "items": {"type": "object"}},
            "refusal": {"type": ["object", "null"]},
        },
        "additionalProperties": True,
    },
)


class KBQARoleRunner:
    """确定性角色执行器：answerer 读取 s2 的检索结果并产出最终答案。"""

    def __init__(self, registry, adapter=None):
        self.registry = registry
        self.adapter = adapter

    def run(self, role: str, inputs: dict, ctx) -> dict:
        if role != "answerer":
            return {"_error": ROLE_ERROR}
        started = time.perf_counter()
        results = ctx.state.get("results", {})
        task = ctx.state.get("task") or {}
        hits = (results.get("s2") or {}).get("hits") or []
        question = task.get("question") or (results.get("s2") or {}).get("query", "")

        produced = call_tool_for_role(
            ANSWERER_ROLE, self.registry, "answer_kb",
            {"question": question, "hits": hits,
             "min_score": float(task.get("min_score", DEFAULT_MIN_SCORE)),
             "min_coverage": float(task.get("min_coverage", DEFAULT_MIN_COVERAGE)),
             "max_claims": int(task.get("max_claims", DEFAULT_MAX_CLAIMS))}, ctx)
        if not produced.ok:
            return {"_error": produced.error_code or ROLE_ERROR}

        data = produced.data or {}
        claims = data.get("claims") or []
        checked = call_tool_for_role(
            ANSWERER_ROLE, self.registry, "verify_citations",
            {"claims": claims,
             "candidate_chunk_ids": [h.get("chunk_id", "") for h in hits]}, ctx)
        verified = (checked.data or {}).get("verified") or []
        rejected = (checked.data or {}).get("rejected") or []

        for item in verified:
            ctx.emit("verify.result", role="answerer", ok=True,
                     claim_id=item.get("claim_id", ""), verdict="confirmed",
                     reason_code="")
        for item in rejected:
            ctx.emit("verify.result", role="answerer", ok=False,
                     claim_id=item.get("claim_id", ""), verdict="reject",
                     reason_code=item.get("reason_code", ""))

        answered = bool(data.get("answered")) and bool(verified)
        refusal = data.get("refusal")
        if data.get("answered") and not verified:
            answered = False
            refusal = {
                "refused": True,
                "reason_code": CITATION_INVALID,
                "detail": "所有结论都未通过引用校验，按拒答处理",
                "thresholds": (data.get("refusal") or {}).get("thresholds") or {},
                "observed": {"top_score": (data.get("retrieval") or {}).get("top_score", 0.0),
                             "best_coverage": 0.0},
            }

        out = {
            "question": question,
            "answered": answered,
            "answer": data.get("answer", "") if answered else "",
            "claims": verified if answered else [],
            "refusal": None if answered else (refusal or {
                "refused": True, "reason_code": CITATION_INVALID,
                "detail": "没有可核验的结论", "thresholds": {}, "observed": {},
            }),
            "retrieval": {**(data.get("retrieval") or {}),
                          "hit_count": len(hits),
                          "top_chunk_id": (hits[0].get("chunk_id", "") if hits else "")},
            "verification": {"verified": len(verified), "rejected": len(rejected)},
            "fallback": True,
        }
        ctx.emit("role.message", role="answerer", ok=True,
                 latency_ms=int((time.perf_counter() - started) * 1000),
                 tokens_in=0, tokens_out=0, fallback=True,
                 count=len(out["claims"]))
        return out
