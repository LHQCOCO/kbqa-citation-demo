"""工具注册表：参数校验、调用预算、超时与错误码收敛。"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .context import RunContext
from .schema import validate
from .types import (
    INVALID_ARGS, TOOL_BUDGET_EXCEEDED, TOOL_ERROR, TOOL_NOT_FOUND,
    TOOL_TIMEOUT, ToolResult, ToolSpec,
)

ToolFn = Callable[[dict, RunContext], ToolResult]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolFn]] = {}
        self._lock = threading.RLock()

    def register(self, spec: ToolSpec, fn: ToolFn) -> None:
        if not callable(fn):
            raise TypeError("工具实现必须可调用")
        with self._lock:
            if spec.name in self._tools:
                raise ValueError(f"工具重复注册：{spec.name}")
            self._tools[spec.name] = (spec, fn)

    def get(self, name: str) -> tuple[ToolSpec, ToolFn] | None:
        with self._lock:
            return self._tools.get(name)

    def specs(self, scopes: tuple[str, ...] | None = None) -> list[ToolSpec]:
        with self._lock:
            items = [spec for spec, _ in self._tools.values()]
        if scopes is None:
            return items
        allowed = set(scopes)
        return [s for s in items if not s.scopes or set(s.scopes) <= allowed]

    def call(self, name: str, args: dict | None, ctx: RunContext) -> ToolResult:
        entry = self.get(name)
        if entry is None:
            return ToolResult(False, error_code=TOOL_NOT_FOUND,
                              message=f"未注册的工具：{name}")
        spec, fn = entry
        args = args or {}
        if not isinstance(args, dict):
            return ToolResult(False, error_code=INVALID_ARGS,
                              data={"details": [{"path": "$", "message": "参数必须是对象"}]},
                              message="参数必须是 JSON 对象")

        errors = validate(spec.params, args)
        if errors:
            return ToolResult(False, error_code=INVALID_ARGS,
                              data={"details": errors},
                              message=f"参数校验失败（{len(errors)} 处）")

        # 预算：全局调用数与单工具调用数
        by_name = ctx.state.setdefault("tool_calls_by_name", {})
        total = int(ctx.state.get("tool_calls", 0))
        used = int(by_name.get(name, 0))
        if total >= ctx.budget.max_tool_calls:
            return ToolResult(False, error_code=TOOL_BUDGET_EXCEEDED,
                              message="本轮工具调用总数已达上限")
        if spec.max_calls_per_run is not None and used >= spec.max_calls_per_run:
            return ToolResult(False, error_code=TOOL_BUDGET_EXCEEDED,
                              message=f"工具 {name} 调用次数已达上限")

        ctx.state["tool_calls"] = total + 1
        by_name[name] = used + 1
        ctx.emit("tool.call", tool=name, args=args, step_id=ctx.state.get("current_step"))

        box: dict[str, Any] = {}

        def runner() -> None:
            try:
                box["result"] = fn(args, ctx)
            except Exception as exc:  # noqa: BLE001 - 工具异常不向执行器抛出
                box["error"] = exc

        started = time.perf_counter()
        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join(timeout=spec.timeout_s)
        latency = int((time.perf_counter() - started) * 1000)

        if thread.is_alive():
            # 线程无法强杀：按约定不再等待，直接返回超时；残留线程随进程回收。
            result = ToolResult(False, error_code=TOOL_TIMEOUT,
                                message=f"工具执行超过 {spec.timeout_s:g}s",
                                latency_ms=latency)
        elif "error" in box:
            exc = box["error"]
            result = ToolResult(False, error_code=TOOL_ERROR,
                                message=f"工具内部异常：{exc.__class__.__name__}",
                                latency_ms=latency)
        else:
            result = box.get("result")
            if not isinstance(result, ToolResult):
                result = ToolResult(False, error_code=TOOL_ERROR,
                                    message="工具返回值不是 ToolResult",
                                    latency_ms=latency)
            else:
                result.latency_ms = latency

        ctx.charge_tokens(getattr(result, "tokens", 0))
        ctx.emit("tool.result", tool=name, ok=result.ok,
                 error_code=result.error_code, latency_ms=result.latency_ms,
                 tokens=result.tokens, step_id=ctx.state.get("current_step"))
        return result
