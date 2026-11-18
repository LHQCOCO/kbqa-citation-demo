"""知识库问答：引用必须能回到原文 span，篡改与错配要被判掉。"""
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
QUESTION = "默认拒答阈值的 min_score 和 min_coverage 是多少？"


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
    return RunContext(run_id="test-cite", trace=Trace(tmp_path / "trace.jsonl", "test-cite"),
                      budget=Budget(), state={"results": {}})


def _prepare(registry, ctx) -> tuple[dict, dict]:
    ingest = registry.call("ingest_documents", {"paths": [str(KB_DIR)]}, ctx)
    assert ingest.ok
    search = registry.call("search_kb", {"query": QUESTION,
                                         "doc_ids": ingest.data["doc_ids"],
                                         "top_k": 8}, ctx)
    assert search.ok
    return ingest.data, search.data


def _answer(registry, ctx, **thresholds) -> dict:
    ingest, search = _prepare(registry, ctx)
    result = registry.call("answer_kb", {"question": QUESTION,
                                         "hits": search["hits"], **thresholds}, ctx)
    assert result.ok
    return result.data


# ---------------------------------------------------------------- 抽取即引用
def test_answer_claims_are_verbatim(registry, ctx):
    data = _answer(registry, ctx)
    assert data["answered"] is True
    assert data["claims"]
    for claim in data["claims"]:
        doc = DOCS.get(claim["doc_id"])
        span = claim["span"]
        assert doc is not None
        assert doc.text[span["start"]:span["end"]] == claim["quote"]
        assert claim["quote"] in doc.text


def test_verify_accepts_valid_claims(registry, ctx):
    data = _answer(registry, ctx)
    result = registry.call("verify_citations", {"claims": data["claims"]}, ctx)
    assert result.ok
    assert result.data["counts"]["rejected"] == 0
    assert result.data["counts"]["verified"] == len(data["claims"])


def test_verify_rejects_tampered_quote(registry, ctx):
    data = _answer(registry, ctx)
    claim = dict(data["claims"][0])
    claim["quote"] = claim["quote"] + "（篡改）"
    result = registry.call("verify_citations", {"claims": [claim]}, ctx)
    assert not result.ok
    assert result.data["rejected"][0]["reason_code"] == "SPAN_MISMATCH"


def test_verify_rejects_unknown_doc(registry, ctx):
    data = _answer(registry, ctx)
    claim = {**data["claims"][0], "doc_id": "kb-deadbeef"}
    result = registry.call("verify_citations", {"claims": [claim]}, ctx)
    assert not result.ok
    assert result.data["rejected"][0]["reason_code"] == "SOURCE_NOT_FOUND"


def test_verify_rejects_offset_drift(registry, ctx):
    data = _answer(registry, ctx)
    claim = {**data["claims"][0],
             "span": {**data["claims"][0]["span"]}}
    claim["span"]["start"] += 50
    result = registry.call("verify_citations", {"claims": [claim]}, ctx)
    assert not result.ok
    assert result.data["rejected"][0]["reason_code"] == "OFFSET_DRIFT"


def test_verify_rejects_chunk_outside_candidates(registry, ctx):
    data = _answer(registry, ctx)
    result = registry.call("verify_citations", {
        "claims": data["claims"], "candidate_chunk_ids": ["kb-x#c999"]}, ctx)
    assert not result.ok
    assert result.data["rejected"][0]["reason_code"] == "NOT_IN_CANDIDATES"


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
        {"paths": [str(KB_DIR)], "question": QUESTION, "write": True, "run_id": "e2e"},
        budget=Budget(), run_id="e2e", trace_path=tmp_path / "trace.jsonl",
    )


def test_e2e_claims_are_cited_and_traceable(run):
    assert run.status == "ok"
    summary = run.artifacts["summary"]
    assert summary["answered"] is True
    assert summary["claims"]
    for claim in summary["claims"]:
        doc = DOCS.get(claim["doc_id"])
        span = claim["span"]
        assert doc.text[span["start"]:span["end"]] == claim["quote"]
    path = Path(config.EXPORTS_DIR) / "e2e-answer.md"
    assert path.exists()
    assert "## 引用" in path.read_text(encoding="utf-8")

    events = [json.loads(line) for line in Path(run.trace_path).read_text().splitlines()
              if line]
    names = {e["event"] for e in events}
    assert {"run.start", "plan.created", "step.start", "tool.call", "tool.result",
            "role.message", "verify.result", "step.end", "run.end"} <= names
