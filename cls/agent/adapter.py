"""模型适配层：包住 cls/llm.py 的本地 Ollama，不修改它。"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .. import config, llm


class ModelUnavailable(RuntimeError):
    """模型未就绪：网络、模型缺失或返回不可用时抛出。"""


@dataclass
class ModelReply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


class ModelAdapter(Protocol):
    def complete(self, messages: list[dict], schema: dict | None = None,
                 max_tokens: int = 800, temperature: float = 0.0) -> ModelReply: ...


class OllamaAdapter:
    """只连本机 Ollama；temperature 默认 0。"""

    def __init__(self, model: str | None = None, timeout: float | None = None):
        cfg = config.load_config()
        self.model = model or cfg.get("agent", {}).get("model") or cfg["model"]
        self.timeout = timeout or min(float(cfg.get("llm_timeout", 180)), 120.0)

    def complete(self, messages: list[dict], schema: dict | None = None,
                 max_tokens: int = 800, temperature: float = 0.0) -> ModelReply:
        st = llm.status(model=self.model)
        if not st.get("online") or not st.get("model_ready"):
            raise ModelUnavailable(st.get("error") or "本地模型不可用")

        body = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": 0.0 if temperature is None else float(temperature),
                "num_ctx": 4096,
                "num_predict": int(max_tokens),
            },
        }
        if schema:
            body["format"] = "json"

        cfg = config.load_config()
        url = cfg["ollama_url"].rstrip("/")
        request = urllib.request.Request(
            f"{url}/api/chat",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # 显式禁用环境代理，避免合同片段被转发到本机之外
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ModelUnavailable(f"本地模型调用失败：{exc.__class__.__name__}") from exc

        text = (data.get("message") or {}).get("content", "")
        if not isinstance(text, str) or not text.strip():
            raise ModelUnavailable("本地模型返回空内容")
        return ModelReply(
            text=text,
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
            model=self.model,
        )
