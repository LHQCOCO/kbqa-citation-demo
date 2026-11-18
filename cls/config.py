"""Contract LocalScan 本地配置。"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

# 配置读-改-写需要串行：多个请求（模型切换、阈值保存、授权变更）同时写会丢更新
_CONFIG_LOCK = threading.Lock()

if FROZEN:
    # 打包模式：法规库/模板/静态文件为包内只读资源；
    # 审阅记录与配置写入用户目录，避免写入只读的 App/EXE 内部。
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    if sys.platform == "darwin":
        DATA_DIR = Path.home() / "Library" / "Application Support" / "ContractLocalScan"
    elif os.name == "nt":
        DATA_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "ContractLocalScan"
    else:
        DATA_DIR = Path.home() / ".local" / "share" / "ContractLocalScan"
else:
    RESOURCE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = RESOURCE_DIR / "data"

# 多实例隔离：CLS_DATA_DIR 可把配置、历史记录、授权文件指向独立目录，
# 便于在同一台机器上并行跑开发实例与打包实例（或两套测试环境）互不污染。
_data_override = os.environ.get("CLS_DATA_DIR", "").strip()
if _data_override:
    DATA_DIR = Path(_data_override).expanduser().resolve()

BASE_DIR = RESOURCE_DIR
SAMPLES_DIR = RESOURCE_DIR / "samples"
EXPORTS_DIR = DATA_DIR / "exports"
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "history.db"

DEFAULT_CONFIG = {
    "ollama_url": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
    "model": os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"),
    # 默认不启用：首次启动必须先经用户明确授权（deploy_consent=True）才允许调用本机模型
    "use_llm": False,
    "llm_timeout": 180,
    "num_ctx": 8192,
    # 导出方式：direct = 后端直接写入 export_dir（不弹浏览器下载对话框）
    #          browser = 走浏览器标准下载流程（是否弹"存储为"取决于浏览器设置）
    "export_mode": "direct",
    "export_dir": str(Path.home() / "Desktop" / "ContractLocalScan-导出"),
    # 首次启动的本地部署授权：None=尚未询问，True=已同意，False=已拒绝（仅规则模式）
    "deploy_consent": None,
    "consent_at": "",
    # —— 规则参数化阈值：所有百分比单位为 %，时长单位为月/年/天 ——
    "rule_params": {
        "penalty_high_pct": 30,       # 违约金过高判定阈值（合同总价 %）
        "penalty_low_pct_perday": 0.03,  # 违约金过低判定阈值（日利率 %）
        "deposit_max_pct": 20,        # 定金法定上限（合同总价 %）
        "probation_salary_pct": 80,   # 试用期工资下限（转正工资 %）
        "compete_max_months": 24,     # 竞业限制法定上限（月）
        "lease_max_years": 20,        # 租赁期限法定上限（年）
        "lpr_multiplier": 4,          # 民间借贷利率上限（LPR 倍数）
        "probation_max_months": 6,    # 试用期最长上限（月）
        "penalty_recommend_pct": 25,  # 违约金建议合理上限（合同总价 %）
    },
    # 行业规则开关：关闭对应行业规则后，该类规则不参与扫描
    "industry_rules": {
        "tech_dev": True,       # 技术开发合同
        "purchase": True,       # 采购/买卖合同
        "transport": True,      # 运输合同
        "real_estate": True,    # 房地产/土地开发
        "equity": True,         # 股权/投资/合伙
        "guarantee": True,      # 担保/金融
        "consumer": True,       # 消费/生活服务
        "business": True,       # 经营/商业服务
    },
    # 企业自定义包接管的阈值来源标记：{参数名: {value, pack_id, pack_name}}
    # 由 cls/custom.py 的 recompute_params 统一维护，界面只读展示
    "threshold_overrides": {},
    # Agent 运行时：默认关闭，缺省值不参与启动流程
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


def memory_gb() -> float:
    """本机物理内存容量（GB）。取不到时返回 0，调用方按「未知」处理。"""
    try:
        if os.name == "nt":
            import ctypes
            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return round(st.ullTotalPhys / 1024 ** 3, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 1)
    except Exception:
        return 0.0


# 实测结论：gemma3:4b 在 8 GB 机器上可运行（单份约 85 秒，比 16 GB 慢），
# 因此门槛仍为 8 GB，但推荐档位单独标注为 16 GB；qwen2.5:3b 适合 8 GB 及以下。
MODEL_MEMORY_OVERRIDE = {
    "gemma3:4b": 8,
}

# 推荐档位（GB）：只用于界面标注，不参与可选性判定
MODEL_MEMORY_RECOMMEND = {
    "gemma3:4b": 16,
    "qwen2.5:3b": 8,
}


def model_memory_need(model: str) -> int:
    """按参数量估算可用该模型所需的最低物理内存（GB）。

    依据实测：8 GB 机器上 7B 模型需把大部分计算回退到 CPU，
    单份合同 15 分钟内无法完成，且会因内存交换拖垮整台机器，因此不允许选择。
    """
    key = (model or "").strip().lower()
    if key.endswith(":latest"):
        key = key[: -len(":latest")]
    for name, gb in MODEL_MEMORY_OVERRIDE.items():
        # 允许省略 tag（如 gemma3 默认等价 gemma3:4b）
        if key == name or (":" not in key and name.startswith(key + ":")):
            return gb
    m = re.search(r"(\d+(?:\.\d+)?)\s*b(?![a-z])", (model or "").lower())
    if not m:
        return 0
    params = float(m.group(1))
    if params < 5:
        return 8
    if params < 9:
        return 16
    if params < 15:
        return 24
    if params < 35:
        return 48
    return 64


def model_memory_recommend(model: str) -> int:
    """该模型的推荐内存档位（GB）。仅用于界面标注，门槛仍按 model_memory_need。"""
    key = (model or "").strip().lower()
    if key.endswith(":latest"):
        key = key[: -len(":latest")]
    for name, gb in MODEL_MEMORY_RECOMMEND.items():
        if key == name or (":" not in key and name.startswith(key + ":")):
            return gb
    return model_memory_need(model)


def model_supported(model: str) -> tuple[bool, str]:
    """判断当前机器能否使用该模型，返回 (是否可用, 不可用原因)。"""
    need = model_memory_need(model)
    have = memory_gb()
    if not need or not have or have >= need:
        return True, ""
    return False, f"该模型需要约 {need} GB 运行内存，本机为 {have:g} GB，强行启用会导致长时间无响应"


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            # 嵌套字典做深合并：旧配置文件缺少后增的默认项时，以默认配置补齐
            nested = {k: saved.pop(k) for k in ("rule_params", "industry_rules", "threshold_overrides", "agent") if k in saved}
            cfg.update(saved)
            for k, v in nested.items():
                if isinstance(v, dict):
                    merged = dict(DEFAULT_CONFIG.get(k, {}))
                    merged.update(v)
                    if k == "agent" and isinstance(v.get("budget"), dict):
                        # 预算允许只覆盖部分字段，其余沿用默认值
                        budget = dict(DEFAULT_CONFIG["agent"]["budget"])
                        budget.update(v["budget"])
                        merged["budget"] = budget
                    cfg[k] = merged
        except Exception:
            # 配置损坏时备份原始文件再回退默认值，避免下一次保存把用户内容彻底覆盖
            try:
                CONFIG_PATH.replace(CONFIG_PATH.with_name(CONFIG_PATH.name + ".broken"))
            except OSError:
                pass
    return cfg


def save_config(patch: dict) -> dict:
    with _CONFIG_LOCK:
        cfg = load_config()
        for key in ("ollama_url", "model", "use_llm", "llm_timeout", "num_ctx",
                    "export_mode", "export_dir", "deploy_consent", "consent_at"):
            if key in patch:
                cfg[key] = patch[key]
        # 嵌套字典：rule_params / industry_rules 做深合并，避免用户只改一个参数时覆盖全部
        for nested in ("rule_params", "industry_rules"):
            if nested in patch and isinstance(patch[nested], dict):
                cfg.setdefault(nested, {}).update(patch[nested])
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子替换，避免进程中断留下半截 JSON
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
        return cfg


def save_rule_params(params: dict, overrides: dict) -> dict:
    """整体替换 rule_params 与 threshold_overrides（供 custom.recompute_params 调用）。

    与 save_config 的深合并不同，这里必须整体替换：企业包停用/删除后，
    被接管的阈值要彻底回退，不能残留合并进来的旧值。
    """
    with _CONFIG_LOCK:
        cfg = load_config()
        cfg["rule_params"] = dict(params)
        cfg["threshold_overrides"] = dict(overrides)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
        return cfg


for _d in (DATA_DIR, EXPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
