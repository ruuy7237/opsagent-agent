"""评测用的指标函数，各项目的 eval 脚本共用。"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Sequence


def wilson_lower(success: int, n: int, z: float = 1.96) -> float:
    """Wilson 置信区间下界。样本小的时候点估计容易偏乐观，写结果时用这个值。"""
    if n == 0:
        return 0.0
    p = success / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((centre - margin) / denom, 4)


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def dump(path: str | Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


