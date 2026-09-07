"""
confirm.py — 吸収コンファーム (Pine v0.3.3 の完全移植。SPEC §5)
**ロジックは一文字も変えない。** 閾値は config から受け取る (ここに数値を書かない)。

15分確定足ごと、直近3本の窓 ([0]=最新確定足, [2]=3本前):
  Σδ = δ[0]+δ[1]+δ[2] / ΣV = V[0]+V[1]+V[2] / ratio = |Σδ|/ΣV
  floor_ok = ΣV >= vol_floor_mult × median(V, vol_median_len)
  S側: dir_ok = Σδ>0, prog = max(H[0],H[1],H[2]) − H[2], c = ref_s==0 or C[0]<ref_s, d = δ[2]>0
  L側: dir_ok = Σδ<0, prog = L[2] − min(L[0],L[1],L[2]),  c = ref_l==0 or C[0]>ref_l, d = δ[2]<0
  a = ratio>=x_ratio and dir_ok and floor_ok / b = prog < atr_coef×atr
  fire = a∧b∧c∧d → 成立 / rejected = a∧b∧d∧¬c → (c)棄却 / else 沈黙

(d) は **δ の符号** で判定する。ローソクの色で判定すると S2 の基準ケースが失敗する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .config import ConfirmConfig
from .tz import bar_labels

# ---------------- 固定文言 (SPEC §5.5) ----------------
VERDICT_TEXT = {
    "fire": "指値を残してよい",
    "rejected_c": "向こう側で受け入れ。プロトコル③へ",
    "silent": "—",
}
ACCEPT_TEXT = {"S": "受け入れ↑ 整理準備。プロトコル③へ", "L": "受け入れ↓ 整理準備。プロトコル③へ"}
VERDICT_LABEL = {"fire": "成立", "rejected_c": "(c)棄却", "silent": "沈黙"}


@dataclass(frozen=True)
class WindowBar:
    """判定に必要な最小要素。フィクスチャでも実データでも同じ型で渡す。"""
    t: int
    h: float
    l: float
    c: float
    v: float
    delta: float


@dataclass(frozen=True)
class SideDiag:
    side: str                  # "S" | "L"
    sum_delta: float
    sum_vol: float
    ratio: float
    floor_ok: bool
    floor_threshold: float | None   # vol_floor_mult × median。median 不明なら None
    dir_ok: bool
    prog: float
    prog_atr: float | None     # prog ÷ ATR。ATR が 0/None なら None
    a: bool
    b: bool
    c: bool
    d: bool
    verdict: str               # "fire" | "rejected_c" | "silent"


@dataclass(frozen=True)
class Diagnosis:
    t: int                     # 最新確定足の open (epoch ms)
    atr: float | None
    mode: str                  # "S" | "L" | "both"
    ref_s: float
    ref_l: float
    floor_enabled: bool
    median_v: float | None
    S: SideDiag
    L: SideDiag
    verdict: str               # mode に従った総合判定
    verdict_side: str | None   # 総合判定を出した側

    def labels(self) -> dict:
        return bar_labels(self.t, "15m")

    def to_dict(self) -> dict:
        return {
            **self.labels(),
            "atr": self.atr, "mode": self.mode, "ref_s": self.ref_s, "ref_l": self.ref_l,
            "floor_enabled": self.floor_enabled, "median_v": self.median_v,
            "S": self.S.__dict__, "L": self.L.__dict__,
            "verdict": self.verdict, "verdict_side": self.verdict_side,
            "verdict_label": VERDICT_LABEL[self.verdict], "verdict_text": VERDICT_TEXT[self.verdict],
        }


def _verdict(a: bool, b: bool, c: bool, d: bool) -> str:
    if a and b and c and d:
        return "fire"
    if a and b and d and not c:
        return "rejected_c"
    return "silent"


def judge_side(
    window: Sequence[WindowBar], atr0: float | None, side: str, ref: float,
    cfg: ConfirmConfig, median_v: float | None, floor_enabled: bool = True,
) -> SideDiag:
    """片側の判定。window は [0]=最新確定足 の順 (長さ window_bars)。"""
    if len(window) != cfg.window_bars:
        raise ValueError(f"window は {cfg.window_bars} 本必要 ({len(window)})")
    w0, w1, w2 = window[0], window[1], window[2]
    sum_delta = w0.delta + w1.delta + w2.delta
    sum_vol = w0.v + w1.v + w2.v
    ratio = abs(sum_delta) / sum_vol if sum_vol > 0 else 0.0

    if not floor_enabled:
        floor_threshold, floor_ok = None, True
    elif median_v is None:
        floor_threshold, floor_ok = None, False     # 履歴不足では床を満たしたことにしない
    else:
        floor_threshold = cfg.vol_floor_mult * median_v
        floor_ok = sum_vol >= floor_threshold

    if side == "S":
        dir_ok = sum_delta > 0
        prog = max(w0.h, w1.h, w2.h) - w2.h
        c = (ref == 0) or (w0.c < ref)
        d = w2.delta > 0
    elif side == "L":
        dir_ok = sum_delta < 0
        prog = w2.l - min(w0.l, w1.l, w2.l)
        c = (ref == 0) or (w0.c > ref)
        d = w2.delta < 0
    else:
        raise ValueError(f"side は S/L ({side})")

    a = ratio >= cfg.x_ratio and dir_ok and floor_ok
    b = (atr0 is not None) and (prog < cfg.atr_coef * atr0)
    prog_atr = (prog / atr0) if atr0 else None
    return SideDiag(
        side=side, sum_delta=sum_delta, sum_vol=sum_vol, ratio=ratio,
        floor_ok=floor_ok, floor_threshold=floor_threshold, dir_ok=dir_ok,
        prog=prog, prog_atr=prog_atr, a=a, b=b, c=c, d=d, verdict=_verdict(a, b, c, d),
    )


def judge(
    window: Sequence[WindowBar], atr0: float | None, cfg: ConfirmConfig,
    mode: str, ref_s: float, ref_l: float, median_v: float | None, floor_enabled: bool = True,
) -> Diagnosis:
    """両側を常に計算し (診断表は S側・L側を並べる)、総合判定は mode に従う。"""
    if mode not in ("S", "L", "both"):
        raise ValueError(f"mode は S/L/both ({mode})")
    s = judge_side(window, atr0, "S", ref_s, cfg, median_v, floor_enabled)
    l = judge_side(window, atr0, "L", ref_l, cfg, median_v, floor_enabled)
    if mode == "S":
        verdict, side = s.verdict, "S"
    elif mode == "L":
        verdict, side = l.verdict, "L"
    else:
        # dir_ok が排他なので同時に非沈黙になることはない。非沈黙の側を採る
        if s.verdict != "silent":
            verdict, side = s.verdict, "S"
        elif l.verdict != "silent":
            verdict, side = l.verdict, "L"
        else:
            verdict, side = "silent", None
    return Diagnosis(
        t=window[0].t, atr=atr0, mode=mode, ref_s=ref_s, ref_l=ref_l,
        floor_enabled=floor_enabled, median_v=median_v, S=s, L=l, verdict=verdict, verdict_side=side,
    )


# ---------------- 受け入れ警告 (SPEC §5.3。判定とは独立) ----------------
@dataclass(frozen=True)
class AcceptState:
    count_s: int = 0
    count_l: int = 0

    def step(self, close: float, ref_s: float, ref_l: float, n: int) -> tuple["AcceptState", str | None]:
        """
        確定足の終値で更新。S: C>ref_s が n 本連続 → n 本目で1回だけ "S"。L: C<ref_l → "L"。
        連続が途切れたらカウンタを 0 に戻す。ref==0 の側は数えない。
        """
        cs = self.count_s + 1 if (ref_s != 0 and close > ref_s) else 0
        cl = self.count_l + 1 if (ref_l != 0 and close < ref_l) else 0
        event: str | None = None
        if cs == n:
            event = "S"
        elif cl == n:
            event = "L"
        return AcceptState(count_s=cs, count_l=cl), event


# ---------------- 系列評価 (ライブ・リプレイ共通) ----------------
@dataclass(frozen=True)
class SeriesResult:
    diagnoses: list[Diagnosis] = field(default_factory=list)
    accept_events: dict = field(default_factory=dict)   # t(open ms) → "S"|"L"
    accept_counts: dict = field(default_factory=dict)   # t → (count_s, count_l)


def evaluate_series(
    bars: Sequence[WindowBar], atrs: Sequence[float | None], medians: Sequence[float | None],
    cfg: ConfirmConfig, mode: str, ref_s: float, ref_l: float, floor_enabled: bool = True,
) -> SeriesResult:
    """
    確定足列 (昇順) を順に流し、窓が揃った足ごとに判定する。
    atrs / medians は bars と同じ長さ (各足時点の値)。受け入れ警告は全足で更新する。
    """
    if not (len(bars) == len(atrs) == len(medians)):
        raise ValueError("bars / atrs / medians の長さが一致しない")
    out = SeriesResult()
    state = AcceptState()
    n = cfg.window_bars
    for i, b in enumerate(bars):
        state, ev = state.step(b.c, ref_s, ref_l, cfg.accept_consecutive)
        out.accept_counts[b.t] = (state.count_s, state.count_l)
        if ev:
            out.accept_events[b.t] = ev
        if i + 1 < n:
            continue
        window = [bars[i], bars[i - 1], bars[i - 2]]
        out.diagnoses.append(judge(window, atrs[i], cfg, mode, ref_s, ref_l, medians[i], floor_enabled))
    return out


# ---------------- 診断表 (Pine と同じ行構成。SPEC §5.4) ----------------
def diagnosis_rows(diag: Diagnosis, accept_counts: tuple[int, int] = (0, 0)) -> list[dict]:
    def fmt(x, nd=0):
        return "—" if x is None else f"{x:,.{nd}f}"

    def yn(v: bool) -> str:
        return "✓" if v else "✗"

    s, l = diag.S, diag.L
    floor_txt = lambda sd: ("無効" if not diag.floor_enabled else f"{yn(sd.floor_ok)} ΣV {fmt(sd.sum_vol)} / 床 {fmt(sd.floor_threshold)}")  # noqa: E731
    return [
        {"row": "Σδ", "S": fmt(s.sum_delta), "L": fmt(l.sum_delta)},
        {"row": "ΣV", "S": fmt(s.sum_vol), "L": fmt(l.sum_vol)},
        {"row": "攻め比率", "S": f"{s.ratio:.4f}", "L": f"{l.ratio:.4f}"},
        {"row": "床", "S": floor_txt(s), "L": floor_txt(l)},
        {"row": "向き整合", "S": yn(s.dir_ok), "L": yn(l.dir_ok)},
        {"row": "前進÷ATR", "S": f"{fmt(s.prog)} / {fmt(s.prog_atr, 3)} {yn(s.b)}", "L": f"{fmt(l.prog)} / {fmt(l.prog_atr, 3)} {yn(l.b)}"},
        {"row": "(c)位置", "S": yn(s.c) if diag.ref_s else "無効", "L": yn(l.c) if diag.ref_l else "無効"},
        {"row": "(d)1本目", "S": yn(s.d), "L": yn(l.d)},
        {"row": "受け入れカウント", "S": str(accept_counts[0]), "L": str(accept_counts[1])},
        {"row": "判定", "S": VERDICT_LABEL[s.verdict], "L": VERDICT_LABEL[l.verdict]},
    ]
