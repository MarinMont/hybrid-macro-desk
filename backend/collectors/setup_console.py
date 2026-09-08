"""
setup_console.py — /api/setup/*  セットアップコンソール (handoff/SETUP_CONSOLE_SPEC.md)
=====================================================================================
判定・算術・台帳・フロー・カレンダーの純関数は setup_console/ パッケージ。ここは
  - Binance BTCUSDT klines のポーリング (15m を 15s / 1h を 60s。確定足のみ判定に使う)
  - 確定足ごとの吸収コンファーム判定 → 履歴 (data/history.json) と台帳の到達行の更新
  - フローの自動遷移 (到達 / 4窓 / タイマー) と、人間のチェック操作の受け口
  - GET /api/setup/state (画面が 15s 毎に読む集約) と各 POST
発注 API には接続しない。取引権限のキーを持たない。

障害時: Binance 不達なら直近データを stale=true で返し続ける。データが無ければ state は「データ待ち」。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from setup_console import atr as atrmod, calendar_gate as cg, entry_bridge as EB, flow as F, klines, ledger as LG, replay as RP
from setup_console.arithmetic import ArithInput, EntryLeg, check as arith_check
from setup_console.config import DEFAULT_VERSION, list_versions, load_config
from setup_console.confirm import ACCEPT_TEXT, WindowBar, diagnosis_rows, evaluate_series, judge
from setup_console.store import data_path, read_json, write_json_atomic
from setup_console.tz import bar_labels, paris_date, paris_label, paris_to_ms

log = logging.getLogger("liqmap.setup")
router = APIRouter()

FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
POLL_15M_SEC = int(os.getenv("SETUP_POLL_15M_SEC", "15"))
POLL_1H_SEC = int(os.getenv("SETUP_POLL_1H_SEC", "60"))
LIMIT_15M = 500     # ATR 収束 + 床 median 96 本 + 到達検出の範囲
LIMIT_1M = 1000     # reconstruct 方式 (直近 66 本の 15m 足ぶん)
LIMIT_1H = 200
HISTORY_MAX = 2000
STALE_SEC = 90

WRITE_ENTRY_STATE = os.getenv("SETUP_WRITE_ENTRY_STATE", "1") == "1"   # ダッシュボードの Entry Engine へ写す
INPUTS_FILE = "inputs.json"
HISTORY_FILE = "history.json"
DEFAULT_INPUTS = {"mode": "both", "ref_s": 0.0, "ref_l": 0.0, "zone_lo": 0.0, "zone_hi": 0.0, "use_zone": False}

_deps: dict = {"http": None}
_lock = asyncio.Lock()
_st: dict = {
    "bars15": [], "bars1": [], "bars1h": [],
    "fetched_15m": 0.0, "fetched_1h": 0.0, "error": None,
    "latest": None,          # 最新確定足の Diagnosis
    "latest_counts": (0, 0),
    "provisional": None,     # 形成中の足の暫定診断
    "accept_events": {},
    "last_history_t": 0,
}


def init(http, bucket, store) -> None:  # bucket 不使用 (Binance は HL 重み予算の対象外)
    _deps["http"] = http


# ---------------- 永続データ ----------------
def _inputs() -> dict:
    d = read_json(data_path(INPUTS_FILE), None)
    return {**DEFAULT_INPUTS, **(d or {})}


def _history() -> list[dict]:
    return read_json(data_path(HISTORY_FILE), [])


def _cfg():
    return load_config(_inputs().get("version", DEFAULT_VERSION))


# ---------------- ポーリング ----------------
async def _fetch(interval: str, limit: int) -> list[klines.Bar]:
    http = _deps["http"]
    r = await http.get(f"{FAPI}/fapi/v1/klines", params={"symbol": SYMBOL, "interval": interval, "limit": limit}, timeout=10.0)
    r.raise_for_status()
    return klines.parse_klines(r.json())


async def _fetch_range(interval: str, start_ms: int, end_ms: int, limit: int = 1500) -> list[klines.Bar]:
    """範囲指定の klines (ページング)。リプレイ用。"""
    http = _deps["http"]
    out: dict[int, klines.Bar] = {}
    cur = start_ms
    while cur < end_ms:
        r = await http.get(f"{FAPI}/fapi/v1/klines",
                           params={"symbol": SYMBOL, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": limit}, timeout=20.0)
        r.raise_for_status()
        chunk = klines.parse_klines(r.json())
        if not chunk:
            break
        for b in chunk:
            out[b.t] = b
        if len(chunk) < limit:
            break
        cur = chunk[-1].t + 1
    return [out[t] for t in sorted(out)]


async def _loop_15m():
    while True:
        try:
            cfg = _cfg()
            _st["bars15"] = await _fetch("15m", LIMIT_15M)
            if cfg.delta_method == "reconstruct":
                _st["bars1"] = await _fetch("1m", LIMIT_1M)
            _st["fetched_15m"] = time.time()
            _st["error"] = None
            async with _lock:
                _evaluate(int(time.time() * 1000))
        except Exception as e:  # noqa: BLE001
            _st["error"] = str(e)
            log.warning("setup 15m loop: %s", e)
        await asyncio.sleep(POLL_15M_SEC)


async def _loop_1h():
    while True:
        try:
            _st["bars1h"] = await _fetch("1h", LIMIT_1H)
            _st["fetched_1h"] = time.time()
        except Exception as e:  # noqa: BLE001
            log.warning("setup 1h loop: %s", e)
        await asyncio.sleep(POLL_1H_SEC)


async def run():
    asyncio.create_task(_loop_15m())
    asyncio.create_task(_loop_1h())


# ---------------- 判定 → 履歴 / 台帳 / フロー (純関数を束ねる) ----------------
def _series(bars15: list[klines.Bar], cfg, inp: dict, now_ms: int):
    closed = klines.only_closed(bars15, now_ms, 900_000)
    ds = klines.deltas(closed, cfg.delta_method, _st["bars1"])
    atrs = atrmod.atr_series(closed, cfg.atr_len)
    meds = [klines.volume_median(closed[: i + 1], cfg.vol_median_len) for i in range(len(closed))]
    wbs = [WindowBar(t=b.t, h=b.h, l=b.l, c=b.c, v=b.v, delta=(d if d is not None else 0.0)) for b, d in zip(closed, ds)]
    res = evaluate_series(wbs, atrs, meds, cfg, inp["mode"], float(inp["ref_s"]), float(inp["ref_l"]), floor_enabled=True)
    # reconstruct 方式で 1 分足が揃わない足 (δ=None) を含む窓は判定しない (偽の δ=0 で判定を出さない)
    ok = [d is not None for d in ds]
    valid = {closed[i].t for i in range(len(closed)) if i >= 2 and all(ok[i - 2:i + 1])}
    res.diagnoses[:] = [d for d in res.diagnoses if d.t in valid]
    return closed, wbs, atrs, meds, res


def _atr_1h_info(now_ms: int, holidays: list[dict]) -> dict:
    """1H ATR (SL設計用) と、直近フル流動性セッション (平日・非休場日) 最終確定足時点の ATR (暫定定義)。"""
    closed = klines.only_closed(_st["bars1h"], now_ms, 3_600_000)
    if not closed:
        return {"atr_1h": None, "atr_1h_last_session": None, "last_session_bar_local": None}
    s = atrmod.atr_series(closed, 14)
    last_sess = None
    last_sess_label = None
    for b, a in zip(reversed(closed), reversed(s)):
        if a is None:
            continue
        d = paris_date(b.t)
        if cg.is_business_day(d, holidays):
            last_sess, last_sess_label = a, paris_label(b.t)
            break
    return {"atr_1h": s[-1], "atr_1h_last_session": last_sess, "last_session_bar_local": last_sess_label}


def _evaluate(now_ms: int) -> None:
    """確定足を評価して履歴・台帳・フローを更新する (_lock の内側で呼ぶ)。"""
    bars15 = _st["bars15"]
    if len(bars15) < 4:
        return
    cfg = _cfg()
    inp = _inputs()
    closed, wbs, atrs, meds, res = _series(bars15, cfg, inp, now_ms)
    if not res.diagnoses:
        return
    latest = res.diagnoses[-1]
    _st["latest"] = latest
    _st["latest_counts"] = res.accept_counts.get(latest.t, (0, 0))
    _st["accept_events"] = res.accept_events

    # 形成中の足の暫定診断 (判定は出さない。数値だけ「暫定」ラベルで表示)
    forming = [b for b in bars15 if b.t + 900_000 > now_ms]
    if forming and len(wbs) >= 2:
        fb = forming[-1]
        fd = klines.deltas([fb], cfg.delta_method, _st["bars1"])[0]
        if fd is None:   # 形成中の 15 分足は 1 分足が 15 本揃わない → 揃った分だけの符号付き出来高で暫定表示
            fd = sum(b.sign_delta for b in _st["bars1"] if fb.t <= b.t < fb.t + 900_000)
        fw = WindowBar(t=fb.t, h=fb.h, l=fb.l, c=fb.c, v=fb.v, delta=fd)
        fatr = atrmod.atr_series(closed + [fb], cfg.atr_len)[-1]
        fmed = klines.volume_median(closed + [fb], cfg.vol_median_len)
        _st["provisional"] = judge([fw, wbs[-1], wbs[-2]], fatr, cfg, inp["mode"], float(inp["ref_s"]), float(inp["ref_l"]), fmed, True)
    else:
        _st["provisional"] = None

    # 履歴: 新しい確定足のみ追記 (判定当時の入力で記録する。遡って書き換えない)
    hist = _history()
    last_t = max((h["open_utc_ms"] for h in hist), default=0)
    new = [d for d in res.diagnoses if d.t > last_t]
    holidays = cg.load_calendar()["holidays"]
    atr1h = _atr_1h_info(now_ms, holidays)["atr_1h"]
    flow = F.load_flow()
    flow_changed = False
    for d in new:
        ev = res.accept_events.get(d.t)
        hist.append({**d.to_dict(), "accept_event": ev, "accept_text": ACCEPT_TEXT.get(ev) if ev else None,
                     "accept_counts": list(res.accept_counts.get(d.t, (0, 0))), "inputs": {k: inp[k] for k in ("mode", "ref_s", "ref_l")}})
        if flow.state in ("AT_POI", "CONFIRMED", "FILLED_UNCONFIRMED"):
            flow = F.on_verdict(flow, d.verdict, ev, d.t, atr1h)
            flow_changed = True
    if new:
        hist = hist[-HISTORY_MAX:]
        write_json_atomic(data_path(HISTORY_FILE), hist)
    _st["last_history_t"] = max(last_t, *(d.t for d in new)) if new else last_t

    # 台帳: 到達行の同期 (人間の記入欄は保持)
    lg = LG.load_ledger()
    spans = LG.detect_touches(closed, LG.targets_from_ledger(lg))
    by_key = {(t.target_id, t.ts_utc): t for t in lg.touches}
    verdict_by_t = {h["open_utc_ms"]: h for h in hist}
    changed = False
    for sp in spans:
        key = (sp.target_id, sp.start_t)
        if key not in by_key:
            tid = f"T{len(lg.touches) + 1:04d}"
            t = LG.span_to_touch(sp, True, tid)
            lg.touches.append(t)
            by_key[key] = t
            changed = True
            if sp.target_id == flow.band_id:
                before = flow.state
                flow = F.on_touch(flow, sp.target_id, sp.start_t)
                flow_changed = flow_changed or (before != flow.state)
        t = by_key[key]
        fires = [x for x in sp.bars_t if verdict_by_t.get(x, {}).get("verdict") == "fire"]
        rejects = [x for x in sp.bars_t if verdict_by_t.get(x, {}).get("verdict") == "rejected_c"]
        accepts = [x for x in sp.bars_t if x in res.accept_events]
        new_verdict = "confirmed" if fires else "rejected_c" if rejects else "silent"
        new_first = paris_label(fires[0]) if fires else None
        new_acc = paris_label(accepts[0]) if accepts else None
        if (t.verdict, t.first_fire_ts, t.accept_warning_ts, t.end_ts_utc, t.bar_count) != (new_verdict, new_first, new_acc, sp.end_t, len(sp.bars_t)):
            t.verdict, t.first_fire_ts, t.accept_warning_ts, t.end_ts_utc, t.bar_count = new_verdict, new_first, new_acc, sp.end_t, len(sp.bars_t)
            changed = True
        if sp.end_t is not None and sp.target_id == flow.band_id and flow.state == "AT_POI":
            flow = F.on_band_left(flow, sp.target_id)
            flow_changed = True
    if changed:
        LG.save_ledger(lg)
    if flow_changed:
        F.save_flow(flow)
    _write_entry_state(now_ms, flow=flow, lg=lg)


def _dashboard_candles() -> list[dict]:
    """ダッシュボードの HL 1h 足 (collectors/market のキャッシュ)。regime の簡易判定に使う。"""
    try:
        from collectors import market as mk
        return list(mk._cache.get("candles") or [])
    except Exception:  # noqa: BLE001
        return []


_last_entry_state: dict | None = None


def _write_entry_state(now_ms: int, flow: F.FlowState | None = None, lg: LG.Ledger | None = None) -> None:
    """コンソールの状態を entry_state.json (SPEC §7) に写す。/api/entry-state が読む場所 (ENTRY_STATE_PATH) と同じ。"""
    global _last_entry_state
    if not WRITE_ENTRY_STATE:
        return
    try:
        flow = flow or F.load_flow()
        lg = lg or LG.load_ledger()
        bars15 = _st["bars15"]
        price = bars15[-1].c if bars15 else None
        latest = _st["latest"]
        atr15 = latest.atr if latest else None
        view = _ledger_view(lg, price, atr15, paris_date(now_ms))["bands"]
        doc = EB.build_entry_state(flow, latest, lg, price, atr15, _dashboard_candles(), now_ms, view)
        cmp_doc = {k: v for k, v in doc.items() if k != "console"}
        if cmp_doc == _last_entry_state:
            return
        _last_entry_state = cmp_doc
        write_json_atomic(Path(os.getenv("ENTRY_STATE_PATH", "entry_state.json")), doc)
    except Exception as e:  # noqa: BLE001
        log.warning("entry_state.json write failed: %s", e)


# ---------------- GET /api/setup/state ----------------
def _ledger_view(lg: LG.Ledger, price: float | None, atr15: float | None, today) -> dict:
    lm = lg.level_map()

    def dist(p):
        if price is None:
            return {"usd": None, "atr": None}
        return {"usd": p - price, "atr": ((p - price) / atr15) if atr15 else None}

    levels = []
    for l in lg.levels:
        levels.append({**asdict(l), "pending": l.pending, "active": l.is_active(today), "born_yet": bool(l.valid_from) and not l.pending and l.is_active(today),
                       "distance": dist(l.price), "suggested_valid_from": LG.suggest_valid_from(l.kind, l.born_on)})
    bands = []
    for b in lg.bands:
        mid = (b.lo + b.hi) / 2
        bands.append({**asdict(b), "pending": b.pending, "valid_from": b.valid_from(lm), "active": b.is_active(lm, today),
                      "distance": dist(mid), "thickness": b.hi - b.lo})
    touches = [asdict(t) for t in lg.touches][-100:]
    return {"levels": levels, "bands": bands, "touches": touches}


@router.get("/api/setup/state")
async def state():
    now_ms = int(time.time() * 1000)
    cfg = _cfg()
    inp = _inputs()
    cal = cg.load_calendar()
    gate = cg.gate(now_ms, cal)
    bars15 = _st["bars15"]
    price = bars15[-1].c if bars15 else None
    latest = _st["latest"]
    prov = _st["provisional"]
    atr1h = _atr_1h_info(now_ms, cal["holidays"])
    lg = LG.load_ledger()
    flow = F.load_flow()
    today = paris_date(now_ms)
    stale = (time.time() - _st["fetched_15m"]) > STALE_SEC
    hist = _history()
    recent = [b for b in bars15[-40:]]
    return {
        "updated": now_ms, "now_local": paris_label(now_ms),
        "version": cfg.version, "versions": list_versions(), "config": cfg.to_dict(),
        "inputs": inp,
        "data": {"symbol": SYMBOL, "stale": stale, "error": _st["error"], "fetched_15m": _st["fetched_15m"],
                 "bars15": len(bars15), "price": price, "waiting": not bars15},
        "latest": ({**latest.to_dict(), "rows": diagnosis_rows(latest, _st["latest_counts"]),
                    "accept_event": _st["accept_events"].get(latest.t),
                    "accept_text": ACCEPT_TEXT.get(_st["accept_events"].get(latest.t)) if _st["accept_events"].get(latest.t) else None}
                   if latest else None),
        "provisional": ({**prov.to_dict(), "rows": diagnosis_rows(prov, _st["latest_counts"]), "provisional": True} if prov else None),
        "atr15": latest.atr if latest else None,
        **atr1h,
        "weekend_or_holiday": today.weekday() >= 5 or cg.is_holiday(today, cal["holidays"]),
        "ledger": _ledger_view(lg, price, latest.atr if latest else None, today),
        "flow": flow.to_dict(),
        "gate": gate,
        "history": hist[-100:],
        "recent_bars": [{**bar_labels(b.t), "o": b.o, "h": b.h, "l": b.l, "c": b.c, "v": b.v, "closed": b.t + 900_000 <= now_ms} for b in recent],
        "limits": [
            "点灯は極値の 2〜3 本後 (30〜45 分)。追いかける道具ではない",
            "守備範囲は「攻め手の失敗 (吸収)」のみ。売り主導の反転や素直なブレイク継続は拾わない",
            "閾値は 15 分足で校正されたセット。他の時間足への流用禁止",
        ],
    }


# ---------------- POST: 判定入力 ----------------
class InputsBody(BaseModel):
    mode: str = "both"
    ref_s: float = 0.0
    ref_l: float = 0.0
    zone_lo: float = 0.0
    zone_hi: float = 0.0
    use_zone: bool = False
    version: str | None = None


@router.post("/api/setup/inputs")
async def set_inputs(body: InputsBody):
    if body.mode not in ("S", "L", "both"):
        raise HTTPException(400, "mode は S / L / both")
    d = {**_inputs(), **{k: v for k, v in body.model_dump().items() if v is not None}}
    if body.version and body.version not in list_versions():
        raise HTTPException(400, f"校正値バージョン {body.version} は存在しない")
    async with _lock:
        write_json_atomic(data_path(INPUTS_FILE), d)
        _evaluate(int(time.time() * 1000))
    return {"ok": True, "inputs": d}


# ---------------- POST: 算術 ----------------
class LegBody(BaseModel):
    price: float
    ratio: float = 1.0
    lot: float | None = None


class ArithBody(BaseModel):
    balance_usd: float
    risk_pct: float = 2.0
    side: str
    entries: list[LegBody]
    sl: float
    tp: float
    atr_1h: float
    atr_15m: float | None = None
    liq_upper: float | None = None
    liq_lower: float | None = None
    band_lo: float | None = None
    band_hi: float | None = None
    atr_source: str = "today"


@router.post("/api/setup/arith")
async def arith(body: ArithBody):
    cal = cg.load_calendar()
    today = paris_date(int(time.time() * 1000))
    wk = today.weekday() >= 5 or cg.is_holiday(today, cal["holidays"])
    try:
        res = arith_check(ArithInput(
            balance_usd=body.balance_usd, risk_pct=body.risk_pct, side=body.side,
            entries=[EntryLeg(l.price, l.ratio, l.lot) for l in body.entries], sl=body.sl, tp=body.tp,
            atr_1h=body.atr_1h, atr_15m=body.atr_15m, liq_upper=body.liq_upper, liq_lower=body.liq_lower,
            band_lo=body.band_lo, band_hi=body.band_hi, weekend_or_holiday=wk, atr_source=body.atr_source,
        ))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return res.to_dict()


# ---------------- POST: フロー (人間のチェック操作) ----------------
class AdvanceBody(BaseModel):
    to: str
    note: str = ""
    arith_ok: bool | None = None
    band_id: str | None = None
    side: str | None = None
    sl: float | None = None
    avg_entry: float | None = None
    tp: float | None = None


@router.post("/api/setup/flow/advance")
async def flow_advance(body: AdvanceBody):
    now_ms = int(time.time() * 1000)
    cal = cg.load_calendar()
    gate = cg.gate(now_ms, cal)
    atr1h = _atr_1h_info(now_ms, cal["holidays"])["atr_1h"]
    async with _lock:
        fs = F.load_flow()
        try:
            fs = F.advance(fs, body.to, now_ms, body.note, arith_ok=body.arith_ok, gate_open=gate["placement_allowed"],
                           band_id=body.band_id, side=body.side, sl=body.sl, avg_entry=body.avg_entry, atr_1h=atr1h, tp=body.tp)
        except F.TransitionError as e:
            raise HTTPException(400, str(e))
        F.save_flow(fs)
        _write_entry_state(now_ms, flow=fs)
    return fs.to_dict()


# ---------------- POST: 台帳 ----------------
class LevelBody(BaseModel):
    id: str
    price: float
    name: str
    kind: str
    tier: str
    born_on: str | None = None
    valid_from: str | None = None
    status: str = "active"
    note: str = ""
    needs_input: list[str] = []
    needs_confirm: list[str] = []


@router.post("/api/setup/ledger/level")
async def upsert_level(body: LevelBody):
    if body.kind not in LG.LEVEL_KINDS or body.tier not in LG.TIERS or body.status not in LG.STATUSES:
        raise HTTPException(400, "kind / tier / status が不正")
    async with _lock:
        lg = LG.load_ledger()
        lv = LG.Level(**body.model_dump())
        # 値が入った欄は要記入/要確認から外す (人間が埋めた = 確定)
        lv.needs_input = [f for f in lv.needs_input if getattr(lv, f, None) in (None, "")]
        lg.levels = [lv if x.id == lv.id else x for x in lg.levels] if any(x.id == lv.id for x in lg.levels) else lg.levels + [lv]
        LG.save_ledger(lg)
        _evaluate(int(time.time() * 1000))
    return {"ok": True, "level": asdict(lv)}


class BandBody(BaseModel):
    id: str
    side: str
    lo: float
    hi: float
    ref_level: float | None = None
    sl_basis: str = ""
    sl_price: float | None = None
    component_level_ids: list[str] = []
    note: str = ""
    needs_input: list[str] = []
    needs_confirm: list[str] = []


@router.post("/api/setup/ledger/band")
async def upsert_band(body: BandBody):
    if body.side not in ("S", "L") or body.lo >= body.hi:
        raise HTTPException(400, "side / lo<hi が不正")
    async with _lock:
        lg = LG.load_ledger()
        b = LG.Band(**body.model_dump())
        b.needs_input = [f for f in b.needs_input if getattr(b, f, None) in (None, "")]
        lg.bands = [b if x.id == b.id else x for x in lg.bands] if any(x.id == b.id for x in lg.bands) else lg.bands + [b]
        LG.save_ledger(lg)
        _evaluate(int(time.time() * 1000))
    return {"ok": True, "band": asdict(b)}


class TouchBody(BaseModel):
    id: str
    outcome_4h: str | None = None
    outcome_structure: str | None = None
    calib_scene_id: str | None = None
    note: str | None = None


@router.post("/api/setup/ledger/touch")
async def update_touch(body: TouchBody):
    if body.outcome_4h not in (None, "progress", "flat", "adverse") or body.outcome_structure not in (None, "held", "broken"):
        raise HTTPException(400, "outcome の値が不正")
    async with _lock:
        lg = LG.load_ledger()
        t = next((x for x in lg.touches if x.id == body.id), None)
        if t is None:
            raise HTTPException(404, "到達行が無い")
        for k in ("outcome_4h", "outcome_structure", "calib_scene_id", "note"):
            v = getattr(body, k)
            if v is not None:
                setattr(t, k, v)
        LG.save_ledger(lg)
    return {"ok": True, "touch": asdict(t)}


# ---------------- カレンダー ----------------
class EventBody(BaseModel):
    ts_local: str
    name: str
    importance: str = "market_moving"
    note: str = ""


@router.post("/api/setup/calendar/event")
async def add_event(body: EventBody):
    if body.importance not in ("market_moving", "minor"):
        raise HTTPException(400, "importance は market_moving / minor")
    async with _lock:
        cal = cg.load_calendar()
        cal["events"] = [e for e in cal["events"] if not (e.get("ts_local") == body.ts_local and e.get("name") == body.name)]
        cal["events"].append(body.model_dump())
        cal["events"].sort(key=lambda e: e["ts_local"])
        cg.save_calendar(cal)
    return {"ok": True, "events": cal["events"]}


class HolidayBody(BaseModel):
    date: str
    name: str = ""


@router.post("/api/setup/calendar/holiday")
async def add_holiday(body: HolidayBody):
    async with _lock:
        cal = cg.load_calendar()
        cal["holidays"] = [h for h in cal["holidays"] if h.get("date") != body.date] + [body.model_dump()]
        cal["holidays"].sort(key=lambda h: h["date"])
        cg.save_calendar(cal)
    return {"ok": True, "holidays": cal["holidays"]}


# ---------------- リプレイ (SPEC §9) ----------------
class ReplayBody(BaseModel):
    from_local: str      # "YYYY-MM-DD HH:MM" (Europe/Paris)
    to_local: str
    floor_enabled: bool = True


@router.post("/api/setup/replay")
async def replay_endpoint(body: ReplayBody):
    try:
        from_ms, to_ms = paris_to_ms(body.from_local), paris_to_ms(body.to_local)
    except ValueError:
        raise HTTPException(400, "日時は YYYY-MM-DD HH:MM (Europe/Paris)")
    if to_ms <= from_ms or (to_ms - from_ms) > 60 * 86_400_000:
        raise HTTPException(400, "範囲は 0 < 期間 ≤ 60 日")
    cfg = _cfg()
    warm = max(cfg.vol_median_len, 400) * 900_000   # ATR 収束 + 床 median の履歴
    try:
        bars = await _fetch_range("15m", from_ms - warm, to_ms + (RP.PATH_BARS + 1) * 900_000)
        bars1 = await _fetch_range("1m", from_ms - 3 * 900_000, to_ms + 900_000) if cfg.delta_method == "reconstruct" else []
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"klines 取得失敗: {e}")
    now_ms = int(time.time() * 1000)
    bars = klines.only_closed(bars, now_ms, 900_000)
    lg = LG.load_ledger()
    rows = RP.replay(bars, cfg, lg, from_ms, to_ms, bars1, body.floor_enabled)
    return {"from_local": body.from_local, "to_local": body.to_local, "bars": len(bars), "rows": rows, "csv": RP.to_csv(rows)}
