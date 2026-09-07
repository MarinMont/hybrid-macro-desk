"""
calendar_gate.py — カレンダーゲート (SPEC §7)
v1 は手動登録リスト (data/calendar.json)。項目: 日時 (Europe/Paris)、名称、重要度 (market_moving / minor)。
  - market_moving の前 2h・後 1h は「敷設不可」
  - 月末最終営業日は終日「ノートレード」、その 48 時間前から予告
  - 土日・休場日 (calendar.json の holidays) は「薄商い」
「営業日」は US 営業日 (土日 + holidays を除く) と暫定解釈 (README 未回答事項 11)。
"""

from __future__ import annotations

import calendar as _cal
import datetime as dt
from dataclasses import dataclass

from .store import data_path, read_json, write_json_atomic
from .tz import PARIS, paris_label, paris_to_ms, to_paris

BEFORE_MS = 2 * 3_600_000
AFTER_MS = 1 * 3_600_000
NOTICE_MS = 48 * 3_600_000

SEED = {
    "events": [
        {"ts_local": "2026-09-10 14:30", "name": "PPI（8月）", "importance": "market_moving"},
        {"ts_local": "2026-09-11 14:30", "name": "CPI（8月）", "importance": "market_moving"},
        {"ts_local": "2026-09-11 16:00", "name": "ミシガン消費者信頼感", "importance": "minor"},
        {"ts_local": "2026-09-16 14:30", "name": "小売売上高", "importance": "market_moving"},
        {"ts_local": "2026-09-16 20:00", "name": "FOMC", "importance": "market_moving"},
        {"ts_local": "2026-09-30 14:30", "name": "PCE", "importance": "market_moving", "note": "同日は月末最終営業日＝ノートレード"},
    ],
    "holidays": [
        {"date": "2026-09-07", "name": "レイバーデー (米休場)"},
    ],
}

CALENDAR_FILE = "calendar.json"


def load_calendar(path=None) -> dict:
    p = path or data_path(CALENDAR_FILE)
    d = read_json(p, None)
    if d is None:
        write_json_atomic(p, SEED)
        return {"events": list(SEED["events"]), "holidays": list(SEED["holidays"])}
    return {"events": d.get("events", []), "holidays": d.get("holidays", [])}


def save_calendar(cal: dict, path=None) -> None:
    write_json_atomic(path or data_path(CALENDAR_FILE), cal)


def is_holiday(d: dt.date, holidays: list[dict]) -> bool:
    return any(h.get("date") == d.isoformat() for h in holidays)


def is_business_day(d: dt.date, holidays: list[dict]) -> bool:
    return d.weekday() < 5 and not is_holiday(d, holidays)


def last_business_day(year: int, month: int, holidays: list[dict]) -> dt.date:
    d = dt.date(year, month, _cal.monthrange(year, month)[1])
    while not is_business_day(d, holidays):
        d -= dt.timedelta(days=1)
    return d


@dataclass
class Badge:
    kind: str      # "no_placement" | "no_trade" | "no_trade_notice" | "thin"
    text: str
    until_local: str | None = None


def gate(now_ms: int, cal: dict, horizon_h: int = 72) -> dict:
    """
    現在時刻の判定と、今日〜horizon_h の窓の一覧。
    placement_allowed = 敷設不可 / ノートレード のバッジが無いこと。
    """
    events, holidays = cal.get("events", []), cal.get("holidays", [])
    badges: list[Badge] = []
    windows: list[dict] = []

    # イベント窓
    for e in events:
        try:
            t = paris_to_ms(e["ts_local"])
        except (KeyError, ValueError):
            continue
        if e.get("importance") != "market_moving":
            windows.append({"kind": "minor", "name": e.get("name", ""), "start_utc": t, "end_utc": t, "start_local": e["ts_local"], "end_local": e["ts_local"]})
            continue
        s, en = t - BEFORE_MS, t + AFTER_MS
        windows.append({"kind": "no_placement", "name": e.get("name", ""), "start_utc": s, "end_utc": en,
                        "start_local": paris_label(s), "end_local": paris_label(en)})
        if s <= now_ms < en:
            badges.append(Badge("no_placement", f"敷設不可: {e.get('name', '')} (前2h・後1h)", paris_label(en)))

    # 月末最終営業日
    now_p = to_paris(now_ms)
    today = now_p.date()
    for ym in ((today.year, today.month), _next_month(today)):
        lbd = last_business_day(*ym, holidays)
        s = int(dt.datetime.combine(lbd, dt.time(0, 0), tzinfo=PARIS).timestamp() * 1000)
        en = int(dt.datetime.combine(lbd + dt.timedelta(days=1), dt.time(0, 0), tzinfo=PARIS).timestamp() * 1000)
        windows.append({"kind": "no_trade", "name": "月末最終営業日", "start_utc": s, "end_utc": en,
                        "start_local": paris_label(s), "end_local": paris_label(en)})
        if s <= now_ms < en:
            badges.append(Badge("no_trade", f"ノートレード: 月末最終営業日 ({lbd.isoformat()})", paris_label(en)))
        elif 0 < s - now_ms <= NOTICE_MS:
            badges.append(Badge("no_trade_notice", f"予告: {lbd.isoformat()} は月末最終営業日 (ノートレード)", paris_label(s)))

    # 薄商い
    if today.weekday() >= 5:
        badges.append(Badge("thin", "薄商い: 週末"))
    elif is_holiday(today, holidays):
        name = next((h.get("name", "") for h in holidays if h.get("date") == today.isoformat()), "")
        badges.append(Badge("thin", f"薄商い: {name or '休場日'}"))

    horizon_end = now_ms + horizon_h * 3_600_000
    upcoming = sorted((w for w in windows if w["end_utc"] >= now_ms and w["start_utc"] <= horizon_end), key=lambda w: w["start_utc"])
    return {
        "now_local": paris_label(now_ms),
        "badges": [b.__dict__ for b in badges],
        "placement_allowed": not any(b.kind in ("no_placement", "no_trade") for b in badges),
        "thin": any(b.kind == "thin" for b in badges),
        "windows": upcoming,
    }


def _next_month(d: dt.date) -> tuple[int, int]:
    return (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
