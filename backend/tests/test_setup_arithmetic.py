"""
test_setup_arithmetic.py — §4 算術 / §11-4 再現
9/5 の帯ショート (81,500/81,900/82,300、SL 83,500、TP 77,450、残高 10,000、リスク 2%)
→ R:R 2.78・合計ロット 0.1250 BTC
"""

from __future__ import annotations

import pytest

from setup_console.arithmetic import ArithInput, EntryLeg, check


def base(**kw):
    d = dict(
        balance_usd=10_000, risk_pct=2, side="S",
        entries=[EntryLeg(81500), EntryLeg(81900), EntryLeg(82300)],
        sl=83500, tp=77450, atr_1h=600.0,
    )
    d.update(kw)
    return ArithInput(**d)


def rows(res):
    return {r.key: r for r in res.rows}


def test_section_11_4_reproduction():
    res = check(base())
    assert res.avg_entry == pytest.approx(81900)
    assert res.sl_width == pytest.approx(1600)
    assert res.rr == pytest.approx(2.78, abs=0.005)
    assert res.max_lot == pytest.approx(0.1250)
    assert res.total_lot == pytest.approx(0.1250)
    assert res.lots == pytest.approx([0.1250 / 3] * 3)
    assert not res.placement_blocked
    assert rows(res)["rr"].value == "2.78"
    assert rows(res)["max_lot"].value == "0.1250 BTC"


def test_sl_width_violation_under_2x_atr():
    res = check(base(atr_1h=900.0))        # 2×900 = 1800 > 1600
    assert rows(res)["sl_width"].violation and res.placement_blocked


def test_rr_violation():
    res = check(base(tp=79000))            # |79000−81900| / 1600 = 1.81
    assert rows(res)["rr"].violation


def test_lot_and_full_risk_violation_with_input_lots():
    res = check(base(entries=[EntryLeg(81500, lot=0.05), EntryLeg(81900, lot=0.05), EntryLeg(82300, lot=0.05)]))
    assert res.total_lot == pytest.approx(0.15)
    assert rows(res)["max_lot"].violation
    assert rows(res)["full_risk"].violation   # 0.15 × 1600 = 240 > 200


def test_sl_and_tp_position_vs_liq_bands():
    ok = check(base(liq_upper=83000, liq_lower=77000))   # SL 83500 > 83000 (外側), TP 77450 > 77000 (手前)
    assert not rows(ok)["sl_pos"].violation and not rows(ok)["tp_pos"].violation
    bad = check(base(liq_upper=84000, liq_lower=78000))  # SL が清算帯の内側 / TP が次の清算帯を越える
    assert rows(bad)["sl_pos"].violation and rows(bad)["tp_pos"].violation
    # ロング側の対称
    lng = check(base(side="L", entries=[EntryLeg(77100)], sl=76000, tp=81000, liq_lower=76500, liq_upper=80000))
    assert not rows(lng)["sl_pos"].violation and rows(lng)["tp_pos"].violation


def test_split_violation_when_band_too_thin():
    thin = check(base(atr_15m=300, band_lo=81500, band_hi=82000))   # 厚み 500 < 600
    assert rows(thin)["split"].violation
    thick = check(base(atr_15m=300, band_lo=81500, band_hi=82300))  # 800 ≥ 600
    assert not rows(thick)["split"].violation
    single = check(base(entries=[EntryLeg(81900)], atr_15m=300, band_lo=81500, band_hi=81600))
    assert not rows(single)["split"].violation and rows(single)["split"].value == "単発"


def test_weekend_atr_rejected():
    res = check(base(weekend_or_holiday=True, atr_source="today"))
    assert rows(res)["weekend_atr"].violation
    res = check(base(weekend_or_holiday=True, atr_source="last_full_session"))
    assert not rows(res)["weekend_atr"].violation


def test_no_rounding_of_design_values():
    res = check(base(entries=[EntryLeg(81500.7), EntryLeg(81900.3)], sl=83500.55))
    assert res.avg_entry == pytest.approx((81500.7 + 81900.3) / 2)
    assert res.sl_width == pytest.approx(abs(res.avg_entry - 83500.55))


def test_invalid_inputs():
    with pytest.raises(ValueError):
        check(base(side="X"))
    with pytest.raises(ValueError):
        check(base(entries=[]))
    with pytest.raises(ValueError):
        check(base(entries=[EntryLeg(83500)], sl=83500))
