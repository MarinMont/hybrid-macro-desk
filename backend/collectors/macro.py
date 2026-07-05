"""
macro.py — GET /api/macro
=========================
経済カレンダー + 暗号イベント + 持ち越し警告。
レスポンス骨子 (API_DESIGN §1):
  { events[{ts,cur,impact,name,note,f,p}], warning{active,eventName,ts} }

ソース:
  - Finnhub /calendar/economic (無料キー FINNHUB_API_KEY) — US中心の経済指標
  - Deribit満期: 毎週金曜 08:00 UTC の BTC/ETH オプション満期を自前計算
  - Claude注釈: 各イベントに1文の影響分析 (日次バッチ / キー未設定ならルールベース)

持ち越し警告: 現在時刻から24h以内に impact=HIGH が存在すれば active:true (API_DESIGN §3)。
ポーリング 15min (注釈は日次)。Finnhub 不達でも暗号イベントのみで返す (画面を殺さない)。
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time

from fastapi import APIRouter, HTTPException

import llm

log = logging.getLogger("liqmap.macro")
router = APIRouter()

FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"
HORIZON_DAYS = 7

IMPACT_MAP = {"high": "HIGH", "medium": "MED", "low": "LOW", "3": "HIGH", "2": "MED", "1": "LOW"}
IMPACT_ORDER = {"HIGH": 3, "MED": 2, "LOW": 1}
# Finnhub の国コード → 表示通貨 (フロントのCUR色マップに合わせる)
COUNTRY_CUR = {"US": "USD", "JP": "JPY", "EU": "EUR", "DE": "EUR", "FR": "EUR", "GB": "GBP"}

# AIキー未設定時のルールベース注釈 (impact別)
FALLBACK_NOTE = {
    "HIGH": "高インパクト指標。結果次第で米金利とリスク資産のボラが大きく動きやすい。跨ぐ場合はサイズ管理を厳格に。",
    "MED": "中程度のイベント。単発の影響は限定的だが、前後のポジション調整でノイズが出やすい。",
    "LOW": "影響は軽微。ただし上位イベント前の地ならしとして意識されることがある。",
}

_deps: dict = {"http": None}
_cache: dict = {"events": [], "warning": {"active": False, "eventName": None, "ts": None}}
_notes: dict[str, dict[str, str]] = {}  # {date_str: {event_key: note}}


def init(http, bucket, store) -> None:
    _deps["http"] = http


def _event_key(e: dict) -> str:
    return f"{e['ts']}|{e['name']}"


def _to_ms(time_str: str) -> int | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(time_str, fmt).replace(tzinfo=dt.timezone.utc)
            return int(d.timestamp() * 1000)
        except (ValueError, TypeError):
            continue
    return None


async def _fetch_finnhub() -> list[dict]:
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        return []
    http = _deps["http"]
    today = dt.datetime.now(dt.timezone.utc).date()
    params = {
        "token": key,
        "from": today.isoformat(),
        "to": (today + dt.timedelta(days=HORIZON_DAYS)).isoformat(),
    }
    r = await http.get(FINNHUB_URL, params=params, timeout=10)
    r.raise_for_status()
    rows = r.json().get("economicCalendar", []) or []
    out: list[dict] = []
    for row in rows:
        country = str(row.get("country", "")).upper()
        if country != "US":  # US中心 (SPEC §5)
            continue
        impact = IMPACT_MAP.get(str(row.get("impact", "")).lower())
        if not impact:
            continue
        ts = _to_ms(str(row.get("time", "")))
        if ts is None:
            continue
        out.append({
            "ts": ts,
            "cur": COUNTRY_CUR.get(country, country),
            "impact": impact,
            "name": row.get("event", "(名称不明)"),
            "note": "",
            "f": _fmt_num(row.get("estimate")),
            "p": _fmt_num(row.get("prev")),
        })
    return out


def _fmt_num(v) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _deribit_expiries() -> list[dict]:
    """毎週金曜 08:00 UTC の BTC/ETH オプション満期を horizon 内で生成。"""
    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(days=HORIZON_DAYS)
    out: list[dict] = []
    # 直近の金曜を探索 (weekday: 月=0 … 金=4)
    d = now.replace(hour=8, minute=0, second=0, microsecond=0)
    days_ahead = (4 - d.weekday()) % 7
    d = d + dt.timedelta(days=days_ahead)
    while d <= horizon:
        if d > now:
            out.append({
                "ts": int(d.timestamp() * 1000),
                "cur": "CRYPTO",
                "impact": "MED",
                "name": "BTC/ETH 週次オプション満期 (Deribit)",
                "note": "",
                "f": None,
                "p": None,
            })
        d = d + dt.timedelta(days=7)
    return out


async def _annotate(events: list[dict]) -> None:
    """イベントに1文注釈を付与 (AI日次バッチ / フォールバックはルールベース)。"""
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    day_notes = _notes.setdefault(today, {})
    http = _deps["http"]
    use_ai = llm.enabled()
    for e in events:
        k = _event_key(e)
        if k in day_notes:
            e["note"] = day_notes[k]
            continue
        note = FALLBACK_NOTE.get(e["impact"], "")
        if use_ai:
            try:
                prompt = (
                    "あなたはマクロトレーディングデスクのアナリストです。以下の経済/暗号イベントが"
                    "BTC無期限先物に与える影響を、日本語1文で簡潔に述べてください。売買推奨はせず、"
                    "環境認識に徹すること。プレーンテキストのみ。\n"
                    f"イベント: {e['name']} / 重要度: {e['impact']} / 通貨: {e['cur']} / 予想: {e['f']} / 前回: {e['p']}"
                )
                text = await llm.complete(http, prompt, max_tokens=200)
                if text:
                    note = text.replace("\n", " ").strip()
            except Exception as ex:  # noqa: BLE001
                log.warning("annotate failed (%s): %s", e["name"], ex)
        day_notes[k] = note
        e["note"] = note
    # 古い日付のキャッシュを掃除
    for old in [k for k in _notes if k != today]:
        _notes.pop(old, None)


def _build_warning(events: list[dict]) -> dict:
    now_ms = int(time.time() * 1000)
    horizon = now_ms + 24 * 3600 * 1000
    for e in sorted(events, key=lambda x: x["ts"]):
        if e["ts"] > now_ms and e["ts"] <= horizon and e["impact"] == "HIGH":
            return {"active": True, "eventName": e["name"], "ts": e["ts"]}
    return {"active": False, "eventName": None, "ts": None}


async def _refresh():
    finnhub_events: list[dict] = []
    try:
        finnhub_events = await _fetch_finnhub()
    except Exception as e:  # noqa: BLE001
        log.warning("finnhub 不達 (%s) — 暗号イベントのみで継続", e)
    events = finnhub_events + _deribit_expiries()
    events.sort(key=lambda x: x["ts"])
    try:
        await _annotate(events)
    except Exception as e:  # noqa: BLE001
        log.warning("annotate batch failed: %s", e)
    _cache["events"] = events
    _cache["warning"] = _build_warning(events)


async def _loop():
    import asyncio

    while True:
        try:
            await _refresh()
        except Exception as e:  # noqa: BLE001
            log.warning("macro loop: %s", e)
        await asyncio.sleep(15 * 60)


async def run():
    import asyncio

    asyncio.create_task(_loop())


@router.get("/api/macro")
async def macro():
    # イベントが空でも warning は返す (画面を殺さない)。起動直後の空も 200 で返す。
    return {"events": _cache["events"], "warning": _cache["warning"]}
