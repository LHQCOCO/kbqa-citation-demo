"""知识库问答演示页：页面渲染、真实运行、拒答原因与参数校验。"""
from __future__ import annotations

import re

import pytest

import app as app_module
from cls import config
from cls.scenarios.kbqa.documents import DOCS


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """trace 与导出全部落在临时目录，知识库进程内状态在用例间清空。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    DOCS.clear()
    yield
    DOCS.clear()


@pytest.fixture
def client():
    return app_module.app.test_client()


def _html(resp) -> str:
    return resp.get_data(as_text=True)


def _run_id(html: str) -> str:
    match = re.search(r"kbqa-page-[0-9a-f]{8}", html)
    assert match, "页面没有渲染本次 run_id"
    return match.group(0)


def test_page_renders_form_examples_and_boundary(client):
    resp = client.get("/kbqa")
    assert resp.status_code == 200
    html = _html(resp)
    assert 'action="/kbqa/ask"' in html
    assert "Agent 内核由哪些模块构成" in html      # 真实失败案例可一键复现
    assert "今天天气怎么样？" in html              # NO_HITS 路径
    assert "11 个片段" in html                    # 语料规模按实际目录计算
    assert "min_coverage=0.4" in html
    assert "90.0%" in html and "30.0%" in html    # 评测边界如实写出
    assert "17/20" in html                        # 扩题后的拒答结果
    assert "不代表泛化能力" in html


def test_ask_runs_real_pipeline_and_shows_retrieval_and_claim(client):
    resp = client.post("/kbqa/ask",
                       data={"question": "默认预算包含多少个步骤和多少次工具调用？"})
    assert resp.status_code == 200
    html = _html(resp)
    assert "已回答" in html
    assert "默认预算是 20 个步骤、60 次工具调用、180 秒墙钟时间与 20000 个 token。" in html
    assert "agent_runtime.md" in html
    assert "字符 330–377" in html
    assert "展开原文上下文" in html
    assert "RETRIEVAL" in html
    assert "0.477" in html                        # 本次检索 top_score
    _run_id(html)


def test_unlisted_question_is_answered_from_corpus_not_preset(client):
    """问题不在仓库问答集里，仍能作答，说明是现跑的检索链路。"""
    resp = client.post("/kbqa/ask", data={"question": "默认预算里墙钟时间是多少秒？"})
    html = _html(resp)
    assert resp.status_code == 200
    assert "已回答" in html
    assert "180 秒" in html


def test_failing_case_refused_with_low_coverage(client):
    resp = client.post("/kbqa/ask", data={"question": "Agent 内核由哪些模块构成"})
    assert resp.status_code == 200
    html = _html(resp)
    assert "已拒答" in html
    assert "LOW_COVERAGE" in html
    assert "本次没有生成任何答案句子" in html
    assert "0.2703" in html          # 观测 top_score
    assert "0.2" in html             # 观测 best_coverage（关键词口径）
    assert "0.4" in html             # min_coverage 阈值


def test_out_of_scope_question_reports_no_hits(client):
    resp = client.post("/kbqa/ask", data={"question": "今天天气怎么样？"})
    assert resp.status_code == 200
    html = _html(resp)
    assert "NO_HITS" in html
    assert "本次检索没有返回任何片段" in html


def test_empty_question_is_rejected(client):
    resp = client.post("/kbqa/ask", data={"question": "   "})
    assert resp.status_code == 400
    assert "请输入要问的问题" in _html(resp)


def test_each_request_runs_a_new_question(client):
    first = _run_id(_html(client.post("/kbqa/ask", data={"question": "第一版支持哪些文档格式？"})))
    second = _run_id(_html(client.post("/kbqa/ask", data={"question": "Trace 把运行写成什么格式？"})))
    assert first != second
