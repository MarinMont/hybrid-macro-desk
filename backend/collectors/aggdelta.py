"""
aggdelta.py — GET /api/agg-delta
================================
Binance aggTrades (集約約定) から**実際のテイカー買い/売りデルタ**を積み上げる。
`/api/derivs` の takerlongshortRatio 近似より精緻な、約定単位のデルタ/CVD。

ソース: Binance USDⓈ-M Futures 公開API
  GET /fapi/v1/aggTrades?symbol=BTCUSDT&limit=1000  (キー不要)
    各約定: a=aggId, p=price, q=qty, T=time, m=isBuyerMaker

仕組み:
  - AGG_POLL_SEC ごとにポーリングし、前回より新しい aggId の約定だけを対象に
    calc.aggtrade_delta() でデルタを算出 (二重計上を防ぐ)。
  - 区間デルタを固定長リング (deque) に積み、累積で CVD を持つ。メモリは有界。

レスポンス:
  { symbol, updated, stale, lastPrice, lastAggId,
    interval{buyUsd,sellUsd,deltaUsd,count}, cvdUsd, series[{t,deltaUsd,cvdUsd}] }
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque

from fastapi import APIRouter, HTTPException

import calc

log = logging.getLogger("liqmap.aggdelta")
router = APIRouter()

FAPI = "https://fapi.binance.com"
SYMBOL = os.getenv("LIQMAP_COIN", "BTC").upper() + "USDT"
POLL_SEC = int(os.getenv("AGG_DELTA_POLL_SEC", "12"))
SERIES_MAX = int(os.getenv("AGG_DELTA_SERIES_MAX", "360"))  # 12s×360 ≈ 直近72分

_deps: dict = {"http": None}
_state: dict = {
    "last_agg_id": None,
    "last_price": None,
    "cvd_usd": 0.0,
    "interval": None,
    "series": deque(maxlen=SERIES_MAX),
    "updated": 0.0,
}


def init(http, bucket, store) -> None:
    _deps["http"] = http


async def _poll_once():
    http = _deps["http"]
    r = await http.get(f"{FAPI}/fapi/v1/aggTrades", params={"symbol": SYMBOL, "limit": 1000})
    r.raise_for_status()
    trades = r.json()
    if not trades:
        return

    last_id = _state["last_agg_id"]
    if last_id is None:
        # 初回はベースラインを設定するだけ (過去1000件を1区間として計上しない)
        _state["last_agg_id"] = trades[-1]["a"]
        _state["last_price"] = float(trades[-1]["p"])
        _state["updated"] = time.time()
        return

    fresh = [t for t in trades if t["a"] > last_id]
    _state["last_agg_id"] = trades[-1]["a"]
    _state["last_price"] = float(trades[-1]["p"])
    _state["updated"] = time.time()
    if not fresh:
        return

    d = calc.aggtrade_delta(fresh)
    _state["cvd_usd"] += d["deltaUsd"]
    _state["interval"] = {
        "buyUsd": round(d["buyUsd"]),
        "sellUsd": round(d["sellUsd"]),
        "deltaUsd": round(d["deltaUsd"]),
        "count": d["count"],
    }
    _state["series"].append({
        "t": int(_state["updated"] * 1000),
        "deltaUsd": round(d["deltaUsd"]),
        "cvdUsd": round(_state["cvd_usd"]),
    })


async def _loop():
    import asyncio

    while True:
        try:
            await _poll_once()
        except Exception as e:  # noqa: BLE001
            log.warning("aggdelta poll: %s", e)
        await asyncio.sleep(POLL_SEC)


async def run():
    import asyncio

    asyncio.create_task(_loop())


@router.get("/api/agg-delta")
async def agg_delta():
    if _state["last_agg_id"] is None:
        raise HTTPException(503, "agg-delta データ準備中 — Binance aggTrades 取得待ち")
    stale = (time.time() - _state["updated"]) > max(60, POLL_SEC * 4)
    return {
        "symbol": SYMBOL,
        "updated": int(_state["updated"] * 1000),
        "stale": stale,
        "lastPrice": _state["last_price"],
        "lastAggId": _state["last_agg_id"],
        "interval": _state["interval"],
        "cvdUsd": round(_state["cvd_usd"]),
        "series": list(_state["series"]),
    }
