"""计划器：场景提供检查清单模板；LLM 规划器仅留接口。"""
from __future__ import annotations

from typing import Callable, Protocol

from .types import Budget, Plan, Step, ToolSpec


class Planner(Protocol):
    def plan(self, task: dict, tools: list[ToolSpec], budget: Budget) -> Plan: ...

    def replan(self, plan: Plan, observation: dict) -> Plan: ...


class ChecklistPlanner:
    """确定性检查清单：由场景提供 ``plan_template(task) -> list[Step]``。"""

    def __init__(self, template: Callable[[dict], list[Step]]):
        self._template = template

    def plan(self, task: dict, tools: list[ToolSpec], budget: Budget) -> Plan:
        steps = list(self._template(task) or [])
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("计划中存在重复的 step id")
        return Plan(steps=steps, version=1, reason="checklist")

    def replan(self, plan: Plan, observation: dict) -> Plan:
        # 检查清单模式不因单步失败改写计划，失败由执行器记录后继续。
        return plan


class LLMPlanner:
    """预留：由模型生成步骤序列。

    约定实现：读取 tools 的 name/description/params 生成 JSON 计划，再转换为
    ``Step`` 列表，并通过 ``replan`` 根据 observation 调整。本次不接入模型，
    以保证默认路径完全确定、可测试。
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("LLMPlanner 为后续场景预留，本版本未实现")

    def plan(self, task: dict, tools: list[ToolSpec], budget: Budget) -> Plan:  # pragma: no cover
        raise NotImplementedError

    def replan(self, plan: Plan, observation: dict) -> Plan:  # pragma: no cover
        raise NotImplementedError
