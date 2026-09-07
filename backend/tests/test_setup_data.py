"""
test_setup_data.py — §2 データ層 (klines / δ / ATR / tz / 校正値ファイル)
"""

from __future__ import annotations

import json

import pytest

from setup_console import atr, calibration as cal, config, klines, tz
from setup_console.klines import Bar


def mk(t, o, h, l, c, v, tb):
    return Bar(t=t, o=o, h=h, l=l, c=c, v=v, taker_buy=tb)


# ---------------- klines パース ----------------
def test_parse_klines_binance_row():
    raw = [[1757000000000, "80000.0", "80100.0", "79900.0", "80050.0", "123.456", 1757000899999,
            "9876543.21", 1000, "70.0", "5600000.0", "0"]]
    bars = klines.parse_klines(raw)
    assert len(bars) == 1
    b = bars[0]
    assert (b.t, b.o, b.h, b.l, b.c, b.v, b.taker_buy) == (1757000000000, 80000.0, 80100.0, 79900.0, 80050.0, 123.456, 70.0)


def test_parse_klines_skips_bad_rows():
    raw = [[1, "x", "1", "1", "1", "1", 2, "1", 1, "1", "1", "0"], [1, "1"], None]
    assert klines.parse_klines(raw) == []


def test_only_closed_excludes_forming_bar():
    bars = [mk(0, 1, 1, 1, 1, 1, 0), mk(900_000, 1, 1, 1, 1, 1, 0)]
    assert [b.t for b in klines.only_closed(bars, now_ms=1_000_000, interval_ms=900_000)] == [0]
    assert [b.t for b in klines.only_closed(bars, now_ms=1_800_000, interval_ms=900_000)] == [0, 900_000]


# ---------------- δ 2方式 ----------------
def test_delta_taker_formula():
    b = mk(0, 1, 1, 1, 1, v=100.0, tb=70.0)
    assert klines.delta_taker(b) == pytest.approx(2 * 70 - 100)   # +40
    assert klines.deltas([b], "taker") == [pytest.approx(40.0)]


def test_delta_reconstruct_sums_signed_1m_volumes():
    t0 = 1_000_000 * 900  # 15分境界
    b15 = mk(t0, 1, 1, 1, 1, 0, 0)
    bars1 = []
    for i in range(15):
        # 上昇足 +10, 下落足 −10, 同値 0 を交互に
        o, c = (1, 2) if i % 3 == 0 else (2, 1) if i % 3 == 1 else (1, 1)
        bars1.append(mk(t0 + i * 60_000, o, 2, 1, c, 10.0, 0))
    d = klines.delta_reconstruct(b15, bars1)
    assert d == pytest.approx(5 * 10 - 5 * 10 + 0)   # 5本上昇, 5本下落, 5本同値


def test_delta_reconstruct_requires_15_bars():
    b15 = mk(0, 1, 1, 1, 1, 0, 0)
    bars1 = [mk(i * 60_000, 1, 2, 1, 2, 1.0, 0) for i in range(14)]
    assert klines.delta_reconstruct(b15, bars1) is None
    assert klines.deltas([b15], "reconstruct", bars1) == [None]


def test_deltas_unknown_method():
    with pytest.raises(ValueError):
        klines.deltas([], "candle_color")


# ---------------- 出来高中央値 ----------------
def test_volume_median_window():
    bars = [mk(i, 1, 1, 1, 1, float(i), 0) for i in range(100)]
    assert klines.volume_median(bars, 96) == pytest.approx((51 + 52) / 2)   # 4..99 の中央値
    assert klines.volume_median(bars[:50], 96) is None


# ---------------- ATR (Wilder RMA) ----------------
def test_true_range_first_bar_is_hl():
    bars = [mk(0, 10, 12, 9, 11, 1, 0), mk(1, 11, 15, 10, 14, 1, 0)]
    tr = atr.true_range(bars)
    assert tr[0] == 3
    assert tr[1] == max(15 - 10, abs(15 - 11), abs(10 - 11))


def test_rma_seed_sma_then_wilder():
    vals = [1, 2, 3, 4, 10]
    out = atr.rma(vals, 4)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(2.5)                      # SMA(1,2,3,4)
    assert out[4] == pytest.approx((2.5 * 3 + 10) / 4)       # Wilder
    # EMA(alpha=2/(n+1)) とは一致しない (RMA であることの確認)
    assert out[4] != pytest.approx(10 * 0.4 + 2.5 * 0.6)


def test_atr_constant_range_converges_to_range():
    bars = [mk(i, 100, 101, 99, 100, 1, 0) for i in range(60)]
    s = atr.atr_series(bars, 14)
    assert s[12] is None
    assert s[13] == pytest.approx(2.0)
    assert atr.atr_last(bars, 14) == pytest.approx(2.0)


# ---------------- tz ----------------
def test_paris_conversion_cest():
    import datetime as dt
    ms = int(dt.datetime(2026, 9, 3, 21, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)  # 21:30Z = 23:30 CEST
    assert tz.paris_label(ms) == "2026-09-03 23:30"
    assert tz.paris_to_ms("2026-09-03 23:30") == ms
    labels = tz.bar_labels(ms, "15m")
    assert labels["close_local"] == "2026-09-03 23:45"


def test_paris_conversion_cet_after_dst_end():
    # 2026-10-25 に CEST→CET。11月は UTC+1
    ms = tz.paris_to_ms("2026-11-10 14:30")
    assert tz.to_paris(ms).utcoffset().total_seconds() == 3600
    assert tz.paris_label(ms) == "2026-11-10 14:30"


# ---------------- 校正値ファイル (上書き禁止のガード) ----------------
def test_confirm_v033_values_are_canonical():
    c = config.load_config("v0.3.3")
    assert c.to_dict() == {
        "version": "v0.3.3", "timeframe": "15m", "window_bars": 3, "x_ratio": 0.05,
        "atr_coef": 0.75, "atr_len": 14, "vol_floor_mult": 3.0, "vol_median_len": 96,
        "accept_consecutive": 2, "delta_method": "taker",
    }
    assert "v0.3.3" in config.list_versions()


def test_config_rejects_other_timeframe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    bad = {"version": "vX", "timeframe": "1h", "window_bars": 3, "x_ratio": 0.05, "atr_coef": 0.75, "atr_len": 14,
           "vol_floor_mult": 3.0, "vol_median_len": 96, "accept_consecutive": 2, "delta_method": "taker"}
    (tmp_path / "confirm_vX.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        config.load_config("vX")


# ---------------- 校正場面の定義が整合していること ----------------
@pytest.mark.parametrize("scene", list(cal.SCENES.values()), ids=lambda s: s.id)
def test_scene_bars_are_15m_aligned_and_expected_labels_exist(scene):
    ts = [b.t for b in scene.bars]
    assert all(t % 900_000 == 0 for t in ts)
    assert all(b - a == 900_000 for a, b in zip(ts, ts[1:]))
    labels = {b.label for b in scene.bars}
    assert set(scene.expected) <= labels
