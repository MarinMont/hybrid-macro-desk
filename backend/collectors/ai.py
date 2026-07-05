"""
ai.py — POST /api/ai-overview
=============================
市況スナップショット (メトリクス) → 日本語のAI概況 { text }。
Anthropic API はバックエンド経由 (キーはフロントに出さない — SPEC §9)。
60sキャッシュ + 手動更新時のみ (API_DESIGN §1)。
キー未設定 / 失敗時は 503 → フロントはルールベース文へフォールバック (API_DESIGN §4)。

プロンプトは参照JSXの文面が正。
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request

import llm

log = logging.getLogger("liqmap.ai")
router = APIRouter()

PROMPT_HEAD = (
    "あなたはマクロトレーディングデスクのアナリストです。以下のBTC無期限先物(Hyperliquid)の"
    "指標スナップショットから、トレーダー向けの日本語の概況を書いてください。形式: 2文で現状の"
    "ナラティブ、続けて1文でリスク・注意点。売買推奨はせず、環境認識に徹すること。"
    "プレーンテキストのみで出力。\n\n"
)

CACHE_TTL = 60.0
_deps: dict = {"http": None}
_cache: dict = {"key": None, "text": None, "ts": 0.0}


def init(http, bucket, store) -> None:
    _deps["http"] = http


async def run():  # ポーリング不要 (呼び出し時生成)
    return None


@router.post("/api/ai-overview")
async def ai_overview(request: Request):
    if not llm.enabled():
        raise HTTPException(503, "ANTHROPIC_API_KEY 未設定 — フロントはルールベース文へ")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}

    key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    now = time.time()
    if _cache["key"] == key and _cache["text"] and now - _cache["ts"] < CACHE_TTL:
        return {"text": _cache["text"], "cached": True}

    try:
        text = await llm.complete(_deps["http"], PROMPT_HEAD + key, max_tokens=1000)
    except Exception as e:  # noqa: BLE001
        log.warning("ai-overview 生成失敗: %s", e)
        raise HTTPException(503, "AI概況の生成に失敗しました")

    if not text:
        raise HTTPException(503, "AI概況が空でした")

    _cache.update({"key": key, "text": text, "ts": now})
    return {"text": text, "cached": False}
