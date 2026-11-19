"""执行器：依赖、预算、重试、foreach 展开与失败隔离。"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from .context import RunContext
from .registry import ToolRegistry
from .types import (
    BUDGET_EXCEEDED, INVALID_ARGS, ROLE_ERROR, SKIPPED_DEP, TOOL_BUDGET_EXCEEDED,
    TOOL_NOT_FOUND, TRUNCATED, UNAUTHORIZED_TOOL, Budget, Plan, RunResult, Step,
    ToolResult,
)

_NON_RETRYABLE = {INVALID_ARGS, UNAUTHORIZED_TOOL, TOOL_NOT_FOUND, TOOL_BUDGET_EXCEEDED}
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_FOREACH = re.compile(r"^([\w.\[\]]+?)(?:\[([\w.]+)=([^\]]+)\])?$")

Repair = Callable[[Step, ToolResult, int, RunContext], dict | None]

PLAN_INVALID = "PLAN_INVALID"


def _validate_plan(plan: Plan) -> str:
    """计划结构自检：重复 id、悬空依赖、foreach 指向自身。"""
    ids = [step.id for step in plan.steps]
    if len(ids) != len(set(ids)):
        return "计划中存在重复的 step id"
    known = set(ids)
    for step in plan.steps:
        for dep in step.depends_on:
            if dep not in known:
                return f"step {step.id} 依赖了计划外的 {dep}"
        if step.foreach and step.foreach.split(".")[0].split("[")[0] == step.id:
            return f"step {step.id} 的 foreach 指向自身"
    return ""


def _walk(path: str, roots: dict) -> Any:
    parts = [p for p in path.split(".") if p]
    if not parts:
        return None
    node: Any = roots.get(parts[0])
    for part in parts[1:]:
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit():
            node = node[int(part)] if int(part) < len(node) else None
        else:
            return None
    return node


def _render(value: Any, roots: dict) -> Any:
    if isinstance(value, dict):
        return {k: _render(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, roots) for v in value]
    if not isinstance(value, str):
        return value
    whole = _PLACEHOLDER.fullmatch(value)
    if whole:
        return _walk(whole.group(1), roots)
    return _PLACEHOLDER.sub(
        lambda m: "" if _walk(m.group(1), roots) is None else str(_walk(m.group(1), roots)),
        value,
    )


class Executor:
    def __init__(self, registry: ToolRegistry, role_runner=None,
                 max_foreach_items: int = 12, repair: Repair | None = None):
        self.registry = registry
        self.role_runner = role_runner
        self.max_foreach_items = max(1, int(max_foreach_items))
        self.repair = repair

    # ---------------------------------------------------------------- 预算
    @staticmethod
    def _budget_hit(ctx: RunContext, started: float, count_steps: bool = True) -> str:
        # max_steps 约束计划步骤数（foreach 展开的子步骤不再重复计数，
        # 展开产生的实际工作量由 max_tool_calls 兜住），否则一份文档几十条
        # 命中就会在核查之前耗尽步数。
        if count_steps and int(ctx.state.get("steps", 0)) >= max(1, ctx.budget.max_steps):
            return "max_steps"
        if int(ctx.state.get("tool_calls", 0)) >= max(1, ctx.budget.max_tool_calls):
            return "max_tool_calls"
        if time.perf_counter() - started >= ctx.budget.max_wall_s:
            return "max_wall_s"
        if int(ctx.state.get("tokens", 0)) >= ctx.budget.max_tokens:
            return "max_tokens"
        return ""

    # ---------------------------------------------------------------- foreach
    def _expand(self, step: Step, ctx: RunContext) -> tuple[list, bool]:
        spec = (step.foreach or "").strip()
        match = _FOREACH.match(spec)
        if not match:
            return [None], False
        path, field, raw_values = match.groups()
        node = _walk(path, {"task": ctx.state.get("task"), **ctx.state.get("results", {})})
        if not isinstance(node, list):
            return [], False
        if field:
            wanted = {v.strip() for v in raw_values.split("|") if v.strip()}
            node = [x for x in node if isinstance(x, dict) and str(x.get(field)) in wanted]
        truncated = len(node) > self.max_foreach_items
        return node[:self.max_foreach_items], truncated

    def _roots(self, task: dict, ctx: RunContext, item: Any) -> dict:
        return {"task": task, "item": item, **ctx.state.get("results", {})}

    # ---------------------------------------------------------------- 单步
    def _exec_once(self, step: Step, args: dict, ctx: RunContext) -> ToolResult:
        ctx.state["current_step"] = step.id
        if step.role:
            if self.role_runner is None:
                return ToolResult(False, error_code=ROLE_ERROR, message="未配置角色执行器")
            try:
                data = self.role_runner.run(step.role, dict(ctx.state.get("results", {})), ctx)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(False, error_code=ROLE_ERROR,
                                  message=f"角色执行异常：{exc.__class__.__name__}")
            if isinstance(data, dict) and data.get("_error"):
                return ToolResult(False, error_code=str(data["_error"]),
                                  message="角色未能产出结构化结果")
            ctx.state["results"][step.id] = data
            return ToolResult(True, data=data)
        return self.registry.call(step.tool or "", args, ctx)

    def _run_step(self, step: Step, args: dict, ctx: RunContext, sid: str) -> ToolResult:
        result = ToolResult(False, error_code=ROLE_ERROR, message="未执行")
        for attempt in range(1, max(1, step.max_attempts) + 1):
            result = self._exec_once(step, args, ctx)
            if result.ok:
                break
            if result.error_code in _NON_RETRYABLE:
                break
            if attempt < step.max_attempts and self.repair is not None:
                try:
                    fixed = self.repair(step, result, attempt, ctx)
                except Exception:  # noqa: BLE001
                    fixed = None
                if isinstance(fixed, dict):
                    args = fixed
            ctx.emit("step.end", step_id=sid, ok=False,
                     error_code=result.error_code, attempt=attempt, phase="retry")
        return result

    # ---------------------------------------------------------------- 主流程
    def run(self, task: dict, plan: Plan, ctx: RunContext) -> RunResult:
        started = time.perf_counter()
        problem = _validate_plan(plan)
        if problem:
            ctx.emit("run.end", ok=False, status="failed",
                     stop_reason=PLAN_INVALID, steps=0, tool_calls=0, tokens=0,
                     latency_ms=int((time.perf_counter() - started) * 1000))
            return RunResult(
                run_id=ctx.run_id, status="failed", stop_reason=PLAN_INVALID,
                artifacts={}, metrics={"steps": 0, "tool_calls": 0, "tokens": 0,
                                       "wall_ms": 0, "failures": 0},
                trace_path=str(ctx.trace.path),
            )
        ctx.state.setdefault("results", {})
        ctx.state.setdefault("finished", set())
        ctx.state.setdefault("failed", set())
        ctx.state.setdefault("steps", 0)
        ctx.state.setdefault("tool_calls", 0)
        ctx.state.setdefault("tokens", 0)
        ctx.state["task"] = task

        artifacts: dict[str, Any] = {}
        failures: list[dict] = []
        stop_reason = ""
        stopped = False

        for step in plan.steps:
            reason = self._budget_hit(ctx, started)
            if reason:
                stop_reason, stopped = BUDGET_EXCEEDED, True
                break

            if any(dep not in ctx.state["finished"] for dep in step.depends_on):
                ctx.emit("step.end", step_id=step.id, ok=False,
                         error_code=SKIPPED_DEP, phase="skip")
                failures.append({"step_id": step.id, "error_code": SKIPPED_DEP})
                ctx.state["failed"].add(step.id)
                continue

            items, truncated = self._expand(step, ctx)
            if truncated:
                ctx.emit("step.end", step_id=step.id, ok=True, code=TRUNCATED,
                         truncated=True, count=len(items), phase="foreach")
            if not items:
                ctx.state["finished"].add(step.id)
                ctx.emit("step.start", step_id=step.id, role=step.role or "",
                         tool=step.tool or "")
                ctx.emit("step.end", step_id=step.id, ok=True, count=0)
                continue

            ok_all = True
            ctx.state["steps"] = int(ctx.state["steps"]) + 1
            for idx, item in enumerate(items):
                sid = step.id if len(items) == 1 and step.foreach is None else f"{step.id}[{idx}]"
                # 同一计划的 foreach 子步骤共享一次步数配额；这里只检查调用数、
                # 墙钟与 token，避免「一个步骤展开 12 条」被算成 12 步。
                reason = self._budget_hit(ctx, started, count_steps=False)
                if reason:
                    stop_reason, stopped = BUDGET_EXCEEDED, True
                    break
                ctx.emit("step.start", step_id=sid, role=step.role or "",
                         tool=step.tool or "")
                args = _render(step.args, self._roots(task, ctx, item))
                result = self._run_step(step, args, ctx, sid)
                ctx.emit("step.end", step_id=sid, ok=result.ok,
                         error_code=result.error_code,
                         latency_ms=result.latency_ms)
                if result.ok:
                    artifacts[sid] = result.data
                    ctx.state["results"][sid] = result.data
                else:
                    ok_all = False
                    failures.append({"step_id": sid, "error_code": result.error_code})
                    ctx.state["failed"].add(sid)
            if stopped:
                break
            if ok_all:
                ctx.state["finished"].add(step.id)
            else:
                # 失败步骤不进入 finished，依赖它的步骤按「依赖未满足」跳过
                ctx.state["failed"].add(step.id)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if stopped:
            # 预算提前停止：已有产物按部分成功返回，没有产物才算失败
            status = "partial" if artifacts else "failed"
            stop_reason = BUDGET_EXCEEDED
        elif not artifacts:
            status, stop_reason = "failed", "COMPLETED"
        elif failures:
            status, stop_reason = "partial", "COMPLETED"
        else:
            status, stop_reason = "ok", "COMPLETED"

        metrics = {
            "steps": int(ctx.state.get("steps", 0)),
            "tool_calls": int(ctx.state.get("tool_calls", 0)),
            "tokens": int(ctx.state.get("tokens", 0)),
            "wall_ms": elapsed_ms,
            "failures": len(failures),
        }
        ctx.emit("run.end", ok=status == "ok", status=status,
                 stop_reason=stop_reason, steps=metrics["steps"],
                 tool_calls=metrics["tool_calls"], tokens=metrics["tokens"],
                 latency_ms=elapsed_ms)
        return RunResult(
            run_id=ctx.run_id,
            status=status,
            stop_reason=stop_reason,
            artifacts=artifacts,
            metrics=metrics,
            trace_path=str(ctx.trace.path),
        )
