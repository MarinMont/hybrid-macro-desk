"""
test_setup_bridge.py — entry_state.json への写像 (ダッシュボード Entry Engine 連携)
"""

from __future__ import annotations

import json

from setup_console import entry_bridge as EB, flow as F, ledger as L
from setup_console.calibration import S2
from setup_console.confirm import WindowBar, judge
from setup_console.config import load_config
from setup_console.tz import paris_to_ms

CFG = load_config()
NOW = paris_to_ms("2026-09-08 10:00")


def diag_fire():
    bars = [WindowBar(t=b.t, h=b.h, l=b.l, c=(b.h + b.l) / 2, v=b.v, delta=b.delta) for b in S2.bars]
    i = [b.t for b in bars].index(paris_to_ms("2026-09-04 00:00"))
    return judge([bars[i], bars[i - 1], bars[i - 2]], 344.2, CFG, "S", 82300, 0, None, floor_enabled=False)


def candles(trend: float, n: int = 120):
    out = []
    p = 80000.0
    for i in range(n):
        p += trend
        out.append({"t": i, "o": p, "h": p + 30, "l": p - 30, "c": p, "v": 1})
    return out


def bands_view(active=True):
    return [{"id": "P_UPPER", "lo": 81500, "hi": 82300, "active": active, "distance": {"usd": 1850.0, "atr": 5.4}},
            {"id": "P_MID", "lo": 77000, "hi": 77150, "active": False, "distance": {"usd": -2975.0, "atr": -8.7}}]


def test_state_and_direction_mapping():
    assert EB.STATE_MAP["IDLE"] == "IDLE" and EB.STATE_MAP["PLACED"] == "ARMED"
    assert EB.STATE_MAP["AT_POI"] == "PULLBACK" and EB.STATE_MAP["CONFIRMED"] == "TRIGGERED"
    assert EB.STATE_MAP["REJECTED"] == "IDLE" and EB.STATE_MAP["EXIT_PLAN"] == "TRIGGERED"
    assert set(EB.STATE_MAP) == set(F.STATES)


def test_build_idle_without_diag_has_required_fields():
    doc = EB.build_entry_state(F.FlowState(), None, L.Ledger.seed(), None, None, [], NOW, bands_view())
    for k in ("state", "direction", "since", "pillars", "next_condition", "regime", "regime_confidence", "regime_note"):
        assert k in doc
    assert doc["state"] == "IDLE" and doc["direction"] is None and "setup" not in doc
    assert set(doc["pillars"]) == {"structure", "cvd", "oi"}
    assert doc["pillars"]["structure"]["name"] == "帯・水準" and not doc["pillars"]["structure"]["ok"]
    assert "有効な帯 1/2" in doc["pillars"]["structure"]["value"]
    assert doc["regime"] == "range" and doc["regime_confidence"] == 0.0
    json.dumps(doc)   # シリアライズ可能


def test_build_at_poi_with_fire_and_setup():
    fs = F.advance(F.FlowState(), "PLACED", NOW - 900_000, arith_ok=True, gate_open=True, band_id="P_UPPER", side="S", sl=83500, avg_entry=81900, tp=77450)
    fs = F.on_touch(fs, "P_UPPER", NOW)
    fs = F.on_verdict(fs, "fire", None, NOW, 600.0)
    doc = EB.build_entry_state(fs, diag_fire(), L.Ledger.seed(), 81400.0, 344.2, candles(-40), NOW + 900_000, bands_view())
    assert doc["state"] == "PULLBACK" and doc["direction"] == "SHORT"
    assert doc["since"].endswith("Z")
    p = doc["pillars"]
    assert p["structure"]["ok"] and "P_UPPER" in p["structure"]["value"]
    assert p["cvd"]["ok"] and "Σδ +3,117" in p["cvd"]["value"]
    assert p["oi"]["ok"] and "ref 82,300" in p["oi"]["value"]
    assert "提案 → CONFIRMED" in doc["next_condition"]
    assert doc["setup"] == {"side": "SHORT", "entry": 81900, "stop": 83500, "targets": [77450],
                            "note": "セットアップコンソール AT_POI · 帯 P_UPPER · SL は敷設時に固定"}
    assert doc["regime"] == "downtrend"


def test_setup_omitted_without_tp_or_after_reset():
    fs = F.advance(F.FlowState(), "PLACED", NOW, arith_ok=True, gate_open=True, band_id="P_UPPER", side="S", sl=83500, avg_entry=81900)
    assert "setup" not in EB.build_entry_state(fs, None, L.Ledger.seed(), None, None, [], NOW, bands_view())
    fs = F.advance(fs, "IDLE", NOW)
    assert fs.tp is None


def test_dashboard_regime_follows_spec_bearing():
    assert EB.dashboard_regime(candles(+40))["regime"] == "uptrend"
    assert EB.dashboard_regime(candles(-40))["regime"] == "downtrend"
    flat = [{"t": i, "o": 80000, "h": 80030, "l": 79970, "c": 80000 + (5 if i % 2 else -5), "v": 1} for i in range(120)]
    assert EB.dashboard_regime(flat)["regime"] == "range"
    assert EB.dashboard_regime([])["regime"] == "range"
