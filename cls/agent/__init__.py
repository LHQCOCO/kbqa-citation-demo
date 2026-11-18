"""Agent 运行时内核：不依赖任何具体场景。"""
from .types import (
    Budget, Plan, RunResult, Step, ToolResult, ToolSpec,
)
from .registry import ToolRegistry
from .trace import Trace
from .context import RunContext, MemoryStore, SQLiteMemory
from .evidence import EvidenceChecker, CheckResult
from .planner import Planner, ChecklistPlanner, LLMPlanner
from .executor import Executor
from .roles import Role, Pipeline, ReviewLoop, call_tool_for_role
from .adapter import ModelAdapter, ModelReply, ModelUnavailable, OllamaAdapter
from .eval import EvalCase, Check, EvalRunner
from .runtime import AgentRuntime, agent_enabled, config_budget

__all__ = [
    "Budget", "Plan", "RunResult", "Step", "ToolResult", "ToolSpec",
    "ToolRegistry", "Trace", "RunContext", "MemoryStore", "SQLiteMemory",
    "EvidenceChecker", "CheckResult", "Planner", "ChecklistPlanner", "LLMPlanner",
    "Executor", "Role", "Pipeline", "ReviewLoop", "call_tool_for_role",
    "ModelAdapter", "ModelReply", "ModelUnavailable", "OllamaAdapter",
    "EvalCase", "Check", "EvalRunner", "AgentRuntime", "agent_enabled", "config_budget",
]
