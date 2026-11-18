"""角色与编排：顺序管线、生产-核查循环、越权工具拦截。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .context import RunContext
from .registry import ToolRegistry
from .types import UNAUTHORIZED_TOOL, ToolResult

REASON_CODES = frozenset({
    "EVIDENCE_MISSING", "FACT_ADDED", "CITATION_INVALID", "SCHEMA_INVALID",
})


@dataclass(frozen=True)
class Role:
    name: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    output_schema: dict = field(default_factory=dict)
    max_tokens: int = 700

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))


class RoleRunner(Protocol):
    def run(self, role: str, inputs: dict, ctx: RunContext) -> dict: ...


def call_tool_for_role(role: Role, registry: ToolRegistry, name: str,
                       args: dict, ctx: RunContext) -> ToolResult:
    if name not in role.allowed_tools:
        return ToolResult(False, error_code=UNAUTHORIZED_TOOL,
                          message=f"角色 {role.name} 无权调用 {name}")
    return registry.call(name, args, ctx)


@dataclass
class Pipeline:
    roles: tuple[str, ...]

    def run(self, runner: RoleRunner, ctx: RunContext, inputs: dict) -> dict:
        current = dict(inputs)
        output: dict = {}
        for name in self.roles:
            started = time.perf_counter()
            output = runner.run(name, current, ctx)
            ctx.emit("role.message", role=name, ok=not bool(output.get("_error")),
                     latency_ms=int((time.perf_counter() - started) * 1000),
                     tokens_in=0, tokens_out=0,
                     fallback=bool(output.get("fallback")))
            # 前一个角色的产物作为下一个角色的输入
            current = {**current, "previous": output}
        return output


@dataclass
class ReviewLoop:
    """producer → verifier；不通过则带 reason_code 打回，最多 max_rejections 次。"""

    producer: str
    verifier: str
    max_rejections: int = 2

    def run(self, runner: RoleRunner, ctx: RunContext, inputs: dict) -> dict:
        current = dict(inputs)
        draft: dict = {}
        for round_no in range(self.max_rejections + 1):
            draft = runner.run(self.producer, current, ctx)
            verdict = runner.run(self.verifier, {**current, "draft": draft}, ctx)
            verdicts = verdict.get("verdicts", []) if isinstance(verdict, dict) else []
            rejected = [v for v in verdicts if v.get("verdict") == "reject"]
            for item in rejected:
                code = item.get("reason_code")
                if code not in REASON_CODES:
                    raise ValueError(f"非法 reason_code：{code}")
            ctx.emit("verify.result", ok=not rejected, count=len(verdicts),
                     reason_code=rejected[0].get("reason_code") if rejected else "")
            if not rejected:
                return {"ok": True, "draft": draft, "verdict": verdict,
                        "rounds": round_no + 1}
            current = {
                **current,
                "draft": draft,
                "reasons": [{"rule_id": v.get("rule_id"),
                             "reason_code": v.get("reason_code")} for v in rejected],
            }
        return {"ok": False, "draft": draft, "rounds": self.max_rejections + 1,
                "reasons": current.get("reasons", [])}


class DeterministicRunner:
    """测试与无模型场景使用：按角色名查表返回固定产物。"""

    def __init__(self, mapping: dict[str, Any]):
        self.mapping = mapping

    def run(self, role: str, inputs: dict, ctx: RunContext) -> dict:
        value = self.mapping.get(role)
        out = value(inputs, ctx) if callable(value) else value
        return dict(out or {})
