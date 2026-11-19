"""知识库问答场景装配：K1–K5 + answerer + 固定四步。"""
from __future__ import annotations

from ..base import Scenario
from .planner import KBQAPlanner, build_task
from .roles import ANSWERER_ROLE, KBQARoleRunner
from .tools import TOOL_SPECS


def render(ctx) -> dict:
    results = ctx.state.get("results", {})
    ingest = results.get("s1") or {}
    answer = results.get("s3") or {}
    return {
        "doc_ids": ingest.get("doc_ids", []),
        "docs": ingest.get("docs", []),
        "stats": ingest.get("stats", {}),
        "question": answer.get("question", ""),
        "answered": bool(answer.get("answered")),
        "answer": answer.get("answer", ""),
        "claims": answer.get("claims", []),
        "refusal": answer.get("refusal"),
        "retrieval": answer.get("retrieval", {}),
        "verification": answer.get("verification", {}),
    }


def build_scenario(adapter=None) -> Scenario:
    return Scenario(
        name="kbqa",
        title="知识库问答演示",
        version="1",
        tools=tuple(TOOL_SPECS),
        roles={"answerer": ANSWERER_ROLE},
        planner=KBQAPlanner(),
        task_builder=build_task,
        renderer=render,
        role_runner_factory=lambda registry, runtime_adapter=None:
            KBQARoleRunner(registry, adapter or runtime_adapter),
    )


KBQA_SCENARIO = build_scenario()
