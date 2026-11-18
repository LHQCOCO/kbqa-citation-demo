"""知识库问答：入库切片与混合检索。"""
from __future__ import annotations

from pathlib import Path

import pytest

from cls.agent.context import RunContext
from cls.agent.registry import ToolRegistry
from cls.agent.trace import Trace
from cls.agent.types import Budget
from cls.scenarios import SCENARIOS
from cls.scenarios.kbqa.chunking import chunk_text, read_document
from cls.scenarios.kbqa.documents import DOCS
from cls.scenarios.kbqa.planner import build_steps, build_task
from cls.scenarios.kbqa.tools import TOOL_SPECS

ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "samples" / "kb"
DOC_FILES = sorted(p for p in KB_DIR.iterdir() if p.suffix in {".md", ".txt"})


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
    return RunContext(run_id="test-kbqa", trace=Trace(tmp_path / "trace.jsonl", "test-kbqa"),
                      budget=Budget(), state={"results": {}})


def _ingest(registry, ctx) -> dict:
    result = registry.call("ingest_documents", {"paths": [str(KB_DIR)]}, ctx)
    assert result.ok, result.message
    return result.data


def _search(registry, ctx, query: str, **extra) -> dict:
    ingest = _ingest(registry, ctx)
    result = registry.call("search_kb", {"query": query, "doc_ids": ingest["doc_ids"],
                                         "top_k": extra.pop("top_k", 8), **extra}, ctx)
    assert result.ok, result.message
    return result.data


# ---------------------------------------------------------------- 切片
@pytest.mark.parametrize("path", DOC_FILES, ids=lambda p: p.name)
def test_chunk_text_matches_original_slice(path):
    _, text = read_document(path)
    chunks = chunk_text("kb-test", text, chunk_chars=400, overlap=60)
    assert chunks
    previous = -1
    for chunk in chunks:
        assert chunk.start < chunk.end
        assert text[chunk.start:chunk.end] == chunk.text
        assert chunk.start >= previous
        previous = chunk.start


def test_ingest_supports_md_and_txt(registry, ctx):
    data = _ingest(registry, ctx)
    names = {d["name"] for d in data["docs"]}
    assert len(names) == 3
    assert any(n.endswith(".md") for n in names)
    assert any(n.endswith(".txt") for n in names)
    assert data["stats"]["docs"] == 3
    assert data["stats"]["chunks"] >= 3


def test_ingest_rejects_unsupported_extension(registry, ctx, tmp_path):
    bad = tmp_path / "manual.pdf"
    bad.write_text("not a supported format", encoding="utf-8")
    result = registry.call("ingest_documents", {"paths": [str(bad)]}, ctx)
    assert not result.ok
    assert result.error_code == "INVALID_ARGS"


# ---------------------------------------------------------------- 检索
def test_search_hits_expected_chunk(registry, ctx):
    data = _search(registry, ctx, "切片默认把每个片段控制在多少字符？", top_k=3)
    assert data["hits"]
    top = data["hits"][0]
    assert "400 个字符" in top["quote"]
    assert top["keyword_score"] > 0
    assert top["vector_score"] > 0


def test_search_respects_top_k_and_doc_filter(registry, ctx):
    data = _search(registry, ctx, "检索默认返回多少条片段？", top_k=2)
    assert len(data["hits"]) == 2

    ingest = _ingest(registry, ctx)
    only = ingest["doc_ids"][0]
    filtered = registry.call("search_kb", {"query": "检索默认返回多少条片段？",
                                           "doc_ids": [only], "top_k": 5}, ctx)
    assert filtered.ok
    assert filtered.data["hits"]
    assert {h["doc_id"] for h in filtered.data["hits"]} == {only}


def test_search_is_deterministic(registry, ctx):
    query = "关键词通道与向量通道的默认权重是多少？"
    first = _search(registry, ctx, query, top_k=5)["hits"]
    second = _search(registry, ctx, query, top_k=5)["hits"]
    assert [h["chunk_id"] for h in first] == [h["chunk_id"] for h in second]
    assert [h["score"] for h in first] == [h["score"] for h in second]


def test_search_without_ingest_reports_source_not_found(registry, ctx):
    result = registry.call("search_kb", {"query": "任意问题"}, ctx)
    assert not result.ok
    assert result.error_code == "SOURCE_NOT_FOUND"


# ---------------------------------------------------------------- 计划与注册
def test_planner_steps_use_registered_tools_and_roles():
    steps = build_steps(build_task({"paths": [str(KB_DIR)], "question": "q"}))
    assert [s.id for s in steps] == ["s1", "s2", "s3", "s4"]
    tool_names = {spec.name for spec, _ in TOOL_SPECS}
    role_names = {role.name for role in SCENARIOS["kbqa"].roles.values()}
    for step in steps:
        if step.tool:
            assert step.tool in tool_names
        if step.role:
            assert step.role in role_names
        for dep in step.depends_on:
            assert dep in {s.id for s in steps}


def test_kbqa_scenario_is_registered():
    assert SCENARIOS["kbqa"].name == "kbqa"
    assert len(SCENARIOS["kbqa"].tools) == 5
    assert set(SCENARIOS["kbqa"].roles) == {"answerer"}
