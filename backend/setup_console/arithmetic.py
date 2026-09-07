"""
arithmetic.py — 算術 (SPEC §4): SL幅・R:R・ロット・違反フラグの検算
**設計値を勝手に丸めたり補正したりしない** (人間が直す)。フラグが1つでも赤なら「敷設不可」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_SL_ATR_MULT = 2.0     # SL幅 < 2 × 1H ATR → 違反
MIN_RR = 2.0              # R:R < 2.0 → 違反
SPLIT_MIN_ATR15_MULT = 2.0  # 帯の厚み < 2 × 15m ATR なのに分割 → 違反


@dataclass(frozen=True)
class EntryLeg:
    price: float
    ratio: float = 1.0          # 分割比率 (正規化して使う)
    lot: float | None = None    # 入力ロット (BTC)。None なら最大ロット × 比率を表示


@dataclass
class ArithInput:
    balance_usd: float
    risk_pct: float             # 既定 2、勝ちトレード後は 1
    side: str                   # "S" | "L"
    entries: list[EntryLeg]
    sl: float
    tp: float
    atr_1h: float
    atr_15m: float | None = None
    liq_upper: float | None = None   # 上側の清算帯 (価格)
    liq_lower: float | None = None   # 下側の清算帯 (価格)
    band_lo: float | None = None     # 帯の厚み用
    band_hi: float | None = None
    weekend_or_holiday: bool = False
    atr_source: str = "today"        # "today" | "last_full_session"


@dataclass
class Row:
    key: str
    label: str
    value: str
    violation: bool
    detail: str = ""


@dataclass
class ArithResult:
    avg_entry: float
    sl_width: float
    rr: float | None
    max_lot: float
    total_lot: float
    lots: list[float]
    rows: list[Row] = field(default_factory=list)

    @property
    def placement_blocked(self) -> bool:
        return any(r.violation for r in self.rows)

    def to_dict(self) -> dict:
        return {
            "avg_entry": self.avg_entry, "sl_width": self.sl_width, "rr": self.rr, "max_lot": self.max_lot,
            "total_lot": self.total_lot, "lots": self.lots, "placement_blocked": self.placement_blocked,
            "rows": [r.__dict__ for r in self.rows],
        }


def _f(x: float | None, nd: int = 1) -> str:
    return "—" if x is None else f"{x:,.{nd}f}"


def check(inp: ArithInput) -> ArithResult:
    if inp.side not in ("S", "L"):
        raise ValueError("side は S/L")
    if not inp.entries:
        raise ValueError("エントリーが空")
    total_ratio = sum(e.ratio for e in inp.entries)
    if total_ratio <= 0:
        raise ValueError("分割比率の合計が 0")
    weights = [e.ratio / total_ratio for e in inp.entries]
    avg_entry = sum(e.price * w for e, w in zip(inp.entries, weights))
    sl_width = abs(avg_entry - inp.sl)
    if sl_width <= 0:
        raise ValueError("SL が平均エントリーと同値")

    rr = abs(inp.tp - avg_entry) / sl_width
    risk_usd = inp.balance_usd * inp.risk_pct / 100
    max_lot = risk_usd / sl_width
    lots = [e.lot if e.lot is not None else max_lot * w for e, w in zip(inp.entries, weights)]
    total_lot = sum(lots)
    rows: list[Row] = []

    # SL幅
    need = MIN_SL_ATR_MULT * inp.atr_1h
    rows.append(Row("sl_width", "SL幅", _f(sl_width), sl_width < need,
                    f"|平均エントリー {_f(avg_entry)} − SL {_f(inp.sl)}|。下限 2×1H ATR = {_f(need)}"))

    # SL位置 (清算帯の内側 = 建値と清算帯の間)
    if inp.side == "S":
        liq, bad = inp.liq_upper, (inp.liq_upper is not None and inp.sl < inp.liq_upper)
    else:
        liq, bad = inp.liq_lower, (inp.liq_lower is not None and inp.sl > inp.liq_lower)
    rows.append(Row("sl_pos", "SL位置", _f(inp.sl), bad,
                    "清算帯 " + _f(liq) + (" の内側" if bad else " の外側" if liq is not None else " 未指定")))

    # TP位置 (次の清算帯を越えている)
    if inp.side == "S":
        liq_tp, bad = inp.liq_lower, (inp.liq_lower is not None and inp.tp < inp.liq_lower)
    else:
        liq_tp, bad = inp.liq_upper, (inp.liq_upper is not None and inp.tp > inp.liq_upper)
    rows.append(Row("tp_pos", "TP位置", _f(inp.tp), bad,
                    "次の清算帯 " + _f(liq_tp) + (" を越えている" if bad else " の手前" if liq_tp is not None else " 未指定")))

    # R:R
    rows.append(Row("rr", "R:R", f"{rr:.2f}", rr < MIN_RR, f"|TP − 平均エントリー| ÷ SL幅。下限 {MIN_RR:.1f}"))

    # 最大ロット
    rows.append(Row("max_lot", "最大ロット", f"{max_lot:.4f} BTC", total_lot > max_lot + 1e-12,
                    f"(残高 {_f(inp.balance_usd, 0)} × {inp.risk_pct:g}%) ÷ SL幅。入力合計 {total_lot:.4f} BTC"))

    # 分散
    split = len(inp.entries) > 1
    if split and inp.atr_15m is not None and inp.band_lo is not None and inp.band_hi is not None:
        thick = inp.band_hi - inp.band_lo
        bad = thick < SPLIT_MIN_ATR15_MULT * inp.atr_15m
        rows.append(Row("split", "分散", f"帯の厚み {_f(thick)}", bad,
                        f"{len(inp.entries)} 分割。下限 2×15m ATR = {_f(SPLIT_MIN_ATR15_MULT * inp.atr_15m)}"))
    else:
        rows.append(Row("split", "分散", "単発" if not split else f"{len(inp.entries)} 分割", False,
                        "" if not split else "帯の厚み / 15m ATR 未指定のため判定なし"))

    # 全約定時リスク
    full_risk = total_lot * sl_width
    rows.append(Row("full_risk", "全約定時リスク", _f(full_risk, 0) + " USD", full_risk > risk_usd + 1e-6,
                    f"Σ(枠ロット) × SL幅。上限 残高×リスク% = {_f(risk_usd, 0)}"))

    # 週末ATR
    bad = inp.weekend_or_holiday and inp.atr_source == "today"
    rows.append(Row("weekend_atr", "週末ATR",
                    "当日ATR" if inp.atr_source == "today" else "直近フル流動性セッションのATR", bad,
                    "土日・米休場日は直近フル流動性セッションのATRを使用 (当日ATRは拒否)" if inp.weekend_or_holiday else "平日"))

    # 名目・レバレッジ (表示のみ)
    notional = total_lot * avg_entry
    rows.append(Row("notional", "名目・レバレッジ", f"{_f(notional, 0)} USD / {notional / inp.balance_usd:.2f}x", False, "表示のみ"))

    return ArithResult(avg_entry=avg_entry, sl_width=sl_width, rr=rr, max_lot=max_lot,
                       total_lot=total_lot, lots=lots, rows=rows)
