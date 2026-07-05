"""
derivs.py — GET /api/derivs
===========================
Binance Futures 公開API (キー不要) から Positioning 系を集約。
レスポンス骨子 (API_DESIGN §1):
  { lsGlobalLongPct, lsTopLongPct, takerSeries[24], cvd[24], oiHist[48], oiChangePct24h }

ソース (各 60s ポーリング / 60s サーバーキャッシュ):
  - /futures/data/globalLongShortAccountRatio (period=1h, limit=1)  → 個人口座 L%
  - /futures/data/topLongShortPositionRatio    (period=1h, limit=1)  → トップトレーダー L%
  - /futures/data/takerlongshortRatio          (period=1h, limit=24) → テイカーデルタ → CVD
  - /futures/data/openInterestHist             (period=1h, limit=48) → OI推移と24h変化率

障害時: 直近キャッシュを stale=true で返す。キャッシュも無ければ 503 (API_DESIGN §4)。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

import calc

log = logging.getLogger("liqmap.derivs")
router = APIRouter()

FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"

_deps: dict = {"http": None, "store": None}
_cache: dict | None = None
_cache_ts: float = 0.0


def init(http, bucket, store) -> None:  # bucket 不使用 (Binanceは重み制対象外)
    _deps["http"], _deps["store"] = http, store


async def _fetch_once() -> dict:
    http = _deps["http"]
    store = _deps["store"]
    price = getattr(store, "mark_px", None) or 0.0

    async def gd(path, limit):
        r = await http.get(f"{FAPI}{path}", params={"symbol": SYMBOL, "period": "1h", "limit": limit})
        r.raise_for_status()
        return r.json()

    ls_global = await gd("/futures/data/globalLongShortAccountRatio", 1)
    ls_top = await gd("/futures/data/topLongShortPositionRatio", 1)
    taker_raw = await gd("/futures/data/takerlongshortRatio", 24)
    oi_raw = await gd("/futures/data/openInterestHist", 48)

    ls_global_long = round(float(ls_global[-1]["longAccount"]) * 100, 1) if ls_global else None
    ls_top_long = round(float(ls_top[-1]["longAccount"]) * 100, 1) if ls_top else None

    taker_series = [
        round(calc.taker_delta_usd(float(t["buyVol"]), float(t["sellVol"]), price))
        for t in taker_raw
    ]
    cvd_series = [round(v) for v in calc.cvd(taker_series)]

    oi_hist = [round(float(o["sumOpenInterestValue"])) for o in oi_raw]
    oi_chg = calc.oi_change_pct_24h(oi_hist)

    return {
        "updated": int(time.time() * 1000),
        "stale": False,
        "lsGlobalLongPct": ls_global_long,
        "lsTopLongPct": ls_top_long,
        "takerSeries": taker_series,
        "cvd": cvd_series,
        "oiHist": oi_hist,
        "oiChangePct24h": round(oi_chg, 2) if oi_chg is not None else None,
    }


async def _loop():
    import asyncio

    global _cache, _cache_ts
    while True:
        try:
            _cache = await _fetch_once()
            _cache_ts = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("derivs loop: %s — キャッシュ継続", e)
        await asyncio.sleep(60)


async def run():
    import asyncio

    asyncio.create_task(_loop())


@router.get("/api/derivs")
async def derivs():
    if _cache is None:
        raise HTTPException(503, "derivs データ準備中 — Binance 取得待ち")
    stale = (time.time() - _cache_ts) > 90
    return {**_cache, "stale": stale}
