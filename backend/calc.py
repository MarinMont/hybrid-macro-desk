"""
calc.py — 純関数の計算ユーティリティ
====================================
参照実装 `frontend/hybrid-macro-desk-btc.jsx` の計算式を Python へ移植したもの。
**閾値・重みは仕様 (SPEC §4) の正であり、勝手に変えない。**

ここに置く関数は副作用を持たない純関数として切り出し、固定入力で JSX と
同一結果になることを pytest で検証する (tests/test_calc.py)。

collectors 側 (derivs) は cvd() / oi_change_pct_24h() / quadrant() を利用する。
CHOP / percentile / edge_factor は主にフロントで使うが、パリティ検証のためここにも置く。
"""

from __future__ import annotations

import math
from typing import Sequence


def js_round(x: float) -> int:
    """
    JavaScript Math.round と同一のセマンティクス (round half up / floor(x+0.5))。
    Python 組込み round() は銀行丸め (82.5→82) のため、JSXパリティにはこれを使う。
    """
    return math.floor(x + 0.5)


# ---------------- 基本統計 ----------------
def ema(vals: Sequence[float], p: int) -> list[float]:
    """指数移動平均。JSX ema() と同一。"""
    if not vals:
        return []
    k = 2 / (p + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def percentile_rank(arr: Sequence[float], val: float) -> float:
    """arr の中で val 以下の割合 (%)。空配列は 50。JSX percentileRank() と同一。"""
    if not arr:
        return 50.0
    c = sum(1 for x in arr if x <= val)
    return (100 * c) / len(arr)


def choppiness(candles: Sequence[dict], n: int = 14) -> float:
    """
    Choppiness Index(14)。1h足。JSX choppiness() と同一。
    candles は末尾が最新の {o,h,l,c} 辞書列。
    """
    seg = list(candles[-(n + 1):])
    if len(seg) < n + 1:
        return 50.0
    atr_sum = 0.0
    for i in range(1, len(seg)):
        h, l, pc = seg[i]["h"], seg[i]["l"], seg[i - 1]["c"]
        atr_sum += max(h - l, abs(h - pc), abs(l - pc))
    body = seg[1:]
    hh = max(c["h"] for c in body)
    ll = min(c["l"] for c in body)
    if hh - ll <= 0:
        return 100.0
    return (100 * math.log10(atr_sum / (hh - ll))) / math.log10(n)


def bb_width_series(closes: Sequence[float], p: int = 20) -> list[float]:
    """Bollinger Band 幅 (4σ/SMA) の系列。JSX bbWidthSeries() と同一。"""
    out: list[float] = []
    for i in range(p - 1, len(closes)):
        win = closes[i - p + 1:i + 1]
        m = sum(win) / p
        sd = math.sqrt(sum((b - m) ** 2 for b in win) / p)
        out.append((4 * sd) / m)
    return out


# ---------------- デリバティブ変換 (API_DESIGN §3) ----------------
def taker_delta_usd(buy_vol: float, sell_vol: float, price: float) -> float:
    """テイカーデルタ近似: (buyVol - sellVol) を USD 換算。"""
    return (buy_vol - sell_vol) * price


def cvd(taker_series: Sequence[float]) -> list[float]:
    """テイカーデルタの累積和 = 累積出来高デルタ (CVD)。JSX の CVD 構築と同一。"""
    acc = 0.0
    out: list[float] = []
    for v in taker_series:
        acc += v
        out.append(acc)
    return out


def aggtrade_delta(trades: Sequence[dict]) -> dict:
    """
    Binance aggTrades のリストからテイカーデルタを計算する (takerlongshortRatioの近似より精緻)。
    各 trade は {"p": price, "q": qty, "m": isBuyerMaker} を持つ。
      - m == False → 買い手がテイカー(攻撃的な買い) → buy
      - m == True  → 買い手がメイカー ⇒ 売り手がテイカー(攻撃的な売り) → sell
    返り値: {buyBase, sellBase, buyUsd, sellUsd, deltaBase, deltaUsd, count}
    USD換算は price×qty (quote volume)。
    """
    buy_base = sell_base = buy_usd = sell_usd = 0.0
    n = 0
    for t in trades:
        q = float(t["q"])
        p = float(t["p"])
        usd = p * q
        if t.get("m"):          # buyer is maker → taker is the SELLER
            sell_base += q
            sell_usd += usd
        else:                   # buyer is the taker (aggressive buy)
            buy_base += q
            buy_usd += usd
        n += 1
    return {
        "buyBase": buy_base,
        "sellBase": sell_base,
        "buyUsd": buy_usd,
        "sellUsd": sell_usd,
        "deltaBase": buy_base - sell_base,
        "deltaUsd": buy_usd - sell_usd,
        "count": n,
    }


def oi_change_pct_24h(oi_hist: Sequence[float]) -> float | None:
    """
    oiChangePct24h = (oiHist[-1] - oiHist[-25]) / oiHist[-25] * 100。
    1h足 48本前提。25本前 = 24h前。データ不足や 0 除算は None。
    """
    if len(oi_hist) < 25:
        return None
    base = oi_hist[-25]
    if not base:
        return None
    return (oi_hist[-1] - base) / base * 100


def quadrant(price_chg_pct: float, oi_chg_pct: float) -> dict:
    """
    OI×価格 4象限判定。JSX Positioning セクションと同一の分類。
    返り値: {key, label} — 文言はフロントが正なので key のみ厳密一致を保証する。
    """
    px_up = price_chg_pct >= 0
    oi_up = oi_chg_pct >= 0
    if px_up and oi_up:
        return {"key": "LB", "label": "新規ロング主導"}
    if px_up and not oi_up:
        return {"key": "SC", "label": "ショートカバー主導"}
    if not px_up and oi_up:
        return {"key": "SB", "label": "新規ショート主導"}
    return {"key": "LL", "label": "ロング投げ"}


# ---------------- Edge Factor (SPEC §4) ----------------
def edge_factor(chop: float, aligned: bool, bb_pct: float, vol_pct: float, fng_val: float) -> int:
    """
    Edge Factor 合成スコア。重み 30/25/20/15/10。JSX deriveMetrics() と同一。
    """
    chop_score = max(0.0, min(100.0, 100 - chop))
    align_score = 90 if aligned else 35
    vol_regime_score = 45 if bb_pct < 25 else 90 if bb_pct <= 75 else 40
    flow_score = 50 if vol_pct < 30 else 90 if vol_pct <= 70 else 55
    mood_score = 40 if (fng_val <= 10 or fng_val >= 90) else 75
    return js_round(
        0.30 * chop_score
        + 0.25 * align_score
        + 0.20 * vol_regime_score
        + 0.15 * flow_score
        + 0.10 * mood_score
    )
