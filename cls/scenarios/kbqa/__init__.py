"""私有知识库问答场景插件：复用 cls/agent 的 Executor、ToolRegistry、Trace 与 EvidenceChecker。"""
from .manifest import KBQA_SCENARIO, build_scenario, render

__all__ = ["KBQA_SCENARIO", "build_scenario", "render"]
