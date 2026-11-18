"""知识库问答：无依据必须拒答，阈值可配置且原因可解释。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cls import config
from cls.agent.context import RunContext
from cls.agent.registry import ToolRegistry
from cls.agent.trace import Trace
from cls.agent.types import Budget
from cls.scenarios.kbqa.documents import DOCS
from cls.scenarios.kbqa.tools import TOOL_SPECS

ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "samples" / "kb"
ANSWERABLE = "默认预算是多少？"
# 关键词覆盖不满分的问法，用来验证「阈值调高就会拒答」仍然成立。
PARTIAL = "默认预算里的步骤上限是多少？"
UNANSWERABLE = "知识库是否支持多租户隔离？"


@pytest.fixture(autouse=True)
def _clean_store():
    DOCS.clear()
    yield
    DOCS.clear()


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    for spec, fn in TOOL_SPECS:
        reg.register(spec, fn)
    return reg


@pytest.fixture
def ctx(tmp_path) -> RunContext:
    return RunContext(run_id="test-refuse", trace=Trace(tmp_path / "trace.jsonl", "test-refuse"),
                      budget=Budget(), state={"results": {}})


def _ask(registry, ctx, question: str, **thresholds) -> dict:
    ingest = registry.call("ingest_documents", {"paths": [str(KB_DIR)]}, ctx)
    assert ingest.ok
    search = registry.call("search_kb", {"query": question,
                                         "doc_ids": ingest.data["doc_ids"],
                                         "top_k": 8}, ctx)
    assert search.ok
    result = registry.call("answer_kb", {"question": question,
                                         "hits": search.data["hits"], **thresholds}, ctx)
    assert result.ok
    return result.data


def test_answerable_question_is_answered(registry, ctx):
    data = _ask(registry, ctx, ANSWERABLE)
    assert data["answered"] is True
    assert data["refusal"] is None


def test_unanswerable_question_is_refused(registry, ctx):
    data = _ask(registry, ctx, UNANSWERABLE)
    assert data["answered"] is False
    assert data["claims"] == []
    assert data["refusal"]["reason_code"] == "LOW_COVERAGE"


def test_high_score_threshold_refuses(registry, ctx):
    data = _ask(registry, ctx, ANSWERABLE, min_score=0.99)
    assert data["answered"] is False
    assert data["refusal"]["reason_code"] == "LOW_SCORE"


def test_high_coverage_threshold_refuses(registry, ctx):
    data = _ask(registry, ctx, PARTIAL, min_coverage=0.99)
    assert data["answered"] is False
    assert data["refusal"]["reason_code"] == "LOW_COVERAGE"


def test_refusal_echoes_thresholds_and_observed(registry, ctx):
    data = _ask(registry, ctx, PARTIAL, min_coverage=0.99, min_score=0.05)
    refusal = data["refusal"]
    assert refusal["thresholds"] == {"min_score": 0.05, "min_coverage": 0.99,
                                     "max_claims": 2}
    assert 0.0 <= refusal["observed"]["best_coverage"] < 0.99
    assert refusal["detail"]


def test_coverage_ignores_interrogatives(registry, ctx):
    from cls.scenarios.kbqa.answer import question_keywords

    keywords = question_keywords("默认预算是多少？")
    assert "多少" not in keywords
    assert {"默认", "预算"} <= set(keywords)
    for question, word in [("这套 Agent 框架有哪些组成部分？", "哪些"),
                           ("中文文本是怎么切词的？", "怎么"),
                           ("知识库是否支持多租户隔离？", "是否")]:
        assert word not in question_keywords(question)

    data = _ask(registry, ctx, ANSWERABLE)
    assert data["answered"] is True
    assert {"默认", "预算"} <= set(data["retrieval"]["keywords"])


def test_paraphrased_question_is_answered(registry, ctx):
    # 旧口径把「分别 / 多少」也算进分母，这题覆盖率不够会被拒答。
    data = _ask(registry, ctx, "默认的片段长度和相邻重叠分别是多少字符？")
    assert data["answered"] is True
    assert "400 个字符" in data["claims"][0]["quote"]


def test_empty_question_is_rejected(registry, ctx):
    ingest = registry.call("ingest_documents", {"paths": [str(KB_DIR)]}, ctx)
    result = registry.call("answer_kb", {"question": " ", "hits": []}, ctx)
    assert not result.ok or result.data["answered"] is False
    assert ingest.ok


# ---------------------------------------------------------------- 端到端
@pytest.fixture
def run(tmp_path, monkeypatch):
    from cls.agent.runtime import AgentRuntime
    from cls.scenarios.kbqa.manifest import KBQA_SCENARIO

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "data" / "exports")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "history.db")
    runtime = AgentRuntime(KBQA_SCENARIO, data_dir=tmp_path / "traces")
    return runtime.run(
        {"paths": [str(KB_DIR)], "question": UNANSWERABLE, "write": True,
         "run_id": "refuse"},
        budget=Budget(), run_id="refuse", trace_path=tmp_path / "trace.jsonl",
    )


def test_e2e_refusal_is_reported_and_written(run):
    assert run.status == "ok"
    summary = run.artifacts["summary"]
    assert summary["answered"] is False
    assert summary["claims"] == []
    assert summary["refusal"]["reason_code"] == "LOW_COVERAGE"
    assert summary["refusal"]["thresholds"]["min_coverage"] == 0.40

    path = Path(config.EXPORTS_DIR) / "refuse-answer.md"
    body = path.read_text(encoding="utf-8")
    assert "已拒答" in body
    assert "LOW_COVERAGE" in body

    events = [json.loads(line) for line in Path(run.trace_path).read_text().splitlines()
              if line]
    assert events[-1]["event"] == "run.end"
    assert any(e["event"] == "tool.result" and e["tool"] == "answer_kb" for e in events)
