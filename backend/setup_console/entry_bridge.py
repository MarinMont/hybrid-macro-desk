"""
entry_bridge.py — セットアップコンソールの状態を entry_state.json (SPEC §7 スキーマ) に写す
==========================================================================================
ダッシュボードの Entry Engine パネルは entry_state.json を読むだけ (表示のみ)。結合はこのファイル1点。
コンソールの状態機械 (§6) と判定 (§5) を、パネルの語彙 (IDLE/ARMED/PULLBACK/TRIGGERED・3本柱) に
**写像するだけ**で、ここで新たな判定はしない。

状態の写像:
  IDLE / REJECTED / CLOSED          → IDLE      (待機・撤去済み・決済済み)
  PLACED / PULLED_EVENT / PULLED_WEEKEND → ARMED  (敷設済み・一時撤去)
  AT_POI                             → PULLBACK  (帯に到達、15分確定ごとに判定中)
  CONFIRMED / FILLED_UNCONFIRMED / MANAGE / EXIT_PLAN → TRIGGERED (「消さなくてよい」〜約定後。GO but WAIT)
3本柱 (名前は entry_state.json 側で上書き。パネルは表示のみ):
  帯・水準 / 吸収 (a·d) / 前進÷ATR (b·c)
regime: コンソールはレジームを判定しない。ダッシュボード自身の簡易判定 (SPEC §4 Bearing) を写し、確信度は未算出 (0)。
"""

from __future__ import annotations

import datetime as dt
from typing import Sequence

import calc

from .confirm import Diagnosis, VERDICT_LABEL
from .flow import TODO_TEXT, FlowState
from .ledger import Ledger

STATE_MAP = {
    "IDLE": "IDLE", "REJECTED": "IDLE", "CLOSED": "IDLE",
    "PLACED": "ARMED", "PULLED_EVENT": "ARMED", "PULLED_WEEKEND": "ARMED",
    "AT_POI": "PULLBACK",
    "CONFIRMED": "TRIGGERED", "FILLED_UNCONFIRMED": "TRIGGERED", "MANAGE": "TRIGGERED", "EXIT_PLAN": "TRIGGERED",
}
SIDE_MAP = {"S": "SHORT", "L": "LONG"}

BEARING_LOOKBACK = 24      # SPEC §4 Bearing: EMA20 の 24 本勾配
BEARING_MIN_SLOPE = 0.15   # |slope| < 0.15% → レンジ
BEARING_CHOP_RANGE = 61.8  # CHOP ≥ 61.8 → レンジ
BEARING_CHOP_TREND = 50.0  # CHOP < 50 → トレンド (それ以外はチョッピー)


def _f(x, nd=0):
    return "—" if x is None else f"{x:,.{nd}f}"


def dashboard_regime(candles: Sequence[dict]) -> dict:
    """ダッシュボードと同じ簡易判定 (SPEC §4 Bearing) を regime 3択へ写す。閾値は SPEC の値そのまま。"""
    if len(candles) < BEARING_LOOKBACK + 20:
        return {"regime": "range", "regime_confidence": 0.0,
                "regime_note": "ダッシュボードの足データ待ち。コンソールはレジームを判定しない"}
    closes = [c["c"] for c in candles]
    e20 = calc.ema(closes, 20)
    price = closes[-1]
    slope = (e20[-1] - e20[-1 - BEARING_LOOKBACK]) / price * 100
    chop = calc.choppiness(candles, 14)
    if chop >= BEARING_CHOP_RANGE or abs(slope) < BEARING_MIN_SLOPE:
        regime, label = "range", "レンジ / 方向感なし"
    elif slope > 0:
        regime, label = "uptrend", ("上昇トレンド" if chop < BEARING_CHOP_TREND else "チョッピー上昇")
    else:
        regime, label = "downtrend", ("下落トレンド" if chop < BEARING_CHOP_TREND else "チョッピー下落")
    return {
        "regime": regime, "regime_confidence": 0.0,
        "regime_note": f"{label} (ダッシュボードと同じ簡易判定: EMA20 勾配 {slope:+.2f}% × CHOP {chop:.1f})。"
                       "コンソールはレジームを判定せず、確信度は未算出",
    }


def build_entry_state(
    flow: FlowState, latest: Diagnosis | None, ledger: Ledger, price: float | None,
    atr15: float | None, candles: Sequence[dict], now_ms: int, bands_view: Sequence[dict] | None = None,
) -> dict:
    """entry_state.json の中身 (SPEC §7 必須フィールドを常に出す)。"""
    state = STATE_MAP.get(flow.state, "IDLE")
    direction = SIDE_MAP.get(flow.side) if flow.side else None
    if direction is None and latest and latest.verdict != "silent" and latest.verdict_side:
        direction = SIDE_MAP[latest.verdict_side]
    since_ms = flow.since_utc or now_ms
    since = dt.datetime.fromtimestamp(since_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 柱1: 帯・水準
    bands = list(bands_view or [])
    b = next((x for x in bands if x["id"] == flow.band_id), None) if flow.band_id else None
    if b:
        d = b.get("distance") or {}
        ok = bool(b.get("active"))
        val = f"{b['id']} {_f(b['lo'])}–{_f(b['hi'])} · 距離 {_f(d.get('usd'))} ({_f(d.get('atr'), 1)} ATR)" + ("" if ok else " · inactive (要記入あり)")
    else:
        act = [x for x in bands if x.get("active")]
        ok = False
        val = f"帯未選択 · 有効な帯 {len(act)}/{len(bands)}" + (f" ({', '.join(x['id'] for x in act)})" if act else "")
    pillar_band = {"name": "帯・水準", "ok": ok, "value": val}

    # 柱2 / 柱3: 最新確定足の判定 (flow の側 → 判定側 → Σδ の向き)
    if latest:
        side = flow.side or latest.verdict_side or ("S" if latest.S.dir_ok else "L")
        sd = latest.S if side == "S" else latest.L
        pillar_ad = {"name": "吸収 (a·d)", "ok": bool(sd.a and sd.d),
                     "value": f"Σδ {sd.sum_delta:+,.0f} · 攻め比率 {sd.ratio:.3f} · 向き {'✓' if sd.dir_ok else '✗'} · 床 {'✓' if sd.floor_ok else '✗'} · (d) {'✓' if sd.d else '✗'}"}
        ref = latest.ref_s if side == "S" else latest.ref_l
        pillar_bc = {"name": "前進÷ATR (b·c)", "ok": bool(sd.b and sd.c),
                     "value": f"前進 {_f(sd.prog)} / ATR {_f(latest.atr, 1)} = {_f(sd.prog_atr, 2)} {'✓' if sd.b else '✗'} · (c) {'✓' if sd.c else '✗'}" + (f" (ref {_f(ref, 1)})" if ref else " (ref 無効)")}
        verdict_txt = f"{VERDICT_LABEL[latest.verdict]} ({latest.labels()['close_local']} 確定)"
    else:
        pillar_ad = {"name": "吸収 (a·d)", "ok": False, "value": "確定足の判定待ち"}
        pillar_bc = {"name": "前進÷ATR (b·c)", "ok": False, "value": "確定足の判定待ち"}
        verdict_txt = "判定なし"

    if flow.suggested:
        nxt = f"[{flow.state}] 提案 → {flow.suggested['to']}: {flow.suggested['reason']} (人間がチェック)"
    else:
        nxt = f"[{flow.state}] {TODO_TEXT.get(flow.state, '')} · 最新判定: {verdict_txt}"

    out = {
        "state": state, "direction": direction, "since": since,
        "pillars": {"structure": pillar_band, "cvd": pillar_ad, "oi": pillar_bc},
        "next_condition": nxt,
        **dashboard_regime(candles),
        "console": {"state": flow.state, "band_id": flow.band_id, "verdict": latest.verdict if latest else None,
                    "price": price, "atr15": atr15, "written_at": now_ms},
    }
    # setup: 敷設済み以降で建値/SL/TP が揃っていれば指定値として渡す (無ければダッシュボードは機械式にフォールバック)
    if flow.avg_entry is not None and flow.sl is not None and flow.tp is not None and flow.state not in ("IDLE", "REJECTED", "CLOSED"):
        out["setup"] = {"side": SIDE_MAP.get(flow.side), "entry": flow.avg_entry, "stop": flow.sl, "targets": [flow.tp],
                        "note": f"セットアップコンソール {flow.state} · 帯 {flow.band_id} · SL は敷設時に固定"}
    return out
