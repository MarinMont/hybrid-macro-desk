"""
replay.py — リプレイ (SPEC §9): 台帳データ収集用
日付範囲と帯設定を指定して過去の 15 分足を順に流し、到達・判定・受け入れ警告を Touch 行として自動生成する。
  - verdict / first_fire_ts / accept_warning_ts を埋める。outcome_* は人間が記入
  - 先読み防止 (§3.3) を強制: 各時点で valid_from 未到達の水準は無視
  - 到達から +16 本の価格経路 (終値) を付ける (4 時間後・構造判定の補助)
  - CSV は Touch モデルのフィールド順 (Google Sheet C表の列順は未回答 → README 未回答事項 12)
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, fields
from typing import Sequence

from . import atr as atrmod, klines
from .config import ConfirmConfig
from .confirm import WindowBar, evaluate_series
from .ledger import Ledger, Touch, detect_touches, span_to_touch, targets_from_ledger
from .tz import paris_label

PATH_BARS = 16
CSV_COLUMNS = [f.name for f in fields(Touch)] + ["path_16_close"]


def replay(
    bars15: Sequence[klines.Bar], cfg: ConfirmConfig, ledger: Ledger, from_ms: int, to_ms: int,
    bars1: Sequence[klines.Bar] | None = None, floor_enabled: bool = True,
) -> list[dict]:
    """bars15 は昇順の確定足 (範囲より前に ATR 収束 + 床 median のための履歴を含めて渡す)。"""
    bars = list(bars15)
    if not bars:
        return []
    ds = klines.deltas(bars, cfg.delta_method, bars1 or [])
    atrs = atrmod.atr_series(bars, cfg.atr_len)
    meds = [klines.volume_median(bars[: i + 1], cfg.vol_median_len) for i in range(len(bars))]
    wbs = [WindowBar(t=b.t, h=b.h, l=b.l, c=b.c, v=b.v, delta=(d if d is not None else 0.0)) for b, d in zip(bars, ds)]
    idx = {b.t: i for i, b in enumerate(bars)}
    closes = [b.c for b in bars]

    targets = targets_from_ledger(ledger)
    band_ref = {b.id: (b.ref_level or 0.0) for b in ledger.bands}
    spans = [s for s in detect_touches(bars, targets, enforce_valid=True) if from_ms <= s.start_t <= to_ms]

    # (mode, ref) の組ごとに系列評価をキャッシュ
    cache: dict[tuple[str, float], object] = {}

    def series_for(mode: str, ref: float):
        key = (mode, ref)
        if key not in cache:
            cache[key] = evaluate_series(wbs, atrs, meds, cfg, mode, ref if mode == "S" else 0.0, ref if mode == "L" else 0.0, floor_enabled)
        return cache[key]

    rows: list[dict] = []
    for n, sp in enumerate(spans, 1):
        ref = band_ref.get(sp.target_id, 0.0)
        res = series_for(sp.side, ref)
        by_t = {d.t: d for d in res.diagnoses}
        fires = [t for t in sp.bars_t if by_t.get(t) and by_t[t].verdict == "fire"]
        rejects = [t for t in sp.bars_t if by_t.get(t) and by_t[t].verdict == "rejected_c"]
        accepts = [t for t in sp.bars_t if res.accept_events.get(t) == sp.side]
        touch = span_to_touch(sp, True, f"R{n:04d}")
        touch.verdict = "confirmed" if fires else "rejected_c" if rejects else "silent"
        touch.first_fire_ts = paris_label(fires[0]) if fires else None
        touch.accept_warning_ts = paris_label(accepts[0]) if accepts else None
        i0 = idx[sp.start_t]
        path = closes[i0 + 1: i0 + 1 + PATH_BARS]
        rows.append({**asdict(touch), "path_16_close": path, "ref_used": ref,
                     "verdicts": [{"open_local": paris_label(t), "verdict": by_t[t].verdict} for t in sp.bars_t if t in by_t]})
    return rows


def to_csv(rows: Sequence[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        w.writerow([("|".join(f"{x:.1f}" for x in r[c]) if c == "path_16_close" else ("" if r.get(c) is None else r.get(c))) for c in CSV_COLUMNS])
    return buf.getvalue()
