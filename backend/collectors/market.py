"""
market.py — GET /api/market
===========================
HL集約 + テクニカル入力。レスポンス骨子 (API_DESIGN §1):
  { price, chg24, candles[320], fundingApr, oiUsd, vol24Usd, fundingCompare[{ex,apr}], fng{value,label} }

ソース:
  - candleSnapshot (BTC, 1h, 320本)   : 60s   (weight 20)
  - metaAndAssetCtxs                   : liqmap の market_loop が 20s で store.asset_ctx に格納済み → 再利用
  - predictedFundings                  : 5min  (weight 20) → HL/Binance/Bybit の APR
  - Alternative.me /fng/?limit=1       : 5min

price/chg24/fundingApr/oiUsd/vol24Usd は asset_ctx から算出 (deriveMetrics と同一定義)。
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException

log = logging.getLogger("liqmap.market")
router = APIRouter()

INFO_URL = "https://api.hyperliquid.xyz/info"
FNG_URL = "https://api.alternative.me/fng/?limit=1"

# HL predictedFundings のベンダー名 → 表示名
VENUE_NAMES = {"HlPerp": "Hyperliquid", "BinPerp": "Binance", "BybitPerp": "Bybit"}
VENUE_ORDER = ["Hyperliquid", "Binance", "Bybit"]

_deps: dict = {"http": None, "bucket": None, "store": None}
_cache: dict = {"candles": [], "fundingCompare": [], "fng": None, "candlesTs": 0.0}


def init(http, bucket, store) -> None:
    _deps["http"], _deps["bucket"], _deps["store"] = http, bucket, store


# ---------------- ポーリング ----------------
async def _candle_loop():
    import asyncio

    http, bucket = _deps["http"], _deps["bucket"]
    while True:
        try:
            end = int(time.time() * 1000)
            start = end - 320 * 3600 * 1000
            await bucket.take(20)
            r = await http.post(
                INFO_URL,
                json={"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": start, "endTime": end}},
            )
            raw = r.json()
            _cache["candles"] = [
                {"t": c["t"], "o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])}
                for c in raw
            ]
            _cache["candlesTs"] = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("candle_loop: %s", e)
        await asyncio.sleep(60)


async def _funding_loop():
    import asyncio

    http, bucket = _deps["http"], _deps["bucket"]
    while True:
        try:
            await bucket.take(20)
            r = await http.post(INFO_URL, json={"type": "predictedFundings"})
            data = r.json()
            compare = _parse_predicted_fundings(data, "BTC")
            if compare:
                _cache["fundingCompare"] = compare
        except Exception as e:  # noqa: BLE001
            log.warning("funding_loop: %s", e)
        await asyncio.sleep(300)


async def _fng_loop():
    import asyncio

    http = _deps["http"]
    while True:
        try:
            r = await http.get(FNG_URL)
            j = r.json()
            d = j["data"][0]
            _cache["fng"] = {"value": int(d["value"]), "label": d["value_classification"]}
        except Exception as e:  # noqa: BLE001
            log.warning("fng_loop: %s", e)
        await asyncio.sleep(300)


def _parse_predicted_fundings(data, coin: str) -> list[dict]:
    """
    predictedFundings 形式: [[coin, [[venue, {fundingRate, ...}], ...]], ...]
    APR = rate × 24 × 365 × 100 (各社の時間当たりレート前提 — API_DESIGN §3)。
    """
    out: dict[str, float] = {}
    for entry in data or []:
        if not isinstance(entry, list) or len(entry) < 2 or entry[0] != coin:
            continue
        for venue in entry[1] or []:
            try:
                vname, info = venue[0], venue[1]
                if info is None:
                    continue
                rate = float(info.get("fundingRate"))
                disp = VENUE_NAMES.get(vname)
                if disp:
                    out[disp] = round(rate * 24 * 365 * 100, 1)
            except (TypeError, ValueError, KeyError):
                continue
    return [{"ex": ex, "apr": out[ex]} for ex in VENUE_ORDER if ex in out]


async def run():
    import asyncio

    asyncio.create_task(_candle_loop())
    asyncio.create_task(_funding_loop())
    asyncio.create_task(_fng_loop())


# ---------------- エンドポイント ----------------
@router.get("/api/market")
async def market():
    store = _deps["store"]
    ctx = getattr(store, "asset_ctx", None) if store else None
    candles = _cache["candles"]
    if not ctx or not candles:
        raise HTTPException(503, "market データ準備中 — 起動直後は数十秒待ってください")

    price = ctx["markPx"]
    prev = ctx["prevDayPx"]
    chg24 = ((price - prev) / prev * 100) if prev else 0.0
    funding_hr = ctx["funding"]
    funding_apr = funding_hr * 24 * 365 * 100 if funding_hr is not None else None
    oi_usd = ctx["openInterest"] * price if ctx["openInterest"] is not None else None
    vol24_usd = ctx["dayNtlVlm"]

    return {
        "coin": "BTC",
        "updated": int(time.time() * 1000),
        "price": price,
        "chg24": round(chg24, 4),
        "candles": candles,
        "fundingApr": round(funding_apr, 4) if funding_apr is not None else None,
        "oiUsd": round(oi_usd) if oi_usd is not None else None,
        "vol24Usd": round(vol24_usd) if vol24_usd is not None else None,
        "fundingCompare": _cache["fundingCompare"],
        "fng": _cache["fng"],
    }
