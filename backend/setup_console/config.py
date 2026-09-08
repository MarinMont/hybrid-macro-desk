"""
config.py — 校正値ファイルの読み込み (SPEC §2.5)
閾値は config/confirm_<version>.json に固定。既存ファイルの上書き禁止。
変更は新ファイル追加 + UI のバージョン表示更新。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(os.getenv("SETUP_CONSOLE_CONFIG_DIR", Path(__file__).resolve().parent.parent / "config"))
DEFAULT_VERSION = os.getenv("SETUP_CONSOLE_CONFIRM_VERSION", "v0.3.4")  # v0.3.3 と同閾値、δ は reconstruct (Pine と同一)

REQUIRED_KEYS = (
    "version", "timeframe", "window_bars", "x_ratio", "atr_coef", "atr_len",
    "vol_floor_mult", "vol_median_len", "accept_consecutive", "delta_method",
)


@dataclass(frozen=True)
class ConfirmConfig:
    version: str
    timeframe: str
    window_bars: int
    x_ratio: float
    atr_coef: float
    atr_len: int
    vol_floor_mult: float
    vol_median_len: int
    accept_consecutive: int
    delta_method: str

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in REQUIRED_KEYS}


def config_path(version: str) -> Path:
    return CONFIG_DIR / f"confirm_{version}.json"


def load_config(version: str = DEFAULT_VERSION) -> ConfirmConfig:
    p = config_path(version)
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    missing = [k for k in REQUIRED_KEYS if k not in d]
    if missing:
        raise ValueError(f"{p.name}: 欠落キー {missing}")
    if d["delta_method"] not in ("taker", "reconstruct"):
        raise ValueError(f"{p.name}: delta_method が不正 ({d['delta_method']})")
    if d["timeframe"] != "15m":
        # 閾値は 15 分足で校正されたセット。他の時間足への流用禁止 (SPEC §5.6)
        raise ValueError(f"{p.name}: timeframe は 15m のみ ({d['timeframe']})")
    return ConfirmConfig(**{k: d[k] for k in REQUIRED_KEYS})


def list_versions() -> list[str]:
    return sorted(p.stem.removeprefix("confirm_") for p in CONFIG_DIR.glob("confirm_*.json"))
