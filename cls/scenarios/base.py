"""场景契约：新增场景复用同一结构即可接入。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..agent.planner import Planner
from ..agent.registry import ToolRegistry
from ..agent.roles import Role
from ..agent.types import ToolSpec


@dataclass
class Scenario:
    name: str
    title: str
    version: str = "1"
    tools: tuple[tuple[ToolSpec, Callable], ...] = ()
    roles: dict[str, Role] = field(default_factory=dict)
    planner: Planner | None = None
    task_builder: Callable[[dict], dict] | None = None
    renderer: Callable[[Any], dict] | None = None
    role_runner_factory: Callable[[ToolRegistry, Any], Any] | None = None

    def register_tools(self, registry: ToolRegistry) -> None:
        for spec, fn in self.tools:
            registry.register(spec, fn)

    def build_task(self, inputs: dict) -> dict:
        if self.task_builder is None:
            return dict(inputs)
        return self.task_builder(inputs)

    def make_role_runner(self, registry: ToolRegistry, adapter=None):
        if self.role_runner_factory is None:
            return None
        return self.role_runner_factory(registry, adapter)

    def render(self, ctx) -> dict:
        if self.renderer is None:
            return {}
        return self.renderer(ctx)
