"""Agent 运行时的公共数据类型。

本模块只依赖标准库，不感知任何具体场景。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SideEffect = Literal["none", "local_write", "network"]

SIDE_EFFECTS = ("none", "local_write", "network")

# 统一错误码：工具与执行器共享，避免各处自造字符串
TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
INVALID_ARGS = "INVALID_ARGS"
TOOL_TIMEOUT = "TOOL_TIMEOUT"
TOOL_BUDGET_EXCEEDED = "TOOL_BUDGET_EXCEEDED"
TOOL_ERROR = "TOOL_ERROR"
UNAUTHORIZED_TOOL = "UNAUTHORIZED_TOOL"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
SKIPPED_DEP = "SKIPPED_DEP"
TRUNCATED = "TRUNCATED"
SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
ROLE_ERROR = "ROLE_ERROR"

ERROR_CODES = frozenset({
    TOOL_NOT_FOUND, INVALID_ARGS, TOOL_TIMEOUT, TOOL_BUDGET_EXCEEDED,
    TOOL_ERROR, UNAUTHORIZED_TOOL, BUDGET_EXCEEDED, SKIPPED_DEP,
    TRUNCATED, SOURCE_NOT_FOUND, MODEL_UNAVAILABLE, ROLE_ERROR,
})


@dataclass(frozen=True)
class ToolSpec:
    """工具描述。params/returns 为最小 JSON Schema 子集。"""

    name: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    returns: dict[str, Any] = field(default_factory=dict)
    side_effect: SideEffect = "none"
    timeout_s: float = 10.0
    max_calls_per_run: int | None = None
    scopes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolSpec.name 不能为空")
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(f"未知 side_effect: {self.side_effect}")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s 必须大于 0")
        if self.max_calls_per_run is not None and self.max_calls_per_run <= 0:
            raise ValueError("max_calls_per_run 必须大于 0")
        object.__setattr__(self, "scopes", tuple(self.scopes))


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str = ""
    latency_ms: int = 0
    tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error_code": self.error_code,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "tokens": self.tokens,
        }


@dataclass
class Step:
    """计划中的一步。tool 与 role 二选一。

    foreach 支持 ``"<step_id>.<key>"`` 或 ``"<step_id>.<key>[field=a|b]"``，
    运行时把列表展开为子步骤 ``<step_id>[i]``。
    """

    id: str
    role: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    foreach: str | None = None
    max_attempts: int = 1

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Step.id 不能为空")
        if self.tool is None and not self.role:
            raise ValueError(f"Step {self.id} 必须指定 tool 或 role")
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        object.__setattr__(self, "depends_on", tuple(self.depends_on))


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    version: int = 1
    reason: str = ""


@dataclass
class Budget:
    max_steps: int = 20
    max_tool_calls: int = 60
    max_wall_s: float = 180.0
    max_tokens: int = 20000

    def to_dict(self) -> dict:
        return {
            "max_steps": self.max_steps,
            "max_tool_calls": self.max_tool_calls,
            "max_wall_s": self.max_wall_s,
            "max_tokens": self.max_tokens,
        }


@dataclass
class RunResult:
    run_id: str
    status: str
    stop_reason: str
    artifacts: dict[str, Any]
    metrics: dict[str, Any]
    trace_path: str

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
            "trace_path": self.trace_path,
        }
