"""轻量评测框架：固定数据集、重复执行、结果落盘并记录环境指纹。"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol


class Check(Protocol):
    def check(self, case: "EvalCase", output: dict) -> tuple[bool, str]: ...


@dataclass
class EvalCase:
    id: str
    scenario: str
    inputs: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)
    checks: tuple = ()


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _mem_gb() -> str:
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                             text=True, timeout=5)
        return f"{int(out.stdout.strip()) / 1024 ** 3:.1f} GB"
    except Exception:  # noqa: BLE001
        try:
            import os
            return f"{os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024 ** 3:.1f} GB"
        except Exception:  # noqa: BLE001
            return "unknown"


class EvalRunner:
    def __init__(self, runner: Callable[[EvalCase], dict], results_dir: str | Path = "results"):
        self.runner = runner
        self.results_dir = Path(results_dir)

    def run(self, suite: list[EvalCase], repeat: int = 1, tag: str = "") -> dict:
        started = time.time()
        records = []
        for case in suite:
            for i in range(max(1, repeat)):
                t0 = time.perf_counter()
                error = ""
                output: dict = {}
                try:
                    output = self.runner(case) or {}
                except Exception as exc:  # noqa: BLE001
                    error = exc.__class__.__name__
                checks = []
                for check in case.checks:
                    try:
                        ok, detail = check.check(case, output)
                    except Exception as exc:  # noqa: BLE001
                        ok, detail = False, f"check 异常：{exc.__class__.__name__}"
                    checks.append({"ok": bool(ok), "detail": detail})
                records.append({
                    "case_id": case.id,
                    "scenario": case.scenario,
                    "repeat": i,
                    "error": error,
                    "checks": checks,
                    "passed": not error and all(c["ok"] for c in checks),
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                })

        dataset = json.dumps([asdict(c) for c in suite], ensure_ascii=False,
                             sort_keys=True, default=str)
        payload = {
            "meta": {
                "git_commit": _git_commit(),
                "os": platform.platform(),
                "cpu": platform.processor() or platform.machine(),
                "memory": _mem_gb(),
                "model": {"name": "rule-fallback", "temperature": 0.0, "max_tokens": 800},
                "dataset_sha256": hashlib.sha256(dataset.encode("utf-8")).hexdigest(),
                "command": " ".join(sys.argv),
                "tag": tag,
                "duration_s": round(time.time() - started, 2),
                "repeat": repeat,
                "total": len(records),
                "passed": sum(1 for r in records if r["passed"]),
            },
            "cases": records,
        }
        self.results_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = self.results_dir / f"run-{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["path"] = str(path)
        return payload
