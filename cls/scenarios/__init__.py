"""场景注册入口（公开仓只包含 KBQA 演示场景）。"""
from .base import Scenario
from .kbqa.manifest import KBQA_SCENARIO

SCENARIOS = {"kbqa": KBQA_SCENARIO}

__all__ = ["Scenario", "SCENARIOS", "KBQA_SCENARIO"]
