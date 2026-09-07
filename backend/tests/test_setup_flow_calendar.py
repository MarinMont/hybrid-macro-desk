"""
test_setup_flow_calendar.py — §6 執行フロー / §7 カレンダーゲート
"""

from __future__ import annotations

import pytest

from setup_console import calendar_gate as cg, flow as F
from setup_console.tz import paris_to_ms

T0 = paris_to_ms("2026-09-08 10:00")


def placed() -> F.FlowState:
    fs = F.FlowState()
    return F.advance(fs, "PLACED", T0, arith_ok=True, gate_open=True, band_id="P_UPPER", side="S", sl=83500, avg_entry=81900)


# ---------------- 人間の遷移とガード ----------------
def test_idle_to_placed_requires_arith_ok_and_gate_open():
    with pytest.raises(F.TransitionError):
        F.advance(F.FlowState(), "PLACED", T0, arith_ok=False, gate_open=True, band_id="P_UPPER", side="S", sl=83500)
    with pytest.raises(F.TransitionError):
        F.advance(F.FlowState(), "PLACED", T0, arith_ok=True, gate_open=False, band_id="P_UPPER", side="S", sl=83500)
    fs = placed()
    assert fs.state == "PLACED" and fs.sl == 83500 and fs.history[-1]["by"] == "human"


def test_disallowed_transitions_raise():
    fs = placed()
    with pytest.raises(F.TransitionError):
        F.advance(fs, "MANAGE", T0)
    with pytest.raises(F.TransitionError):
        F.advance(fs, "NOPE", T0)


def test_every_state_has_todo_and_allowed_list():
    for s in F.STATES:
        fs = F.FlowState(state=s)
        d = fs.to_dict()
        assert d["todo"] and isinstance(d["allowed"], list)
    assert F.TODO_TEXT["PLACED"] == "指値は帯・SL・ロットの算術値で敷設済みか"
    assert F.TODO_TEXT["EXIT_PLAN"].startswith("次の戻りで建値±0.25×ATR")


def test_auto_touch_moves_placed_to_at_poi_only_for_flow_band():
    fs = placed()
    fs = F.on_touch(fs, "P_MID", T0 + 900_000)
    assert fs.state == "PLACED"
    fs = F.on_touch(fs, "P_UPPER", T0 - 900_000)   # 敷設前の足は無視
    assert fs.state == "PLACED"
    fs = F.on_touch(fs, "P_UPPER", T0 + 900_000)
    assert fs.state == "AT_POI" and fs.history[-1]["by"] == "auto"


def test_verdict_suggests_but_human_confirms():
    fs = placed()
    fs = F.on_touch(fs, "P_UPPER", T0 + 900_000)
    fs = F.on_verdict(fs, "fire", None, T0 + 1_800_000, 600.0)
    assert fs.state == "AT_POI" and fs.suggested["to"] == "CONFIRMED"
    fs = F.advance(fs, "CONFIRMED", T0 + 2_700_000)
    assert fs.state == "CONFIRMED" and fs.suggested is None
    fs = F.on_verdict(fs, "silent", "S", T0 + 3_600_000, 600.0)   # 受け入れ警告
    assert fs.suggested["to"] == "REJECTED"


def test_time_scratch_after_four_windows_is_automatic():
    fs = placed()
    fs = F.on_touch(fs, "P_UPPER", T0 + 900_000)
    fs = F.advance(fs, "FILLED_UNCONFIRMED", T0 + 1_800_000)
    for i in range(3):
        fs = F.on_verdict(fs, "silent", None, T0 + (3 + i) * 900_000, 600.0)
        assert fs.state == "FILLED_UNCONFIRMED"
    fs = F.on_verdict(fs, "silent", None, T0 + 6 * 900_000, 600.0)
    assert fs.state == "EXIT_PLAN" and fs.history[-1]["by"] == "auto"
    assert fs.exit_plan["price_upper"] == pytest.approx(81900 + 0.25 * 600)
    assert fs.exit_plan["price_lower"] == pytest.approx(81900 - 0.25 * 600)
    assert fs.exit_plan["timer_end_utc"] - (T0 + 7 * 900_000) == 3_600_000


def test_band_left_suggests_rejected():
    fs = placed()
    fs = F.on_touch(fs, "P_UPPER", T0 + 900_000)
    fs = F.on_band_left(fs, "P_UPPER")
    assert fs.suggested["to"] == "REJECTED"


def test_reset_clears_and_roundtrip(tmp_path):
    fs = placed()
    fs = F.advance(fs, "IDLE", T0 + 900_000)
    assert fs.band_id is None and fs.sl is None and fs.state == "IDLE"
    p = tmp_path / "flow.json"
    F.save_flow(fs, p)
    assert F.load_flow(p).history[-1]["to"] == "IDLE"
    assert F.load_flow(tmp_path / "none.json").state == "IDLE"


# ---------------- カレンダーゲート ----------------
CAL = {"events": list(cg.SEED["events"]), "holidays": list(cg.SEED["holidays"])}


def kinds(g):
    return [b["kind"] for b in g["badges"]]


def test_market_moving_window_before_2h_after_1h():
    assert not cg.gate(paris_to_ms("2026-09-10 12:29"), CAL)["badges"]
    g = cg.gate(paris_to_ms("2026-09-10 12:30"), CAL)
    assert kinds(g) == ["no_placement"] and not g["placement_allowed"]
    assert "PPI" in g["badges"][0]["text"]
    assert not cg.gate(paris_to_ms("2026-09-10 15:29"), CAL)["placement_allowed"]
    assert cg.gate(paris_to_ms("2026-09-10 15:30"), CAL)["placement_allowed"]   # 窓は [前2h, 後1h) の半開区間


def test_minor_event_does_not_block():
    g = cg.gate(paris_to_ms("2026-09-11 16:00"), CAL)
    # 同時刻は CPI (14:30) の後1h 窓の外 (15:30 まで) → ミシガンは minor なので開いている
    assert g["placement_allowed"]


def test_month_end_last_business_day_no_trade_and_notice():
    assert cg.last_business_day(2026, 9, CAL["holidays"]) .isoformat() == "2026-09-30"
    g = cg.gate(paris_to_ms("2026-09-30 10:00"), CAL)
    assert "no_trade" in kinds(g) and not g["placement_allowed"]
    g = cg.gate(paris_to_ms("2026-09-28 10:00"), CAL)     # 48h 前から予告
    assert "no_trade_notice" in kinds(g) and g["placement_allowed"]
    assert "no_trade_notice" not in kinds(cg.gate(paris_to_ms("2026-09-27 10:00"), CAL))
    # 月末が週末なら前の営業日 (2026-10-31 は土曜 → 10-30 金曜)
    assert cg.last_business_day(2026, 10, []).isoformat() == "2026-10-30"


def test_weekend_and_holiday_thin():
    assert kinds(cg.gate(paris_to_ms("2026-09-05 12:00"), CAL)) == ["thin"]      # 土曜
    g = cg.gate(paris_to_ms("2026-09-07 12:00"), CAL)                            # レイバーデー
    assert "thin" in kinds(g) and "レイバーデー" in g["badges"][0]["text"] and g["placement_allowed"]


def test_windows_next_72h_sorted():
    g = cg.gate(paris_to_ms("2026-09-09 10:00"), CAL, horizon_h=72)
    names = [w["name"] for w in g["windows"]]
    assert names[:3] == ["PPI（8月）", "CPI（8月）", "ミシガン消費者信頼感"]
    assert g["windows"][0]["start_local"] == "2026-09-10 12:30" and g["windows"][0]["end_local"] == "2026-09-10 15:30"


def test_calendar_seed_and_roundtrip(tmp_path):
    p = tmp_path / "calendar.json"
    c = cg.load_calendar(p)
    assert len(c["events"]) == 6 and p.exists()
    c["events"].append({"ts_local": "2026-10-02 14:30", "name": "雇用統計", "importance": "market_moving"})
    cg.save_calendar(c, p)
    assert len(cg.load_calendar(p)["events"]) == 7
