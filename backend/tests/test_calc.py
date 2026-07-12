"""
test_calc.py — 純関数のパリティ/不変条件テスト
参照JSX (hybrid-macro-desk-btc.jsx) の計算式と同一結果になることを固定入力で検証する。
"""

import math

import pytest

import calc


# ---------------- js_round (Math.round パリティ) ----------------
@pytest.mark.parametrize("x,expected", [
    (82.5, 83),   # JS Math.round(82.5) === 83 (Python round は 82)
    (0.5, 1),
    (-0.5, 0),    # JS Math.round(-0.5) === 0
    (-1.5, -1),
    (2.4, 2),
    (2.6, 3),
])
def test_js_round(x, expected):
    assert calc.js_round(x) == expected


# ---------------- percentile_rank ----------------
def test_percentile_rank_basic():
    assert calc.percentile_rank([1, 2, 3, 4], 3) == 75.0
    assert calc.percentile_rank([1, 2, 3, 4], 4) == 100.0
    assert calc.percentile_rank([1, 2, 3, 4], 0.5) == 0.0


def test_percentile_rank_empty():
    assert calc.percentile_rank([], 5) == 50.0


# ---------------- ema ----------------
def test_ema_matches_formula():
    vals = [10, 12, 14]
    out = calc.ema(vals, 2)  # k = 2/3
    k = 2 / 3
    e = 10.0
    exp = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        exp.append(e)
    assert out == pytest.approx(exp)


# ---------------- choppiness ----------------
def test_choppiness_trending_low():
    # 単調上昇 (トレンド) は低CHOP寄りになる
    candles = [{"o": p, "h": p + 1, "l": p - 1, "c": p} for p in range(100, 130)]
    chop = calc.choppiness(candles, 14)
    assert 0 <= chop <= 100


def test_choppiness_flat_returns_100():
    # 高値=安値でレンジ幅0 → 100 (JSX: hh-ll<=0 → 100)
    candles = [{"o": 100, "h": 100, "l": 100, "c": 100} for _ in range(20)]
    assert calc.choppiness(candles, 14) == 100.0


def test_choppiness_insufficient_returns_50():
    candles = [{"o": 100, "h": 101, "l": 99, "c": 100} for _ in range(5)]
    assert calc.choppiness(candles, 14) == 50.0


def test_choppiness_exact_small_case():
    # 手計算による固定入力パリティ (n=2)
    candles = [
        {"o": 10, "h": 11, "l": 9, "c": 10},
        {"o": 10, "h": 12, "l": 10, "c": 11},
        {"o": 11, "h": 13, "l": 11, "c": 12},
    ]
    # TR1 = max(12-10, |12-10|, |10-10|) = 2 ; TR2 = max(13-11,|13-11|,|11-11|)=2 ; atrSum=4
    # body = last 2 → hh=13, ll=10 → range=3
    # 100*log10(4/3)/log10(2)
    exp = 100 * math.log10(4 / 3) / math.log10(2)
    assert calc.choppiness(candles, 2) == pytest.approx(exp)


# ---------------- bb_width_series ----------------
def test_bb_width_series_length():
    closes = list(range(1, 31))
    out = calc.bb_width_series(closes, 20)
    assert len(out) == len(closes) - 20 + 1


# ---------------- cvd ----------------
def test_cvd_cumulative():
    assert calc.cvd([1, 2, 3]) == [1, 3, 6]
    assert calc.cvd([10, -4, 2]) == [10, 6, 8]
    assert calc.cvd([]) == []


def test_taker_delta_usd():
    assert calc.taker_delta_usd(100, 40, 60000) == 60 * 60000


# ---------------- aggtrade_delta (Binance aggTrades) ----------------
def test_aggtrade_delta_basic():
    # m=False → テイカー買い, m=True → テイカー売り
    trades = [
        {"p": "100", "q": "2", "m": False},   # buy  200
        {"p": "100", "q": "1", "m": True},    # sell 100
        {"p": "200", "q": "0.5", "m": False}, # buy  100
    ]
    d = calc.aggtrade_delta(trades)
    assert d["buyBase"] == 2.5
    assert d["sellBase"] == 1.0
    assert d["buyUsd"] == 300.0
    assert d["sellUsd"] == 100.0
    assert d["deltaUsd"] == 200.0
    assert d["deltaBase"] == 1.5
    assert d["count"] == 3


def test_aggtrade_delta_empty():
    d = calc.aggtrade_delta([])
    assert d == {
        "buyBase": 0.0, "sellBase": 0.0, "buyUsd": 0.0, "sellUsd": 0.0,
        "deltaBase": 0.0, "deltaUsd": 0.0, "count": 0,
    }


def test_aggtrade_delta_all_sell():
    trades = [{"p": "50000", "q": "1", "m": True}, {"p": "50000", "q": "0.4", "m": True}]
    d = calc.aggtrade_delta(trades)
    assert d["buyUsd"] == 0.0
    assert d["deltaUsd"] == -70000.0


# ---------------- oi_change_pct_24h ----------------
def test_oi_change_pct_24h():
    hist = [100.0] * 48
    hist[-25] = 100.0
    hist[-1] = 110.0
    assert calc.oi_change_pct_24h(hist) == pytest.approx(10.0)


def test_oi_change_pct_24h_insufficient():
    assert calc.oi_change_pct_24h([1, 2, 3]) is None


def test_oi_change_pct_24h_zero_base():
    hist = [0.0] * 48
    hist[-1] = 5.0
    assert calc.oi_change_pct_24h(hist) is None


# ---------------- quadrant ----------------
@pytest.mark.parametrize("px,oi,key", [
    (1.0, 1.0, "LB"),    # ↑↑ 新規ロング
    (1.0, -1.0, "SC"),   # ↑↓ ショートカバー
    (-1.0, 1.0, "SB"),   # ↓↑ 新規ショート
    (-1.0, -1.0, "LL"),  # ↓↓ ロング投げ
    (0.0, 0.0, "LB"),    # 0は「以上」でロング側 (JSX: >=0)
])
def test_quadrant(px, oi, key):
    assert calc.quadrant(px, oi)["key"] == key


# ---------------- edge_factor ----------------
def test_edge_factor_exact_banker_boundary():
    # chop=30, aligned, bb=50, vol=50, fng=38
    # 0.3*70 + 0.25*90 + 0.2*90 + 0.15*90 + 0.1*75 = 21+22.5+18+13.5+7.5 = 82.5
    # JS Math.round(82.5)=83 (Python round は 82 になるので js_round が要る)
    assert calc.edge_factor(chop=30, aligned=True, bb_pct=50, vol_pct=50, fng_val=38) == 83


def test_edge_factor_low_clarity():
    # チョッピー(高CHOP) + 不整列 + WILD + CROWDED + 極端センチメント → 低スコア
    e = calc.edge_factor(chop=80, aligned=False, bb_pct=90, vol_pct=90, fng_val=5)
    # 0.3*20 + 0.25*35 + 0.2*40 + 0.15*55 + 0.1*40 = 6+8.75+8+8.25+4 = 35
    assert e == 35


def test_edge_factor_clamps_chop():
    # chop>100 でも chop_score は 0 でクランプ
    e = calc.edge_factor(chop=120, aligned=True, bb_pct=50, vol_pct=50, fng_val=50)
    # chop_score=0 → 0 + 22.5 + 18 + 13.5 + 7.5 = 61.5 → 62
    assert e == 62
