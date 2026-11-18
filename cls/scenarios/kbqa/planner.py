"""知识库问答规划器：固定四步，不依赖模型。"""
from __future__ import annotations

from ...agent.types import Plan, Step
from .answer import DEFAULT_MAX_CLAIMS, DEFAULT_MIN_COVERAGE, DEFAULT_MIN_SCORE


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_task(inputs: dict) -> dict:
    inputs = inputs or {}
    paths = inputs.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]
    return {
        "paths": [str(p) for p in paths],
        "question": str(inputs.get("question") or ""),
        "top_k": _int(inputs.get("top_k"), 8),
        "chunk_chars": _int(inputs.get("chunk_chars"), 400),
        "overlap": _int(inputs.get("overlap"), 60),
        "min_score": _float(inputs.get("min_score"), DEFAULT_MIN_SCORE),
        "min_coverage": _float(inputs.get("min_coverage"), DEFAULT_MIN_COVERAGE),
        "max_claims": _int(inputs.get("max_claims"), DEFAULT_MAX_CLAIMS),
        "write": bool(inputs.get("write", True)),
        "run_id": str(inputs.get("run_id") or ""),
    }


def build_steps(task: dict) -> list[Step]:
    steps = [
        Step("s1", "", tool="ingest_documents",
             args={"paths": "{task.paths}",
                   "chunk_chars": "{task.chunk_chars}",
                   "overlap": "{task.overlap}"}),
        Step("s2", "", tool="search_kb",
             args={"query": "{task.question}", "top_k": "{task.top_k}",
                   "doc_ids": "{s1.doc_ids}"},
             depends_on=("s1",)),
        Step("s3", "answerer", args={}, depends_on=("s1", "s2")),
    ]
    if task.get("write", True):
        steps.append(Step("s4", "", tool="write_answer",
                          args={"run_id": "{task.run_id}"}, depends_on=("s3",)))
    return steps


class KBQAPlanner:
    reason = "template"

    def plan(self, task: dict, tools, budget) -> Plan:
        return Plan(build_steps(task), reason=self.reason)

    def replan(self, plan: Plan, observation: dict) -> Plan:
        return plan
