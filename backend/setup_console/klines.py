"""
klines.py — Binance USDⓈ-M 先物 BTCUSDT の足とデルタ (SPEC §2.1 / §2.4)
計測市場は Binance (校正が Binance で行われている)。Hyperliquid の足と混ぜない。

Binance kline 配列: [openTime, open, high, low, close, volume, closeTime, quoteVol,
                     trades, takerBuyBase, takerBuyQuote, ignore]
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

DELTA_METHODS = ("taker", "reconstruct")


@dataclass(frozen=True)
class Bar:
    t: int          # open time (epoch ms, UTC)
    o: float
    h: float
    l: float
    c: float
    v: float        # base volume
    taker_buy: float  # takerBuyBaseVolume

    @property
    def taker_delta(self) -> float:
        """δ = 2 × takerBuyBaseVolume − volume (真の攻め手側)。"""
        return 2 * self.taker_buy - self.v

    @property
    def sign_delta(self) -> float:
        """1分足の再構成用: close>open なら +vol、close<open なら −vol、同値は 0。"""
        if self.c > self.o:
            return self.v
        if self.c < self.o:
            return -self.v
        return 0.0


def parse_klines(raw: Sequence[Sequence]) -> list[Bar]:
    """Binance kline 配列 → Bar 列 (時系列昇順のまま)。数値でない行は捨てる。"""
    out: list[Bar] = []
    for k in raw or []:
        try:
            out.append(Bar(
                t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]),
                c=float(k[4]), v=float(k[5]), taker_buy=float(k[9]),
            ))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def only_closed(bars: Sequence[Bar], now_ms: int, interval_ms: int) -> list[Bar]:
    """確定足のみ (open + 足幅 <= now)。形成中の足では判定を出さない (SPEC §5.4)。"""
    return [b for b in bars if b.t + interval_ms <= now_ms]


# ---------------- δ 2方式 (SPEC §2.4) ----------------
def delta_taker(bar: Bar) -> float:
    return bar.taker_delta


def delta_reconstruct(bar15: Bar, bars1: Sequence[Bar]) -> float | None:
    """
    1分足を15本集約 (Pine v0.3.3 と同一)。bar15 の [t, t+15m) に入る 1分足が
    15本揃わなければ None (欠損データで偽の δ を作らない)。
    """
    lo, hi = bar15.t, bar15.t + 900_000
    seg = [b for b in bars1 if lo <= b.t < hi]
    if len(seg) != 15:
        return None
    return sum(b.sign_delta for b in seg)


def deltas(bars15: Sequence[Bar], method: str, bars1: Sequence[Bar] | None = None) -> list[float | None]:
    """15分足列に対する δ 列。method は config の delta_method。"""
    if method == "taker":
        return [delta_taker(b) for b in bars15]
    if method == "reconstruct":
        return [delta_reconstruct(b, bars1 or []) for b in bars15]
    raise ValueError(f"unknown delta_method: {method}")


# ---------------- 出来高床の中央値 ----------------
def volume_median(bars: Sequence[Bar], length: int) -> float | None:
    """
    直近 length 本 (最新確定足を含む) の出来高中央値。本数不足は None
    (床を計算できないときは a を成立させない側に倒す — 呼び出し側で扱う)。
    """
    if len(bars) < length:
        return None
    return float(statistics.median(b.v for b in bars[-length:]))
