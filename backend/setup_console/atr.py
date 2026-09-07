"""
atr.py — ATR(14) = TradingView `ta.atr` と同じ Wilder RMA 平滑 (SPEC §2.3)
  tr  = max(h−l, |h−prev_c|, |l−prev_c|)   (先頭の足は h−l)
  rma = 先頭 n 本は SMA でシード、以後 (prev × (n−1) + tr) / n
EMA でも SMA でもない。15m ATR は判定用、1H ATR は SL 設計用 — 混ぜない (ラベルで区別)。
"""

from __future__ import annotations

from typing import Sequence

from .klines import Bar


def true_range(bars: Sequence[Bar]) -> list[float]:
    out: list[float] = []
    prev_c: float | None = None
    for b in bars:
        if prev_c is None:
            out.append(b.h - b.l)
        else:
            out.append(max(b.h - b.l, abs(b.h - prev_c), abs(b.l - prev_c)))
        prev_c = b.c
    return out


def rma(values: Sequence[float], n: int) -> list[float | None]:
    """Wilder の移動平均。n 本揃うまで None。"""
    out: list[float | None] = []
    acc: float | None = None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None)
            continue
        if acc is None:
            acc = sum(values[i - n + 1:i + 1]) / n
        else:
            acc = (acc * (n - 1) + v) / n
        out.append(acc)
    return out


def atr_series(bars: Sequence[Bar], n: int = 14) -> list[float | None]:
    """各足に対応する ATR(n)。bars と同じ長さ。"""
    return rma(true_range(bars), n)


def atr_last(bars: Sequence[Bar], n: int = 14) -> float | None:
    s = atr_series(bars, n)
    return s[-1] if s else None
