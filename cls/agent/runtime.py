"""AgentRuntime：把场景、计划、执行器与 trace 装配成一次运行。"""
from __future__ import annotations

import uuid
from pathlib import Path

from .. import config
from .context import RunContext, SQLiteMemory
from .executor import Executor
from .planner import Planner
from .registry import ToolRegistry
from .roles import Role
from .trace import Trace
from .types import Budget, RunResult, ToolSpec


def agent_enabled() -> bool:
    try:
        return config.load_config().get("agent", {}).get("enabled") is True
    except Exception:  # noqa: BLE001
        return False


def config_budget() -> Budget:
    raw = config.load_config().get("agent", {}).get("budget", {})
    allowed = {k: raw[k] for k in ("max_steps", "max_tool_calls", "max_wall_s", "max_tokens")
               if k in raw}
    return Budget(**allowed)


class AgentRuntime:
    def __init__(self, scenario, adapter=None, data_dir: str | Path | None = None,
                 max_foreach_items: int = 12, observer=None, repair=None):
        self.scenario = scenario
        self.adapter = adapter
        self.root = Path(data_dir) if data_dir else (config.DATA_DIR / "agent_traces")
        self.max_foreach_items = max_foreach_items
        self.observer = observer
        self.repair = repair

    def run(self, inputs: dict, budget: Budget | None = None,
            run_id: str | None = None, trace_path: str | Path | None = None,
            scopes: tuple[str, ...] = ()) -> RunResult:
        run_id = run_id or uuid.uuid4().hex[:12]
        path = Path(trace_path) if trace_path else (self.root / f"{run_id}.jsonl")
        trace = Trace(path, run_id)
        budget = budget or config_budget()

        ctx = RunContext(
            run_id=run_id,
            trace=trace,
            budget=budget,
            state={"observer": self.observer},
            memory=SQLiteMemory(config.DATA_DIR / "agent_memory.db"),
            scopes=tuple(scopes),
        )
        ctx.emit("run.start", ok=True, mode="agent")

        registry = ToolRegistry()
        self.scenario.register_tools(registry)
        task = self.scenario.build_task(inputs)
        plan: Planner = self.scenario.planner
        prepared = plan.plan(task, registry.specs(ctx.scopes), budget)
        ctx.emit("plan.created", count=len(prepared.steps),
                 version=prepared.version, reason=prepared.reason)

        role_runner = self.scenario.make_role_runner(registry, self.adapter)
        executor = Executor(registry, role_runner=role_runner,
                            max_foreach_items=self.max_foreach_items,
                            repair=self.repair)
        result = executor.run(task, prepared, ctx)

        try:
            summary = self.scenario.render(ctx) if self.scenario.renderer else {}
        except Exception:  # noqa: BLE001 - 渲染失败不影响已完成的产物
            summary = {}
        result.artifacts["summary"] = summary
        return result


__all__ = ["AgentRuntime", "agent_enabled", "config_budget", "RunResult",
           "ToolSpec", "Role"]
