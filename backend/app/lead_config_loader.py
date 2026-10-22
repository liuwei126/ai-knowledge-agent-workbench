"""lead_config.json 加载与校验。

配置单独一个模块而不是散在编排代码里，对应一个明确的客户承诺：
换一个行业客户，只改 JSON（画像/权重/阈值），不改代码、不改表结构。
启动时校验一次，配置写错就快速失败，而不是跑到一半出诡异分数。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_FILE = Path(__file__).resolve().parent / "lead_config.json"

_REQUIRED_KEYS = {"icp", "weights", "thresholds", "pipeline"}


def load_lead_config() -> dict[str, Any]:
    """读取并校验线索评分配置；缺失关键字段直接抛异常，快速失败。"""
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    missing = _REQUIRED_KEYS - data.keys()
    if missing:
        raise ValueError(f"lead_config.json 缺少必要字段：{sorted(missing)}")

    weights = data["weights"]
    if any(w < 0 for w in weights.values()):
        raise ValueError("权重不能为负数")

    thresholds = data["thresholds"]
    if not thresholds["high"] > thresholds["mid"] >= 0:
        raise ValueError("阈值必须满足 high > mid >= 0")

    total = sum(weights.values())
    if total != 100:
        raise ValueError(f"各维度权重之和应为 100，当前为 {total}")
    return data
