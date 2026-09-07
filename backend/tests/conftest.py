"""
conftest.py — セットアップコンソールのテスト共通フィクスチャ
記録済み実データ (tests/fixtures/calibration/<scene>.json) は Mac で
`python -m setup_console.record_calibration` を実行して生成する。無ければ依存テストは skip。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CALIB_DIR = Path(__file__).resolve().parent / "fixtures" / "calibration"


def load_recorded(scene_id: str) -> dict | None:
    p = CALIB_DIR / f"{scene_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def require_recorded(scene_id: str) -> dict:
    doc = load_recorded(scene_id)
    if doc is None:
        pytest.skip(f"記録済み実データ {scene_id}.json 無し — Mac で python -m setup_console.record_calibration を実行")
    return doc


@pytest.fixture
def recorded():
    return require_recorded
