"""Ollama 本地模型客户端：仅访问 127.0.0.1 / localhost，绝不连接外部服务。"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import requests

from . import config


class LocalModelError(RuntimeError):
    pass


def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _norm_model(name: str) -> str:
    """ollama 列表省略 :latest 后缀，比较前统一补齐。"""
    return name if ":" in name else f"{name}:latest"


def status(url: str | None = None, model: str | None = None) -> dict:
    cfg = config.load_config()
    url = (url or cfg["ollama_url"]).rstrip("/")
    model = model or cfg["model"]
    out = {
        "url": url,
        "model": model,
        "local_endpoint": _is_local(url),
        "online": False,
        "models": [],
        "model_ready": False,
        "error": "",
    }
    if not _is_local(url):
        out["error"] = "出于隐私策略，仅允许连接本机地址（127.0.0.1 / localhost）。"
        return out
    try:
        # 只连本机：禁用环境变量里的代理，并禁止重定向，避免合同片段被转发到外部
        r = requests.get(f"{url}/api/tags", timeout=4,
                         proxies={"http": None, "https": None}, allow_redirects=False)
        r.raise_for_status()
        models = [m.get("name", "") for m in r.json().get("models", [])]
        out["online"] = True
        out["models"] = models
        # 按完整名称比对：装着 qwen2.5:7b 不代表配置里的 qwen2.5:3b 可用
        out["model_ready"] = any(_norm_model(m) == _norm_model(model) for m in models)
        if not out["model_ready"]:
            out["error"] = f"本机未找到模型 {model}，请先执行 ollama pull {model}"
    except Exception as e:
        out["error"] = f"未连接到本地 Ollama：{e.__class__.__name__}"
    return out


PROMPT = """你是资深法务律师，正在审阅一份中文合同。请只依据合同原文抽取结构化信息，不得推测未写明的内容。
严格输出 JSON，不要输出任何解释。字段要求：
{
  "contract_type": "合同类型，如 软件开发服务合同/劳动合同/采购合同/保密协议/租赁合同/其他",
  "contract_name": "合同标题",
  "party_a": "甲方/委托方/采购方/用人单位全称",
  "party_b": "乙方/受托方/供应方/劳动者全称",
  "amount_total": "合同金额（保留原文表述，如 人民币 120 万元）",
  "currency": "币种",
  "term_start": "履行开始日期 YYYY-MM-DD，没有则空字符串",
  "term_end": "履行结束日期 YYYY-MM-DD，没有则空字符串",
  "payment_terms": "付款方式与节点，摘录关键约定",
  "breach_liability": "违约责任约定，摘录关键内容",
  "dispute_resolution": "争议解决方式（法院/仲裁/管辖地）",
  "confidentiality": "保密条款要点，没有写 未约定",
  "ip_ownership": "知识产权归属，没有写 未约定",
  "probation": "试用期约定（劳动合同适用）",
  "personal_info": "个人信息/数据条款要点，没有写 未约定",
  "signing_date": "签订日期 YYYY-MM-DD 或原文",
  "summary": "【必填】用两句律师视角的话概括本合同最值得注意的风险，必须返回，不能为空"
}
【重要】summary 字段必须返回，1–2 句话，不超过 80 字。若某字段原文确实没有，填写空字符串或"未约定"。合同原文如下：
"""


def extract_fields(text: str, model: str | None = None, timeout: int | None = None) -> dict:
    cfg = config.load_config()
    url = cfg["ollama_url"].rstrip("/")
    model = model or cfg["model"]
    timeout = timeout or cfg.get("llm_timeout", 180)
    if not _is_local(url):
        raise LocalModelError("仅允许连接本机 Ollama")

    # 头尾都要给：文末常放违约责任、争议解决、签署日期，只截前 6000 字会漏掉这些条款
    limit = 6000
    if len(text) > limit:
        head = int(limit * 0.75)
        excerpt = text[:head] + "\n【……中间内容省略……】\n" + text[-(limit - head):]
    else:
        excerpt = text
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": cfg.get("num_ctx", 8192), "num_predict": 900},
        "messages": [
            {"role": "system", "content": "你是严谨的中国法务律师，只做信息抽取，不编造事实。"},
            {"role": "user", "content": PROMPT + excerpt},
        ],
    }
    try:
        r = requests.post(f"{url}/api/chat", json=body, timeout=timeout,
                          proxies={"http": None, "https": None}, allow_redirects=False)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "")
    except Exception as e:
        raise LocalModelError(f"本地模型调用失败：{e.__class__.__name__}") from e
    return _loads(content)


# LLM 辅助判别：验证单条 finding 是否真正成立
VERIFY_PROMPT = """你是合同审阅复核助手。请判断下面这条风险 finding 是否真正成立。

【合同片段】（从命中处 ±150 字截取）：
{context}

【Finding 内容】：
规则编号：{rule_id}
规则标题：{rule_title}
规则类型：{mode}（match=检测合同中有坏条款；absence=合同本该有但没写的条款）
severity：{severity_label}

请输出严格 JSON（不要输出任何其他内容）：
{{"verdict": "confirm" 或 "reject" 或 "doubtful", "confidence": 0.0 到 1.0 的小数, "reason": "1 句话说明判断依据"}}

约束：
- verdict 只能是 confirm / reject / doubtful 三者之一
- confidence 必须是 0.0–1.0；≥ 0.5 才允许输出 confirm 或 reject，否则必须是 doubtful
- 不得编造合同片段里没有的内容
- 如果合同片段太短看不出来，输出 doubtful
"""


def verify_clause(context: str, rule_id: str, rule_title: str,
                  mode: str, severity_label: str,
                  model: str | None = None, timeout: int = 15) -> dict | None:
    """调本地模型验证单条 finding。

    返回 {"verdict": "confirm|reject|doubtful", "confidence": float, "reason": str}；
    任何异常 / 超时 / JSON 解析失败都返回 None（让调用方跳过该 finding 的 LLM 复核）。
    """
    cfg = config.load_config()
    url = cfg["ollama_url"].rstrip("/")
    model = model or cfg["model"]
    if not _is_local(url):
        return None

    prompt = VERIFY_PROMPT.format(
        context=context, rule_id=rule_id, rule_title=rule_title,
        mode=mode, severity_label=severity_label,
    )
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 200},
        "messages": [
            {"role": "system", "content": "你是严谨的合同审阅复核助手，只输出 JSON，不输出解释。"},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        r = requests.post(f"{url}/api/chat", json=body, timeout=timeout,
                          proxies={"http": None, "https": None}, allow_redirects=False)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "").strip()
    except Exception:
        return None

    data = _loads(content)
    if not isinstance(data, dict):
        return None

    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in {"confirm", "reject", "doubtful"}:
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    # 约束：confidence < 0.5 强制转为 doubtful
    if confidence < 0.5 and verdict != "doubtful":
        verdict = "doubtful"
        confidence = 0.0

    reason = str(data.get("reason", ""))[:120]
    return {"verdict": verdict, "confidence": round(confidence, 2), "reason": reason}


RULES_PROMPT = """你是企业法务，请把下面的制度/审查清单文本转成合同审查规则 JSON。
严格输出 {"rules": [...]}，不要输出任何解释。每条规则字段：
- title：检查点名称（20 字以内）
- severity：high / medium / low
- kind：forbid（合同中出现即违规）/ require（合同中缺失即违规）/ threshold（数值口径限制）
- keywords：用于在合同原文中定位该事项的关键词，每个 2–6 字，1–3 个（threshold 时第一个必须是主题词，如"违约金"）
- param_key：仅 threshold 填；可选 penalty_high_pct（违约金上限）/ deposit_max_pct（定金上限）/ probation_salary_pct（试用期工资下限）/ probation_max_months（试用期上限月数）/ compete_max_months（竞业限制上限月数）/ lease_max_years（租赁期限上限年数）；无法对应则省略
- op 与 value：仅 threshold 填；op 为 >（超过即违规）或 <（低于即违规），value 为数值（如 20 表示 20%）
- related：与本规则检查同一事项的内置规则编号（违约金填 R04、定金填 R06、试用期工资填 L02、试用期期限填 L01、竞业限制填 L03、租赁期限填 Z01），不确定则不填
- summary：该规则依据的制度原文（60 字以内）
无法确定是否为审查规则的段落请跳过。文本如下：
"""


def extract_rules(text: str, model: str | None = None, timeout: int | None = None) -> list[dict]:
    """用本地模型把制度/清单文档抽取为规则草稿（宽松 schema，由 app 层校验转换）。"""
    cfg = config.load_config()
    url = cfg["ollama_url"].rstrip("/")
    model = model or cfg["model"]
    timeout = timeout or cfg.get("llm_timeout", 180)
    if not _is_local(url):
        raise LocalModelError("仅允许连接本机 Ollama")
    limit = 6000
    excerpt = text[:limit] if len(text) <= limit else (
        text[:int(limit * 0.75)] + "\n【……中间内容省略……】\n" + text[-(limit - int(limit * 0.75)):])
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": cfg.get("num_ctx", 8192), "num_predict": 2000},
        "messages": [
            {"role": "system", "content": "你是严谨的企业法务，只做规则抽取，不编造制度中不存在的口径。"},
            {"role": "user", "content": RULES_PROMPT + excerpt},
        ],
    }
    try:
        r = requests.post(f"{url}/api/chat", json=body, timeout=timeout,
                          proxies={"http": None, "https": None}, allow_redirects=False)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "")
    except Exception as e:
        raise LocalModelError(f"本地模型调用失败：{e.__class__.__name__}") from e
    data = _loads(content)
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise LocalModelError("本地模型返回内容缺少 rules 列表")
    return [r for r in rules if isinstance(r, dict)]


def _loads(content: str) -> dict:
    content = content.strip()
    try:
        data = json.loads(content)
    except Exception:
        data = None
    if data is None:
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    # 小模型可能返回数组/字符串/数字，必须当成本次抽取失败交给规则模式兜底
    if not isinstance(data, dict):
        raise LocalModelError("本地模型返回内容不是 JSON 对象")
    return data
