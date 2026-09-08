"""
test_setup_replay.py — §9 リプレイ: Touch 行の自動生成 / 先読み防止 / +16 本の経路 / CSV
"""

from __future__ import annotations

from setup_console import config, ledger as L, replay as R
from setup_console.klines import Bar
from setup_console.tz import paris_to_ms

CFG = config.load_config("v0.3.3")


def bars_with_touch(touch_label: str, n_before: int = 120, level: float = 82923.4) -> list[Bar]:
    t_touch = paris_to_ms(touch_label)
    out = []
    for i in range(-n_before, 20):
        t = t_touch + i * 900_000
        o = 80_000 + (i % 5) * 10
        c = o + (5 if i % 2 else -5)
        h, l = max(o, c) + 40, min(o, c) - 40
        if i == 0:
            h = level + 20        # 到達足
        out.append(Bar(t=t, o=o, h=h, l=l, c=c, v=1000 + (i % 3) * 50, taker_buy=500))
    return out


def test_replay_generates_touch_rows_with_path():
    lg = L.Ledger.seed()
    bars = bars_with_touch("2026-09-05 10:00")
    rows = R.replay(bars, CFG, lg, paris_to_ms("2026-09-05 00:00"), paris_to_ms("2026-09-06 00:00"))
    l01 = [r for r in rows if r["target_id"] == "L01"]
    assert len(l01) == 1
    r = l01[0]
    assert r["ts_local"] == "2026-09-05 10:00" and r["side"] == "S" and r["valid_check"]
    assert r["verdict"] in ("confirmed", "rejected_c", "silent")
    assert len(r["path_16_close"]) == 16 and r["outcome_4h"] is None
    assert r["id"].startswith("R")


def test_replay_enforces_valid_from():
    lg = L.Ledger.seed()
    # L01 は 2026-08-17 から有効 → 08-10 の到達は数えない
    bars = bars_with_touch("2026-08-10 10:00")
    rows = R.replay(bars, CFG, lg, paris_to_ms("2026-08-01 00:00"), paris_to_ms("2026-08-31 00:00"))
    assert not [r for r in rows if r["target_id"] == "L01"]
    # 要記入の L04 (81,270.5) は誕生していないので跨いでも出ない
    bars = bars_with_touch("2026-09-05 10:00", level=81270.5)
    rows = R.replay(bars, CFG, lg, paris_to_ms("2026-09-05 00:00"), paris_to_ms("2026-09-06 00:00"))
    assert not [r for r in rows if r["target_id"] == "L04"]


def test_replay_range_filter_and_csv():
    lg = L.Ledger.seed()
    bars = bars_with_touch("2026-09-05 10:00")
    assert R.replay(bars, CFG, lg, paris_to_ms("2026-09-06 00:00"), paris_to_ms("2026-09-07 00:00")) == []
    rows = R.replay(bars, CFG, lg, paris_to_ms("2026-09-05 00:00"), paris_to_ms("2026-09-06 00:00"))
    csv_text = R.to_csv(rows)
    head = csv_text.splitlines()[0].split(",")
    assert head[:6] == ["id", "ts_local", "ts_utc", "target_id", "side", "valid_check"] and head[-1] == "path_16_close"
    assert len(csv_text.splitlines()) == 1 + len(rows)
