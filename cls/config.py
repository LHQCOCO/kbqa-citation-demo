"""演示仓的本地路径与 Agent 默认预算。"""
from __future__ import annotations

import os
from pathlib import Path

RESOURCE_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = RESOURCE_DIR
SAMPLES_DIR = RESOURCE_DIR / "samples"

_data_override = os.environ.get("KBQA_DATA_DIR", "").strip()
DATA_DIR = (Path(_data_override).expanduser().resolve()
            if _data_override else RESOURCE_DIR / "data")
EXPORTS_DIR = DATA_DIR / "exports"
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "history.db"

DEFAULT_CONFIG = {
    "agent": {
        "enabled": False,
        "model": "",
        "budget": {
            "max_steps": 20,
            "max_tool_calls": 60,
            "max_wall_s": 180,
            "max_tokens": 20000,
        },
    },
}


def load_config() -> dict:
    """返回默认配置；本演示不读写用户配置文件。"""
    return {
        "agent": {
            **DEFAULT_CONFIG["agent"],
            "budget": dict(DEFAULT_CONFIG["agent"]["budget"]),
        },
    }


for _d in (DATA_DIR, EXPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
