"""
test_setup_collector.py — collectors/setup_console のオフライン検証
Binance はフェイク klines でモック。データ置き場は tmp。時刻は固定。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from collectors import setup_console as sc
from setup_console import flow as F, klines, ledger as LG, store
from setup_console.tz import paris_to_ms

NOW_MS = paris_to_ms("2026-09-08 10:07")   # 火曜 (ゲート開・平日)。09:45 足が最新確定、10:00 足は形成中


def synth_klines(n: int = 500, end_open_ms: int = NOW_MS - NOW_MS % 900_000, interval_ms: int = 900_000,
                 base: float = 80_000.0, touch_l01_at: int | None = None) -> list[list]:
    """穏やかなレンジの合成足。touch_l01_at の足だけ L01 (82,923.4) を跨がせる。"""
    out = []
    for i in range(n):
        t = end_open_ms - (n - 1 - i) * interval_ms
        o = base + (i % 7) * 20
        c = o + (10 if i % 2 else -10)
        h, l = max(o, c) + 60, min(o, c) - 60
        v, tb = 1500.0 + (i % 5) * 100, 800.0
        if touch_l01_at is not None and t == touch_l01_at:
            h = 82_950.0
        out.append([t, str(o), str(h), str(l), str(c), str(v), t + interval_ms - 1, "0", 100, str(tb), "0", "0"])
    return out


class FakeResp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d

    def raise_for_status(self):
        return None


CLOCK = {"ms": NOW_MS}   # テスト内で進められる固定時計


class FakeHTTP:
    def __init__(self, touch_at=None):
        self.touch_at = touch_at

    async def get(self, url, params=None, timeout=None):
        iv, now = params["interval"], CLOCK["ms"]
        if iv == "15m":
            return FakeResp(synth_klines(500, end_open_ms=now - now % 900_000, touch_l01_at=self.touch_at))
        if iv == "1h":
            return FakeResp(synth_klines(200, end_open_ms=now - now % 3_600_000, interval_ms=3_600_000))
        if iv == "1m":
            return FakeResp(synth_klines(1000, end_open_ms=now - now % 60_000, interval_ms=60_000))
        raise AssertionError(iv)


@pytest.fixture
def env(tmp_path, monkeypatch):
    CLOCK["ms"] = NOW_MS
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sc.time, "time", lambda: CLOCK["ms"] / 1000)
    sc._st.update({"bars15": [], "bars1": [], "bars1h": [], "latest": None, "provisional": None, "accept_events": {}, "error": None})
    app = FastAPI()
    app.include_router(sc.router)
    return TestClient(app)


async def _poll(http):
    sc.init(http, None, None)
    sc._st["bars15"] = await sc._fetch("15m", sc.LIMIT_15M)
    sc._st["bars1"] = await sc._fetch("1m", sc.LIMIT_1M)
    sc._st["bars1h"] = await sc._fetch("1h", sc.LIMIT_1H)
    sc._st["fetched_15m"] = sc.time.time()
    async with sc._lock:
        sc._evaluate(int(sc.time.time() * 1000))


def test_state_waiting_before_data(env):
    r = env.get("/api/setup/state")
    assert r.status_code == 200
    j = r.json()
    assert j["data"]["waiting"] and j["latest"] is None and j["version"] == "v0.3.4"
    assert j["flow"]["state"] == "IDLE" and j["gate"]["placement_allowed"]
    assert len(j["ledger"]["levels"]) == 10


def test_evaluate_populates_latest_history_and_provisional(env):
    asyncio.run(_poll(FakeHTTP()))
    j = env.get("/api/setup/state").json()
    assert not j["data"]["waiting"] and not j["data"]["stale"]
    assert j["latest"]["open_local"] == "2026-09-08 09:45" and j["latest"]["close_local"] == "2026-09-08 10:00"
    assert j["latest"]["verdict"] in ("fire", "rejected_c", "silent")
    assert [r["row"] for r in j["latest"]["rows"]][-1] == "判定"
    assert j["provisional"]["provisional"] and j["provisional"]["open_local"] == "2026-09-08 10:00"
    assert j["atr15"] and j["atr_1h"] and j["atr_1h_last_session"]
    assert len(j["history"]) > 0 and j["history"][-1]["open_utc_ms"] == j["latest"]["open_utc_ms"]
    assert j["history"][-1]["inputs"]["mode"] == "both"
    # 形成中の足は履歴に入らない
    assert all(h["open_utc_ms"] + 900_000 <= NOW_MS for h in j["history"])


def test_history_is_append_only_and_bounded(env):
    asyncio.run(_poll(FakeHTTP()))
    n1 = len(sc._history())
    asyncio.run(_poll(FakeHTTP()))
    assert len(sc._history()) == n1    # 同じ確定足は二重に追記しない


def test_inputs_post_reevaluates(env):
    asyncio.run(_poll(FakeHTTP()))
    r = env.post("/api/setup/inputs", json={"mode": "S", "ref_s": 82300})
    assert r.status_code == 200 and r.json()["inputs"]["mode"] == "S"
    assert env.get("/api/setup/state").json()["latest"]["mode"] == "S"
    assert env.post("/api/setup/inputs", json={"mode": "X"}).status_code == 400
    assert env.post("/api/setup/inputs", json={"version": "v9.9.9"}).status_code == 400


def test_arith_endpoint_reproduces_11_4(env):
    body = {"balance_usd": 10000, "risk_pct": 2, "side": "S",
            "entries": [{"price": 81500}, {"price": 81900}, {"price": 82300}], "sl": 83500, "tp": 77450, "atr_1h": 600}
    j = env.post("/api/setup/arith", json=body).json()
    assert round(j["rr"], 2) == 2.78 and round(j["max_lot"], 4) == 0.125 and not j["placement_blocked"]
    assert env.post("/api/setup/arith", json={**body, "side": "X"}).status_code == 400


def test_flow_advance_guards_and_auto_touch(env):
    asyncio.run(_poll(FakeHTTP()))
    r = env.post("/api/setup/flow/advance", json={"to": "PLACED", "arith_ok": False, "band_id": "L01", "side": "S", "sl": 83500})
    assert r.status_code == 400
    r = env.post("/api/setup/flow/advance", json={"to": "PLACED", "arith_ok": True, "band_id": "L01", "side": "S", "sl": 83500, "avg_entry": 81900})
    assert r.status_code == 200 and r.json()["state"] == "PLACED" and r.json()["todo"]
    # 敷設前の足 (09:30) が L01 を跨いでも、フローは動かない (到達行は台帳に残る)
    asyncio.run(_poll(FakeHTTP(touch_at=paris_to_ms("2026-09-08 09:30"))))
    assert env.get("/api/setup/state").json()["flow"]["state"] == "PLACED"
    # 時計を進め、敷設後の足 (10:15) が L01 (有効: 08-17) を跨ぐ → 台帳に到達行、フローは AT_POI (自動)
    CLOCK["ms"] = paris_to_ms("2026-09-08 10:37")
    asyncio.run(_poll(FakeHTTP(touch_at=paris_to_ms("2026-09-08 10:15"))))
    j = env.get("/api/setup/state").json()
    touches = [t for t in j["ledger"]["touches"] if t["target_id"] == "L01"]
    assert touches and touches[-1]["ts_local"] == "2026-09-08 10:15" and touches[-1]["side"] == "S" and touches[-1]["valid_check"]
    assert j["flow"]["state"] == "AT_POI" and j["flow"]["history"][-1]["by"] == "auto"
    # 人間の記入欄
    r = env.post("/api/setup/ledger/touch", json={"id": touches[-1]["id"], "outcome_4h": "flat", "note": "テスト"})
    assert r.status_code == 200 and r.json()["touch"]["outcome_4h"] == "flat"
    assert env.post("/api/setup/ledger/touch", json={"id": touches[-1]["id"], "outcome_4h": "meh"}).status_code == 400


def test_pending_levels_do_not_generate_touches(env):
    # L04 (要記入, 81,270.5) を跨いでも到達は数えない
    asyncio.run(_poll(FakeHTTP()))
    lg = LG.load_ledger()
    assert not [t for t in lg.touches if t.target_id == "L04"]


def test_ledger_upsert_clears_pending_and_activates(env):
    r = env.post("/api/setup/ledger/level", json={"id": "L04", "price": 81270.5, "name": "上POI帯上端（白線）", "kind": "horizontal",
                                                   "tier": "tier2", "born_on": "2026-09-01", "valid_from": "2026-09-03",
                                                   "needs_input": ["born_on", "valid_from"]})
    assert r.status_code == 200 and r.json()["level"]["needs_input"] == []
    lv = {l["id"]: l for l in env.get("/api/setup/state").json()["ledger"]["levels"]}
    assert lv["L04"]["active"] and lv["L04"]["pending"] == []
    r = env.post("/api/setup/ledger/band", json={"id": "P_MID", "side": "L", "lo": 77000, "hi": 77150, "ref_level": 76151.9,
                                                  "sl_basis": "L06帯の外側", "sl_price": 75900, "component_level_ids": ["L05"],
                                                  "needs_input": ["ref_level", "sl_price"]})
    assert r.status_code == 200 and r.json()["band"]["needs_input"] == []
    assert env.post("/api/setup/ledger/band", json={"id": "X", "side": "L", "lo": 2, "hi": 1}).status_code == 400


def test_calendar_endpoints(env):
    r = env.post("/api/setup/calendar/event", json={"ts_local": "2026-10-02 14:30", "name": "雇用統計", "importance": "market_moving"})
    assert r.status_code == 200 and any(e["name"] == "雇用統計" for e in r.json()["events"])
    assert env.post("/api/setup/calendar/event", json={"ts_local": "2026-10-02 14:30", "name": "x", "importance": "huge"}).status_code == 400
    r = env.post("/api/setup/calendar/holiday", json={"date": "2026-11-26", "name": "感謝祭"})
    assert any(h["date"] == "2026-11-26" for h in r.json()["holidays"])


def test_state_text_has_no_forbidden_words(env):
    asyncio.run(_poll(FakeHTTP()))
    body = env.get("/api/setup/state").text
    for w in ("シグナル", "エントリー推奨", "ロング推奨", "ショート推奨", "買い時", "売り時"):
        assert w not in body, w


def test_replay_endpoint(env):
    sc.init(FakeHTTP(touch_at=paris_to_ms("2026-09-07 15:00")), None, None)
    r = env.post("/api/setup/replay", json={"from_local": "2026-09-07 00:00", "to_local": "2026-09-08 00:00"})
    assert r.status_code == 200
    j = r.json()
    assert j["bars"] > 0 and j["csv"].splitlines()[0].startswith("id,ts_local")
    l01 = [x for x in j["rows"] if x["target_id"] == "L01"]
    assert l01 and l01[0]["ts_local"] == "2026-09-07 15:00" and len(l01[0]["path_16_close"]) == 16
    assert env.post("/api/setup/replay", json={"from_local": "2026-09-08 00:00", "to_local": "2026-09-07 00:00"}).status_code == 400
    assert env.post("/api/setup/replay", json={"from_local": "bad", "to_local": "2026-09-07 00:00"}).status_code == 400


def test_entry_state_json_is_written_for_dashboard(env, tmp_path, monkeypatch):
    p = tmp_path / "entry_state.json"
    monkeypatch.setenv("ENTRY_STATE_PATH", str(p))
    monkeypatch.setattr(sc, "_last_entry_state", None)
    asyncio.run(_poll(FakeHTTP()))
    assert p.exists()
    j = json.loads(p.read_text(encoding="utf-8"))
    assert j["state"] == "IDLE" and set(j["pillars"]) == {"structure", "cvd", "oi"} and j["regime"] in ("uptrend", "range", "downtrend")
    assert j["pillars"]["cvd"]["name"] == "吸収 (a·d)"
    # フロー遷移で即時に書き換わる
    env.post("/api/setup/flow/advance", json={"to": "PLACED", "arith_ok": True, "band_id": "L01", "side": "S", "sl": 83500, "avg_entry": 81900, "tp": 77450})
    j = json.loads(p.read_text(encoding="utf-8"))
    assert j["state"] == "ARMED" and j["direction"] == "SHORT" and j["setup"]["targets"] == [77450]
    # 同じバックエンドの /api/entry-state が読める形
    from collectors import entry_state as es
    assert asyncio.run(es.entry_state())["state"] == "ARMED"
