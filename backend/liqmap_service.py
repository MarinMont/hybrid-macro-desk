"""
HL Liquidation Map Service — Hyperliquid実データ清算マップ
============================================================
Coinglassの「推定モデル」と異なり、Hyperliquidのオンチェーン透明性を利用して
実際のポジションの清算価格(liquidationPx)を集計する。

データフロー:
  1. Harvester: リーダーボード(大口)のブートストラップ + 約定WS(users欄)から
     アクティブアドレスを継続収集
  2. Scanner:   優先度付きキューで clearinghouseState を巡回し、対象コインの
     ポジション(szi, liquidationPx, positionValue)をキャッシュ
  3. Aggregator: 清算価格を価格ビン(mark±15%, 0.25%刻み)に集約。
     カバー率 = 走査済み建玉 / 全体OI を常に併記
  4. FastAPI:   GET /api/liq-map でダッシュボードに配信

レート設計 (HL info APIは重み制・約1200weight/分/IP):
  - 予算は控えめに HL_WEIGHT_BUDGET=600/分 (デフォルト)
  - clearinghouseState = weight 2 → 最大 ~300走査/分
  - 再走査間隔: WHALE(>$1M)=120s / MID(>$100K)=300s / SMALL=900s
  - 新規アドレス(WS発見)は即時1回走査し、対象ポジションが無ければ6hクールダウン

注意事項:
  - クロスマージンの liquidationPx は口座全体の証拠金に依存して動くため、
    大口ほど高頻度で再走査する設計になっている
  - リーダーボードURLは非公式エンドポイントのため、失敗時はWS収集のみで継続
  - 本サービスは環境認識ツールであり、投資助言ではない

起動: uvicorn liqmap_service:app --port 8787
"""

import asyncio
import heapq
import json
import logging
import os
import time
from collections import deque

import httpx
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("liqmap")

# ---------------- 設定 ----------------
INFO_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

COIN = os.getenv("LIQMAP_COIN", "BTC")
WEIGHT_BUDGET_PER_MIN = int(os.getenv("HL_WEIGHT_BUDGET", "600"))
LEADERBOARD_TOP_N = int(os.getenv("LIQMAP_LEADERBOARD_N", "3000"))
BIN_PCT = float(os.getenv("LIQMAP_BIN_PCT", "0.0025"))   # 0.25%刻み
RANGE_PCT = float(os.getenv("LIQMAP_RANGE_PCT", "0.15")) # mark±15%
STALE_POSITION_SEC = 3600  # 1時間更新が無いポジションは集計から除外

TIER_INTERVALS = {"WHALE": 120, "MID": 300, "SMALL": 900}
EMPTY_COOLDOWN_SEC = 6 * 3600
TOP_TRADERS_N = int(os.getenv("LIQMAP_TOP_TRADERS_N", "50"))
TOP_REFRESH_SEC = int(os.getenv("LIQMAP_TOP_REFRESH_SEC", "90"))
LEADERBOARD_REFRESH_SEC = 1800
# HLP等のVault・追跡除外アドレス (カンマ区切り)
IGNORE_ADDRS = {a.strip().lower() for a in os.getenv("LIQMAP_IGNORE_ADDRS", "").split(",") if a.strip()}
COINGLASS_KEY = os.getenv("COINGLASS_API_KEY", "")
COINGLASS_BASE = "https://open-api-v4.coinglass.com"


# ---------------- レート制御 (トークンバケット) ----------------
class WeightBucket:
    def __init__(self, per_min: int):
        self.capacity = per_min
        self.tokens = float(per_min)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self, weight: int):
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.capacity / 60)
                self.updated = now
                if self.tokens >= weight:
                    self.tokens -= weight
                    return
                wait = (weight - self.tokens) * 60 / self.capacity
            await asyncio.sleep(min(wait, 5.0))


bucket = WeightBucket(WEIGHT_BUDGET_PER_MIN)


# ---------------- 状態ストア ----------------
class Store:
    def __init__(self):
        self.positions: dict[str, dict] = {}   # addr -> {szi, liq_px, pos_usd, side, ts}
        self.meta: dict[str, dict] = {}        # addr -> {tier, next_due, empty_count, cooldown_until}
        self.queue: list[tuple[float, str]] = []  # (next_due, addr)
        self.seen: set[str] = set()
        self.top_list: list[dict] = []  # リーダーボード上位 [{addr, rank, month_pnl, account_value}]
        self.mark_px: float | None = None
        self.total_oi_coins: float | None = None
        self.asset_ctx: dict | None = None  # collectors/market が消費する対象コインの完全なctx
        self.snapshot: dict | None = None
        self.scans = 0
        self.started = time.time()

    def add_address(self, addr: str, due: float | None = None):
        addr = addr.lower()
        if addr in self.seen:
            return
        self.seen.add(addr)
        self.meta[addr] = {"tier": "SMALL", "empty_count": 0, "cooldown_until": 0}
        heapq.heappush(self.queue, (due if due is not None else time.time(), addr))

    def reschedule(self, addr: str, delay: float):
        heapq.heappush(self.queue, (time.time() + delay, addr))


store = Store()
http = httpx.AsyncClient(timeout=10)


# ---------------- Harvester ----------------
async def leaderboard_loop():
    """大口アドレスの初期投入 + 上位50人リストの定期更新 (失敗しても致命的ではない)"""
    first = True
    while True:
        try:
            r = await http.get(LEADERBOARD_URL)
            rows = r.json().get("leaderboardRows", [])

            def month_pnl(row):
                for name, perf in row.get("windowPerformances", []):
                    if name == "month":
                        return float(perf.get("pnl", 0))
                return 0.0

            rows = [x for x in rows if x.get("ethAddress") and x["ethAddress"].lower() not in IGNORE_ADDRS]

            # 上位50人 = 30日PnLでランク付け (「今勝っている人の目線」を見るため)
            by_pnl = sorted(rows, key=month_pnl, reverse=True)[:TOP_TRADERS_N]
            store.top_list = [
                {
                    "addr": x["ethAddress"].lower(),
                    "rank": i + 1,
                    "month_pnl": month_pnl(x),
                    "account_value": float(x.get("accountValue", 0)),
                }
                for i, x in enumerate(by_pnl)
            ]

            if first:
                # 清算マップ用: 口座残高上位を初回一括投入
                rows.sort(key=lambda x: float(x.get("accountValue", 0)), reverse=True)
                now = time.time()
                for i, row in enumerate(rows[:LEADERBOARD_TOP_N]):
                    store.add_address(row["ethAddress"], due=now + i * 0.15)
                log.info("leaderboard bootstrap: %d addresses queued", min(len(rows), LEADERBOARD_TOP_N))
                first = False
            log.info("top traders list refreshed: %d addrs", len(store.top_list))
        except Exception as e:
            log.warning("leaderboard unavailable (%s) — WS収集のみで継続", e)
        await asyncio.sleep(LEADERBOARD_REFRESH_SEC)


async def ws_trade_harvester():
    """約定ストリームの users 欄からアクティブアドレスを収集"""
    sub = {"method": "subscribe", "subscription": {"type": "trades", "coin": COIN}}
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                log.info("WS connected: trades/%s", COIN)
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("channel") != "trades":
                        continue
                    for t in msg.get("data", []):
                        for addr in t.get("users", []) or []:
                            store.add_address(addr)
        except Exception as e:
            log.warning("WS error: %s — 5s後に再接続", e)
            await asyncio.sleep(5)


# ---------------- 市場データ (mark / OI) ----------------
async def market_loop():
    while True:
        try:
            await bucket.take(20)  # metaAndAssetCtxs は重いリクエスト
            r = await http.post(INFO_URL, json={"type": "metaAndAssetCtxs"})
            meta, ctxs = r.json()
            idx = next(i for i, u in enumerate(meta["universe"]) if u["name"] == COIN)
            ctx = ctxs[idx]
            store.mark_px = float(ctx["markPx"])
            store.total_oi_coins = float(ctx["openInterest"])
            # collectors/market 用に完全なctxを保持 (同一リクエストの再利用でHL重み予算を節約)
            store.asset_ctx = {
                "markPx": float(ctx["markPx"]),
                "prevDayPx": float(ctx["prevDayPx"]),
                "funding": float(ctx["funding"]) if ctx.get("funding") is not None else None,
                "openInterest": float(ctx["openInterest"]),
                "dayNtlVlm": float(ctx["dayNtlVlm"]),
            }
        except Exception as e:
            log.warning("market_loop: %s", e)
        await asyncio.sleep(20)


# ---------------- Scanner ----------------
def _tier(pos_usd: float) -> str:
    if pos_usd >= 1_000_000:
        return "WHALE"
    if pos_usd >= 100_000:
        return "MID"
    return "SMALL"


async def fetch_position(addr: str) -> dict | None:
    """clearinghouseState から対象コインのポジションを取得し、ストアへ反映"""
    await bucket.take(2)
    r = await http.post(INFO_URL, json={"type": "clearinghouseState", "user": addr})
    data = r.json()
    store.scans += 1
    for ap in data.get("assetPositions", []):
        p = ap.get("position", {})
        if p.get("coin") == COIN and float(p.get("szi", 0)) != 0:
            szi = float(p["szi"])
            liq = p.get("liquidationPx")
            lev = p.get("leverage", {}) or {}
            pos = {
                "szi": szi,
                "liq_px": float(liq) if liq is not None else None,
                "pos_usd": abs(float(p.get("positionValue", 0))),
                "entry_px": float(p.get("entryPx", 0) or 0),
                "upnl": float(p.get("unrealizedPnl", 0) or 0),
                "lev": lev.get("value"),
                "lev_type": lev.get("type"),
                "side": "long" if szi > 0 else "short",
                "ts": time.time(),
            }
            store.positions[addr] = pos
            return pos
    store.positions.pop(addr, None)
    return None


async def scan_address(addr: str):
    pos = await fetch_position(addr)
    meta = store.meta[addr]
    if pos is None:
        meta["empty_count"] += 1
        if meta["empty_count"] >= 2:
            meta["cooldown_until"] = time.time() + EMPTY_COOLDOWN_SEC
            store.reschedule(addr, EMPTY_COOLDOWN_SEC)
        else:
            store.reschedule(addr, TIER_INTERVALS["SMALL"])
        return
    meta["empty_count"] = 0
    meta["tier"] = _tier(pos["pos_usd"])
    store.reschedule(addr, TIER_INTERVALS[meta["tier"]])


async def top_scan_loop():
    """リーダーボード上位50人を高頻度で巡回 (50×weight2=100 / 90s → 予算の~2%)"""
    while True:
        for t in list(store.top_list):
            try:
                await fetch_position(t["addr"])
            except Exception as e:
                log.debug("top scan %s failed: %s", t["addr"][:10], e)
        await asyncio.sleep(TOP_REFRESH_SEC)


async def scanner_loop():
    while True:
        now = time.time()
        if not store.queue or store.queue[0][0] > now:
            await asyncio.sleep(0.25)
            continue
        _, addr = heapq.heappop(store.queue)
        meta = store.meta.get(addr)
        if meta is None or meta["cooldown_until"] > now:
            if meta:
                store.reschedule(addr, max(1.0, meta["cooldown_until"] - now))
            continue
        try:
            await scan_address(addr)
        except Exception as e:
            log.debug("scan %s failed: %s", addr[:10], e)
            store.reschedule(addr, 120)


# ---------------- Aggregator ----------------
def build_snapshot() -> dict | None:
    mark = store.mark_px
    if not mark:
        return None
    now = time.time()
    bin_w = mark * BIN_PCT
    lo, hi = mark * (1 - RANGE_PCT), mark * (1 + RANGE_PCT)
    buckets: dict[int, dict] = {}
    scanned_coins = 0.0
    n_pos = 0
    for pos in store.positions.values():
        if now - pos["ts"] > STALE_POSITION_SEC:
            continue
        scanned_coins += abs(pos["szi"])
        n_pos += 1
        liq = pos["liq_px"]
        if liq is None or not (lo <= liq <= hi):
            continue
        idx = int((liq - lo) / bin_w)
        b = buckets.setdefault(idx, {"px": lo + (idx + 0.5) * bin_w, "longUsd": 0.0, "shortUsd": 0.0})
        notional = abs(pos["szi"]) * liq
        b["longUsd" if pos["side"] == "long" else "shortUsd"] += notional

    rows = sorted(buckets.values(), key=lambda b: b["px"])
    clusters = []
    for b in rows:
        if b["longUsd"] > 0:
            clusters.append({"px": round(b["px"], 1), "usd": round(b["longUsd"]), "side": "long"})
        if b["shortUsd"] > 0:
            clusters.append({"px": round(b["px"], 1), "usd": round(b["shortUsd"]), "side": "short"})
    top = sorted(clusters, key=lambda c: c["usd"], reverse=True)
    top_long = [c for c in top if c["side"] == "long"][:8]
    top_short = [c for c in top if c["side"] == "short"][:8]

    coverage = None
    if store.total_oi_coins:
        coverage = min(100.0, scanned_coins / (2 * store.total_oi_coins) * 100)

    return {
        "coin": COIN,
        "updated": int(now * 1000),
        "markPx": mark,
        "coveragePct": round(coverage, 1) if coverage is not None else None,
        "positionsTracked": n_pos,
        "addressesKnown": len(store.seen),
        "scansTotal": store.scans,
        "buckets": [
            {"px": round(b["px"], 1), "longUsd": round(b["longUsd"]), "shortUsd": round(b["shortUsd"])}
            for b in rows
        ],
        "topClusters": top_long + top_short,
    }


async def aggregator_loop():
    while True:
        try:
            snap = build_snapshot()
            if snap:
                store.snapshot = snap
        except Exception as e:
            log.warning("aggregator: %s", e)
        await asyncio.sleep(5)


# ---------------- Coinglass プロキシ (Hobbyist $29) ----------------
# APIキーはサーバー側のみで保持。フロントには絶対に渡さない。
# Hobbyistは30req/分 — 清算履歴などの低頻度データは60sキャッシュで十分。
_cg_cache: dict[str, tuple[float, dict]] = {}


async def coinglass_get(path: str, params: dict, cache_sec: int = 60) -> dict:
    if not COINGLASS_KEY:
        raise HTTPException(503, "COINGLASS_API_KEY が未設定です (.env を確認)")
    key = path + json.dumps(params, sort_keys=True)
    hit = _cg_cache.get(key)
    if hit and time.time() - hit[0] < cache_sec:
        return hit[1]
    r = await http.get(
        f"{COINGLASS_BASE}{path}",
        params=params,
        headers={"CG-API-KEY": COINGLASS_KEY, "accept": "application/json"},
    )
    data = r.json()
    _cg_cache[key] = (time.time(), data)
    return data


# ---------------- FastAPI ----------------
app = FastAPI(title="HL Liquidation Map Service")

# CORS: 本番では FRONTEND_ORIGIN (カンマ区切り可) に VercelのURLを設定する。
# 未設定時は開発用に全許可 ("*") — 従来のローカル挙動を維持する。
_origins_env = os.getenv("FRONTEND_ORIGIN", "").strip()
ALLOW_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware, allow_origins=ALLOW_ORIGINS, allow_methods=["*"], allow_headers=["*"]
)


@app.on_event("startup")
async def startup():
    asyncio.create_task(market_loop())
    asyncio.create_task(ws_trade_harvester())
    asyncio.create_task(scanner_loop())
    asyncio.create_task(aggregator_loop())
    asyncio.create_task(leaderboard_loop())
    asyncio.create_task(top_scan_loop())
    # 新規collectors (market/derivs/macro/ai/entry_state) のポーリング起動
    import collectors
    for mod in collectors.ALL:
        asyncio.create_task(mod.run())
    log.info("liqmap service started (coin=%s, budget=%d weight/min)", COIN, WEIGHT_BUDGET_PER_MIN)


@app.get("/api/top-traders")
async def top_traders():
    """リーダーボード上位 (30日PnL順) の現在ポジション — Smart Moneyの目線"""
    if not store.top_list:
        raise HTTPException(503, "リーダーボード未取得 — 起動直後は少し待ってください")
    traders = []
    long_usd = short_usd = 0.0
    long_n = short_n = flat_n = 0
    for t in store.top_list:
        pos = store.positions.get(t["addr"])
        if pos is None or time.time() - pos["ts"] > STALE_POSITION_SEC:
            flat_n += 1
            continue
        if pos["side"] == "long":
            long_n += 1
            long_usd += pos["pos_usd"]
        else:
            short_n += 1
            short_usd += pos["pos_usd"]
        traders.append({
            "rank": t["rank"],
            "addr": t["addr"][:6] + "…" + t["addr"][-4:],
            "monthPnl": round(t["month_pnl"]),
            "side": pos["side"],
            "posUsd": round(pos["pos_usd"]),
            "entryPx": round(pos["entry_px"], 1),
            "liqPx": round(pos["liq_px"], 1) if pos["liq_px"] else None,
            "upnl": round(pos["upnl"]),
            "lev": pos["lev"],
            "levType": pos["lev_type"],
        })
    traders.sort(key=lambda x: x["posUsd"], reverse=True)
    total = long_usd + short_usd
    return {
        "coin": COIN,
        "updated": int(time.time() * 1000),
        "summary": {
            "longCount": long_n,
            "shortCount": short_n,
            "flatCount": flat_n,
            "longUsd": round(long_usd),
            "shortUsd": round(short_usd),
            "longNotionalPct": round(long_usd / total * 100, 1) if total else None,
        },
        "traders": traders[:15],
    }


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "uptimeSec": int(time.time() - store.started),
        "addresses": len(store.seen),
        "positions": len(store.positions),
        "scans": store.scans,
        "markPx": store.mark_px,
    }


@app.get("/api/liq-map")
async def liq_map():
    if store.snapshot is None:
        raise HTTPException(503, "スナップショット生成中 — 起動直後は1〜2分待ってください")
    return store.snapshot


@app.get("/api/coinglass/liquidation-history")
async def cg_liq_history(symbol: str = "BTC", interval: str = "1h"):
    """例: Coinglass清算履歴 (Hobbyist可) — ロング/ショート別の清算額推移"""
    return await coinglass_get(
        "/api/futures/liquidation/aggregated-history",
        {"symbol": symbol, "interval": interval},
        cache_sec=120,
    )


# ---------------- collectors (新規エンドポイント) の登録 ----------------
# market / derivs / macro / ai-overview / entry-state を疎結合で追加する。
# 各collectorには共有依存 (httpxクライアント / HLレートバケット / Store) を注入し、
# ルーターを取り込む。ポーリングループは上の startup() で create_task される。
import collectors  # noqa: E402

for _mod in collectors.ALL:
    _mod.init(http, bucket, store)
    app.include_router(_mod.router)


# ---------------- 直接起動 (python liqmap_service.py) ----------------
# 通常は `uvicorn liqmap_service:app --port 8787` で起動する。
# Render等はポートを環境変数 PORT で注入するため、直接起動時もそれを尊重する (未設定は8787)。
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8787")))
