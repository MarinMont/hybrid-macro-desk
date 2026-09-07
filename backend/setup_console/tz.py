"""
tz.py — 時刻の規約 (SPEC §2.2)
内部は UTC (epoch ms)。表示・記録・入力はすべて Europe/Paris。
15分足は open 時刻で識別し、確定表示は close 時刻 (open + 15分) を使う。
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
UTC = dt.timezone.utc

BAR_MS = {"1m": 60_000, "15m": 900_000, "1h": 3_600_000}


def to_paris(ms: int) -> dt.datetime:
    """epoch ms (UTC) → Europe/Paris の aware datetime。"""
    return dt.datetime.fromtimestamp(ms / 1000, tz=UTC).astimezone(PARIS)


def paris_label(ms: int) -> str:
    """記録ファイルの `YYYY-MM-DD HH:MM` 表記 (Europe/Paris)。"""
    return to_paris(ms).strftime("%Y-%m-%d %H:%M")


def paris_to_ms(s: str) -> int:
    """`YYYY-MM-DD HH:MM` (Europe/Paris) → epoch ms。DST は zoneinfo に従う。"""
    d = dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=PARIS)
    return int(d.timestamp() * 1000)


def bar_close_ms(open_ms: int, interval: str = "15m") -> int:
    """足の確定時刻 = open + 足幅 (Binance の closeTime は 1ms 手前なので自前で計算)。"""
    return open_ms + BAR_MS[interval]


def bar_labels(open_ms: int, interval: str = "15m") -> dict:
    """UI/台帳用: 開始・確定の Paris 表記と UTC epoch。"""
    return {
        "open_local": paris_label(open_ms),
        "close_local": paris_label(bar_close_ms(open_ms, interval)),
        "open_utc_ms": open_ms,
    }


def paris_date(ms: int) -> dt.date:
    return to_paris(ms).date()
