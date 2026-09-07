"""
ledger.py — 台帳 (SPEC §3): POI水準・帯の登録、有効開始日、到達記録
「要記入」「要確認」は値として保持し、人間が埋めるまで当該水準を inactive 扱いにする (§3.2)。
先読み防止 (§3.3): 到達を数えるのは valid_from <= 到達日 の水準のみ。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

from .klines import Bar
from .store import data_path, read_json, write_json_atomic
from .tz import paris_date, paris_label

LEVEL_KINDS = ("swing_high", "swing_low", "fib", "horizontal", "fib_band")
TIERS = ("tier1", "tier2", "watch")
STATUSES = ("active", "broken", "retired")


@dataclass
class Level:
    id: str
    price: float
    name: str
    kind: str
    tier: str
    born_on: str | None          # "YYYY-MM-DD" (L01 は "2026-05" — 月のみ。README 未回答事項 7)
    valid_from: str | None       # "YYYY-MM-DD"。None = 要記入
    status: str = "active"
    note: str = ""
    needs_input: list[str] = field(default_factory=list)     # 要記入のフィールド名
    needs_confirm: list[str] = field(default_factory=list)   # 要確認のフィールド名

    @property
    def pending(self) -> list[str]:
        return sorted(set(self.needs_input) | set(self.needs_confirm))

    def is_active(self, on: dt.date | None = None) -> bool:
        """人間が埋めるまで inactive。on を渡すと有効開始日も見る。"""
        if self.status != "active" or self.pending or not self.valid_from:
            return False
        if on is None:
            return True
        return dt.date.fromisoformat(self.valid_from) <= on


@dataclass
class Band:
    id: str
    side: str                    # "S" | "L"
    lo: float
    hi: float
    ref_level: float | None      # (c) の参照水準 = 帯の向こう側の縁。None = 要記入
    sl_basis: str
    sl_price: float | None       # SL の具体値。None = 要記入
    component_level_ids: list[str] = field(default_factory=list)
    note: str = ""
    needs_input: list[str] = field(default_factory=list)
    needs_confirm: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[str]:
        return sorted(set(self.needs_input) | set(self.needs_confirm))

    def valid_from(self, levels: dict[str, Level]) -> str | None:
        """構成水準の最も遅い valid_from。構成水準に未記入があれば None。"""
        vs = []
        for lid in self.component_level_ids:
            lv = levels.get(lid)
            if lv is None or not lv.valid_from or lv.pending:
                return None
            vs.append(lv.valid_from)
        return max(vs) if vs else None

    def is_active(self, levels: dict[str, Level], on: dt.date | None = None) -> bool:
        vf = self.valid_from(levels)
        if self.pending or vf is None:
            return False
        if on is None:
            return True
        return dt.date.fromisoformat(vf) <= on


@dataclass
class Touch:
    id: str
    ts_local: str                # 到達足の open (Europe/Paris)
    ts_utc: int                  # 到達足の open (epoch ms)
    target_id: str               # band_id | level_id
    side: str                    # "S" | "L"
    valid_check: bool            # その時点で水準が既に有効だったか
    verdict: str = "silent"      # "confirmed" | "rejected_c" | "silent"
    first_fire_ts: str | None = None
    accept_warning_ts: str | None = None
    outcome_4h: str | None = None          # "progress" | "flat" | "adverse" (人間が記入)
    outcome_structure: str | None = None   # "held" | "broken" (人間が記入)
    calib_scene_id: str | None = None
    note: str = ""
    end_ts_utc: int | None = None          # 帯離脱 (3本連続で帯外) した足の open。None = 継続中
    bar_count: int = 0


# ---------------- 初期データ (SPEC §3.2 そのまま) ----------------
def seed_levels() -> list[Level]:
    return [
        Level("L01", 82923.4, "Key High（5月高値）", "swing_high", "tier1", "2026-05", "2026-08-17",
              note="5月由来 → valid_from 2026-08-17 固定。born_on は月のみ"),
        Level("L02", 82282.8, "9/3高値", "swing_high", "tier2", "2026-09-03", "2026-09-05"),
        Level("L03", 81500.0, "フィボ0", "fib", "tier1", "2026-08-27", "2026-08-29",
              note="born_on はアンカー日", needs_confirm=["valid_from"]),
        Level("L04", 81270.5, "上POI帯上端（白線）", "horizontal", "tier2", None, None,
              needs_input=["born_on", "valid_from"]),
        Level("L05", 77068.8, "0.236", "fib", "tier1", "2026-08-27", "2026-08-29", needs_confirm=["valid_from"]),
        Level("L06", 76151.9, "9/2安値", "swing_low", "tier2", "2026-09-02", "2026-09-04"),
        Level("L07", 75558.7, "Key Low", "swing_low", "tier1", None, None, needs_input=["born_on", "valid_from"]),
        Level("O01", 74327.5, "0.382", "fib", "watch", "2026-08-27", "2026-08-29", note="同L05", needs_confirm=["valid_from"]),
        Level("O02", 72112.0, "0.5", "fib", "watch", "2026-08-27", "2026-08-29", note="同L05", needs_confirm=["valid_from"]),
        Level("O03", 69896.4, "0.618", "fib", "watch", "2026-08-27", "2026-08-29", note="同L05", needs_confirm=["valid_from"]),
    ]


def seed_bands() -> list[Band]:
    # 構成水準は帯の縁と一致する水準から推定 (暫定。UI から編集可):
    #   P_UPPER lo 81500 = L03 / hi 82300 ≈ L02 82282.8 / P_MID ∋ L05 / P_LOWER ∋ L07
    return [
        Band("P_UPPER", "S", 81500, 82300, 82300, "L01（82,923.4）の外側 → 83,500", 83500.0,
             component_level_ids=["L03", "L02"], note="暫定"),
        Band("P_MID", "L", 77000, 77150, None, "L06帯の外側", None,
             component_level_ids=["L05"], note="ref_level = 帯下端の向こう側（当時有効な下側水準）",
             needs_input=["ref_level", "sl_price"]),
        Band("P_LOWER", "L", 75500, 75650, None, "75,000〜75,300帯の外側", None,
             component_level_ids=["L07"], note="ref_level は同上",
             needs_input=["ref_level", "sl_price"]),
    ]


# ---------------- 永続化 ----------------
LEDGER_FILE = "ledger.json"


@dataclass
class Ledger:
    levels: list[Level] = field(default_factory=list)
    bands: list[Band] = field(default_factory=list)
    touches: list[Touch] = field(default_factory=list)

    def level_map(self) -> dict[str, Level]:
        return {l.id: l for l in self.levels}

    def to_dict(self) -> dict:
        return {"levels": [asdict(l) for l in self.levels], "bands": [asdict(b) for b in self.bands],
                "touches": [asdict(t) for t in self.touches]}

    @classmethod
    def from_dict(cls, d: dict) -> "Ledger":
        return cls(
            levels=[Level(**x) for x in d.get("levels", [])],
            bands=[Band(**x) for x in d.get("bands", [])],
            touches=[Touch(**x) for x in d.get("touches", [])],
        )

    @classmethod
    def seed(cls) -> "Ledger":
        return cls(levels=seed_levels(), bands=seed_bands(), touches=[])


def load_ledger(path=None) -> Ledger:
    p = path or data_path(LEDGER_FILE)
    d = read_json(p, None)
    if d is None:
        lg = Ledger.seed()
        write_json_atomic(p, lg.to_dict())
        return lg
    return Ledger.from_dict(d)


def save_ledger(lg: Ledger, path=None) -> None:
    write_json_atomic(path or data_path(LEDGER_FILE), lg.to_dict())


# ---------------- 到達判定 (SPEC §3.4) ----------------
LEAVE_BARS = 3   # 帯から離脱 = 3本連続で帯外


@dataclass(frozen=True)
class Target:
    id: str
    lo: float
    hi: float
    side: str | None       # Band は固定。Level は None → 接近方向で決める
    valid_from: str | None # None = 未誕生/未記入 (数えない)


def targets_from_ledger(lg: Ledger) -> list[Target]:
    lm = lg.level_map()
    out: list[Target] = []
    for b in lg.bands:
        out.append(Target(b.id, b.lo, b.hi, b.side, b.valid_from(lm) if not b.pending else None))
    for l in lg.levels:
        side = "S" if l.kind == "swing_high" else "L" if l.kind == "swing_low" else None
        vf = l.valid_from if (l.status == "active" and not l.pending) else None
        out.append(Target(l.id, l.price, l.price, side, vf))
    return out


def overlaps(bar: Bar, tg: Target) -> bool:
    return bar.l <= tg.hi and bar.h >= tg.lo


def target_valid_on(tg: Target, on: dt.date) -> bool:
    return tg.valid_from is not None and dt.date.fromisoformat(tg.valid_from) <= on


@dataclass
class TouchSpan:
    target_id: str
    side: str
    start_t: int
    bars_t: list[int]
    end_t: int | None = None    # 離脱を確定した足の open


def detect_touches(bars: Sequence[Bar], targets: Iterable[Target], enforce_valid: bool = True) -> list[TouchSpan]:
    """
    15分確定足の高安が水準/帯を跨いだ最初の足を到達とする。帯内で揉んでいる間は同一到達。
    帯から離脱 (3本連続で帯外) して再到達したら新規。
    enforce_valid=True なら、その時点で valid_from 未到達の水準は無視する (先読み防止)。
    """
    spans: list[TouchSpan] = []
    for tg in targets:
        cur: TouchSpan | None = None
        outside = 0
        prev_close: float | None = None
        for b in bars:
            if enforce_valid and not target_valid_on(tg, paris_date(b.t)):
                prev_close = b.c
                continue
            hit = overlaps(b, tg)
            if cur is None:
                if hit:
                    if tg.side:
                        side = tg.side
                    else:   # 接近方向: 前足終値が水準の下 → 上から見て抵抗 (S)、上 → 支持 (L)
                        side = "S" if (prev_close is not None and prev_close < tg.lo) else "L"
                    cur = TouchSpan(tg.id, side, b.t, [b.t])
                    outside = 0
            else:
                cur.bars_t.append(b.t)
                if hit:
                    outside = 0
                else:
                    outside += 1
                    if outside >= LEAVE_BARS:
                        cur.end_t = b.t
                        spans.append(cur)
                        cur = None
                        outside = 0
            prev_close = b.c
        if cur is not None:
            spans.append(cur)
    spans.sort(key=lambda s: s.start_t)
    return spans


def span_to_touch(span: TouchSpan, valid_check: bool, tid: str) -> Touch:
    return Touch(
        id=tid, ts_local=paris_label(span.start_t), ts_utc=span.start_t, target_id=span.target_id,
        side=span.side, valid_check=valid_check, end_ts_utc=span.end_t, bar_count=len(span.bars_t),
    )


def suggest_valid_from(kind: str, born_on: str | None) -> str | None:
    """
    valid_from の自動提案 (人間が確定する。§3.3):
      スイング高安 = 極値の翌日の日足終値で極値未更新を確認した翌日 = born_on + 2日
      フィボ = アンカーのスイング確定日 (= born_on + 2日 と同じ規則をアンカーに適用)
      5月由来 = 2026-08-17
    """
    if not born_on:
        return None
    if born_on.startswith("2026-05"):
        return "2026-08-17"
    try:
        d = dt.date.fromisoformat(born_on)
    except ValueError:
        return None
    return (d + dt.timedelta(days=2)).isoformat()
