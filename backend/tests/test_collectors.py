"""
test_collectors.py — collectors のパース/変換ロジックのテスト (ネットワーク非依存)。
外部APIはフェイククライアントでモックし、正常系 + フォールバックを検証する。
"""

import datetime as dt

import pytest

from collectors import market, derivs, macro, aggdelta


# ---------------- フェイクHTTP ----------------
class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeStore:
    mark_px = 60000.0
    asset_ctx = {
        "markPx": 60000.0, "prevDayPx": 58800.0, "funding": 0.0000125,
        "openInterest": 27000.0, "dayNtlVlm": 2.1e9,
    }


# ---------------- market: predictedFundings パース ----------------
def test_parse_predicted_fundings_order_and_apr():
    data = [
        ["BTC", [
            ["BinPerp", {"fundingRate": "0.0000125"}],
            ["HlPerp", {"fundingRate": "0.00001"}],
            ["BybitPerp", {"fundingRate": "0.000011"}],
        ]],
        ["ETH", [["HlPerp", {"fundingRate": "0.00002"}]]],
    ]
    out = market._parse_predicted_fundings(data, "BTC")
    assert [x["ex"] for x in out] == ["Hyperliquid", "Binance", "Bybit"]
    hl = next(x for x in out if x["ex"] == "Hyperliquid")
    assert hl["apr"] == pytest.approx(0.00001 * 24 * 365 * 100, abs=0.05)


def test_parse_predicted_fundings_missing_coin():
    assert market._parse_predicted_fundings([["ETH", []]], "BTC") == []


def test_parse_predicted_fundings_handles_null_info():
    data = [["BTC", [["HlPerp", None], ["BinPerp", {"fundingRate": "0.00001"}]]]]
    out = market._parse_predicted_fundings(data, "BTC")
    assert [x["ex"] for x in out] == ["Binance"]


# ---------------- derivs: 正常系フェッチ ----------------
class FakeBinanceClient:
    async def get(self, url, params=None):
        limit = params.get("limit")
        if "globalLongShortAccountRatio" in url:
            return FakeResp([{"longAccount": "0.682", "shortAccount": "0.318"}])
        if "topLongShortPositionRatio" in url:
            return FakeResp([{"longAccount": "0.476", "shortAccount": "0.524"}])
        if "takerlongshortRatio" in url:
            return FakeResp([{"buyVol": "10", "sellVol": "4"} for _ in range(limit)])
        if "openInterestHist" in url:
            # 48本: 先頭 sumOpenInterestValue=1000, [-25]=1000, [-1]=1100
            arr = [{"sumOpenInterestValue": str(1000)} for _ in range(limit)]
            arr[-1] = {"sumOpenInterestValue": "1100"}
            return FakeResp(arr)
        raise AssertionError(f"unexpected url {url}")


@pytest.mark.asyncio
async def test_derivs_fetch_once():
    derivs._deps["http"] = FakeBinanceClient()
    derivs._deps["store"] = FakeStore()
    out = await derivs._fetch_once()
    assert out["lsGlobalLongPct"] == 68.2
    assert out["lsTopLongPct"] == 47.6
    assert len(out["takerSeries"]) == 24
    # (10-4)*60000 = 360000 per bar
    assert out["takerSeries"][0] == 360000
    # cvd 累積
    assert out["cvd"][0] == 360000
    assert out["cvd"][-1] == 360000 * 24
    assert len(out["oiHist"]) == 48
    assert out["oiChangePct24h"] == pytest.approx(10.0)


# ---------------- macro: Deribit満期 / 警告 / 時刻 ----------------
def test_deribit_expiries_are_fridays_0800():
    events = macro._deribit_expiries()
    assert events, "horizon内に少なくとも1件の金曜満期"
    for e in events:
        d = dt.datetime.fromtimestamp(e["ts"] / 1000, tz=dt.timezone.utc)
        assert d.weekday() == 4  # Friday
        assert d.hour == 8 and d.minute == 0
        assert e["cur"] == "CRYPTO"
        assert e["impact"] == "MED"


def test_to_ms_parses_utc():
    ms = macro._to_ms("2026-07-05 13:30:00")
    d = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    assert (d.year, d.month, d.day, d.hour, d.minute) == (2026, 7, 5, 13, 30)


def test_to_ms_invalid():
    assert macro._to_ms("not-a-date") is None


def test_build_warning_active_within_24h():
    import time
    now = int(time.time() * 1000)
    events = [
        {"ts": now + 3 * 3600 * 1000, "impact": "HIGH", "name": "NFP"},
        {"ts": now + 40 * 3600 * 1000, "impact": "HIGH", "name": "CPI"},
    ]
    w = macro._build_warning(events)
    assert w["active"] is True
    assert w["eventName"] == "NFP"


def test_build_warning_inactive_when_far():
    import time
    now = int(time.time() * 1000)
    events = [{"ts": now + 40 * 3600 * 1000, "impact": "HIGH", "name": "CPI"}]
    w = macro._build_warning(events)
    assert w["active"] is False


def test_build_warning_ignores_non_high():
    import time
    now = int(time.time() * 1000)
    events = [{"ts": now + 3 * 3600 * 1000, "impact": "MED", "name": "PMI"}]
    assert macro._build_warning(events)["active"] is False


# ---------------- aggdelta: 増分(aggId)による二重計上防止 ----------------
class FakeAggClient:
    def __init__(self, batches):
        self._batches = list(batches)

    async def get(self, url, params=None):
        return FakeResp(self._batches.pop(0))


@pytest.mark.asyncio
async def test_aggdelta_incremental_no_double_count():
    from collections import deque
    # 初回バッチ (baseline) → 2回目で新規aggIdのみ計上
    batch1 = [{"a": 1, "p": "100", "q": "1", "m": False},
              {"a": 2, "p": "100", "q": "1", "m": True}]
    batch2 = [{"a": 2, "p": "100", "q": "1", "m": True},    # 既出 (a=2) → 無視
              {"a": 3, "p": "100", "q": "2", "m": False}]   # 新規 buy 200
    aggdelta._deps["http"] = FakeAggClient([batch1, batch2])
    aggdelta._state.update({"last_agg_id": None, "last_price": None, "cvd_usd": 0.0,
                            "interval": None, "series": deque(maxlen=10), "updated": 0.0})

    await aggdelta._poll_once()   # baseline のみ (計上しない)
    assert aggdelta._state["last_agg_id"] == 2
    assert aggdelta._state["cvd_usd"] == 0.0
    assert len(aggdelta._state["series"]) == 0

    await aggdelta._poll_once()   # a=3 の buy 200 のみ計上
    assert aggdelta._state["last_agg_id"] == 3
    assert aggdelta._state["cvd_usd"] == 200.0
    assert aggdelta._state["interval"]["count"] == 1
    assert aggdelta._state["series"][-1]["cvdUsd"] == 200


# ---------------- リーダーボードの ijson 逐次パース (メモリ一定・上位抽出) ----------------
class FakeStreamCtx:
    def __init__(self, data: bytes, chunk: int = 16):
        self._data, self._chunk = data, chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        for i in range(0, len(self._data), self._chunk):
            yield self._data[i:i + self._chunk]


class FakeStreamHttp:
    def __init__(self, data: bytes):
        self._data = data

    def stream(self, method, url):
        return FakeStreamCtx(self._data)


@pytest.mark.asyncio
async def test_harvest_leaderboard_ranks_and_bootstrap():
    import liqmap_service as L
    payload = (
        '{"leaderboardRows":['
        '{"ethAddress":"0xAAA","accountValue":"1000","windowPerformances":[["day",{"pnl":"1"}],["month",{"pnl":"500"}]]},'
        '{"ethAddress":"0xBBB","accountValue":"5000","windowPerformances":[["month",{"pnl":"100"}]]},'
        '{"ethAddress":"0xCCC","accountValue":"200","windowPerformances":[["month",{"pnl":"900"}]]}'
        ']}'
    ).encode()
    L.http = FakeStreamHttp(payload)
    top_list, bootstrap = await L._harvest_leaderboard()
    # 30日PnL順: CCC(900) > AAA(500) > BBB(100)
    assert [t["addr"] for t in top_list] == ["0xccc", "0xaaa", "0xbbb"]
    assert [t["rank"] for t in top_list] == [1, 2, 3]
    assert top_list[0]["month_pnl"] == 900.0
    # 口座残高順: BBB(5000) > AAA(1000) > CCC(200)
    assert bootstrap == ["0xbbb", "0xaaa", "0xccc"]


@pytest.mark.asyncio
async def test_harvest_leaderboard_respects_top_n():
    import liqmap_service as L
    rows = ",".join(
        f'{{"ethAddress":"0x{i:03x}","accountValue":"{i}","windowPerformances":[["month",{{"pnl":"{i}"}}]]}}'
        for i in range(1, 21)
    )
    L.http = FakeStreamHttp(('{"leaderboardRows":[' + rows + "]}").encode())
    orig_pnl, orig_acct = L.TOP_TRADERS_N, L.LEADERBOARD_TOP_N
    L.TOP_TRADERS_N, L.LEADERBOARD_TOP_N = 3, 5
    try:
        top_list, bootstrap = await L._harvest_leaderboard()
    finally:
        L.TOP_TRADERS_N, L.LEADERBOARD_TOP_N = orig_pnl, orig_acct
    assert len(top_list) == 3          # 上位3件のみ保持
    assert top_list[0]["month_pnl"] == 20.0
    assert len(bootstrap) == 5         # 口座残高上位5件のみ
