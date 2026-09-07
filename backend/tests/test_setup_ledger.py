"""
test_setup_ledger.py — §3 台帳: 初期データ / 先読み防止 / 到達判定 / 永続化
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from setup_console import ledger as L
from setup_console.klines import Bar
from setup_console.tz import paris_to_ms


def bar(label: str, h: float, l: float, c: float | None = None) -> Bar:
    c = (h + l) / 2 if c is None else c
    return Bar(t=paris_to_ms(label), o=c, h=h, l=l, c=c, v=1.0, taker_buy=0.5)


# ---------------- 初期データ ----------------
def test_seed_levels_match_spec_and_pending_are_inactive():
    lm = {l.id: l for l in L.seed_levels()}
    assert [lid for lid in lm] == ["L01", "L02", "L03", "L04", "L05", "L06", "L07", "O01", "O02", "O03"]
    assert lm["L01"].price == 82923.4 and lm["L01"].valid_from == "2026-08-17" and lm["L01"].is_active()
    assert lm["L02"].valid_from == "2026-09-05" and lm["L02"].is_active()
    assert lm["L06"].valid_from == "2026-09-04" and lm["L06"].is_active()
    # 要記入 / 要確認 は人間が埋めるまで inactive
    for lid in ("L03", "L04", "L05", "L07", "O01", "O02", "O03"):
        assert not lm[lid].is_active(), lid
    assert lm["L04"].needs_input == ["born_on", "valid_from"]
    assert lm["L03"].needs_confirm == ["valid_from"]


def test_seed_bands_and_valid_from_from_components():
    lg = L.Ledger.seed()
    lm = lg.level_map()
    bands = {b.id: b for b in lg.bands}
    assert bands["P_UPPER"].sl_price == 83500 and bands["P_UPPER"].ref_level == 82300
    # P_UPPER は L03 (要確認) を含むため未確定 → inactive
    assert bands["P_UPPER"].valid_from(lm) is None and not bands["P_UPPER"].is_active(lm)
    assert bands["P_MID"].needs_input == ["ref_level", "sl_price"] and not bands["P_MID"].is_active(lm)
    # 人間が L03 を確定すると P_UPPER が有効化 (最も遅い valid_from = L02 の 09-05)
    lm["L03"].needs_confirm = []
    assert bands["P_UPPER"].valid_from(lm) == "2026-09-05"
    assert bands["P_UPPER"].is_active(lm, dt.date(2026, 9, 5))
    assert not bands["P_UPPER"].is_active(lm, dt.date(2026, 9, 4))


def test_level_is_active_respects_valid_from_and_status():
    lv = L.Level("X", 1.0, "x", "swing_high", "tier1", "2026-09-01", "2026-09-03")
    assert lv.is_active(dt.date(2026, 9, 3)) and not lv.is_active(dt.date(2026, 9, 2))
    lv.status = "broken"
    assert not lv.is_active()


def test_suggest_valid_from():
    assert L.suggest_valid_from("swing_high", "2026-09-03") == "2026-09-05"
    assert L.suggest_valid_from("swing_high", "2026-05") == "2026-08-17"
    assert L.suggest_valid_from("fib", None) is None


# ---------------- 到達判定 (§3.4) ----------------
def test_touch_first_bar_then_same_touch_until_three_bars_outside():
    tg = L.Target("P", 100, 110, "S", "2026-09-01")
    bars = [
        bar("2026-09-05 10:00", 95, 90),      # 未到達
        bar("2026-09-05 10:15", 105, 98),     # 到達 (跨ぐ)
        bar("2026-09-05 10:30", 108, 102),    # 帯内
        bar("2026-09-05 10:45", 98, 95),      # 帯外 1
        bar("2026-09-05 11:00", 104, 99),     # 帯内に戻る → 同一到達
        bar("2026-09-05 11:15", 97, 94),      # 帯外 1
        bar("2026-09-05 11:30", 96, 93),      # 帯外 2
        bar("2026-09-05 11:45", 95, 92),      # 帯外 3 → 離脱
        bar("2026-09-05 12:00", 103, 99),     # 再到達 → 新規
    ]
    spans = L.detect_touches(bars, [tg])
    assert len(spans) == 2
    assert spans[0].start_t == paris_to_ms("2026-09-05 10:15")
    assert spans[0].end_t == paris_to_ms("2026-09-05 11:45")
    assert spans[1].start_t == paris_to_ms("2026-09-05 12:00") and spans[1].end_t is None
    assert spans[0].side == "S"


def test_touch_level_side_from_approach_direction():
    tg = L.Target("L", 100, 100, None, "2026-09-01")
    from_below = [bar("2026-09-05 10:00", 99, 95, 98), bar("2026-09-05 10:15", 101, 99)]
    from_above = [bar("2026-09-05 10:00", 105, 101, 103), bar("2026-09-05 10:15", 101, 99)]
    assert L.detect_touches(from_below, [tg])[0].side == "S"
    assert L.detect_touches(from_above, [tg])[0].side == "L"


def test_lookahead_prevention_ignores_unborn_levels():
    tg = L.Target("P", 100, 110, "S", "2026-09-06")
    bars = [bar("2026-09-05 10:15", 105, 98), bar("2026-09-06 10:15", 105, 98)]
    spans = L.detect_touches(bars, [tg])
    assert len(spans) == 1 and spans[0].start_t == paris_to_ms("2026-09-06 10:15")
    assert L.detect_touches(bars, [L.Target("Q", 100, 110, "S", None)]) == []
    # enforce_valid=False (表示用) なら数える
    assert len(L.detect_touches(bars, [tg], enforce_valid=False)) == 1


def test_targets_from_ledger_uses_pending_as_unborn():
    lg = L.Ledger.seed()
    tg = {t.id: t for t in L.targets_from_ledger(lg)}
    assert tg["L01"].valid_from == "2026-08-17" and tg["L01"].side == "S"
    assert tg["L06"].side == "L"
    assert tg["L04"].valid_from is None
    assert tg["P_MID"].valid_from is None and tg["P_MID"].side == "L"


# ---------------- 永続化 ----------------
def test_ledger_seed_and_roundtrip(tmp_path):
    p = tmp_path / "ledger.json"
    lg = L.load_ledger(p)              # 不在 → seed 投入
    assert p.exists() and len(lg.levels) == 10 and len(lg.bands) == 3
    lg.touches.append(L.span_to_touch(L.TouchSpan("P_UPPER", "S", paris_to_ms("2026-09-05 10:15"), [1]), True, "T0001"))
    L.save_ledger(lg, p)
    again = L.load_ledger(p)
    assert again.touches[0].ts_local == "2026-09-05 10:15" and again.touches[0].target_id == "P_UPPER"
    assert not (tmp_path / "ledger.json.tmp").exists()   # アトミック書き込みの一時ファイルが残らない
    assert json.loads(p.read_text(encoding="utf-8"))["levels"][0]["id"] == "L01"
