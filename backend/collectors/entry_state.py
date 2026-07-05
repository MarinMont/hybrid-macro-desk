"""
entry_state.py — GET /api/entry-state
======================================
既存 Entry Console が出力する entry_state.json を監視して配信 (疎結合)。
スキーマは SPEC §7 の正 (state/direction/since/pillars{structure,cvd,oi}/next_condition/
regime/regime_confidence/regime_note)。**判定ロジックは移植・改変しない。中身をそのまま返す。**

ファイル不在時は 404 → フロントはサンプル+黄タグにフォールバック (API_DESIGN §4)。
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, HTTPException

log = logging.getLogger("liqmap.entry")
router = APIRouter()

_deps: dict = {}


def init(http, bucket, store) -> None:
    return None


async def run():  # ファイルはリクエスト時に読む (常駐ループ不要)
    return None


def _path() -> str:
    return os.getenv("ENTRY_STATE_PATH", "entry_state.json")


@router.get("/api/entry-state")
async def entry_state():
    path = _path()
    if not os.path.exists(path):
        raise HTTPException(404, f"entry_state.json 不在 ({path}) — Entry Console 未接続")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(503, f"entry_state.json の読み込みに失敗: {e}")
    return data
