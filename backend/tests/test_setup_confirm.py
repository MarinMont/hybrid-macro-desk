"""
test_setup_confirm.py — §5 Confirm Engine / §8 受け入れテスト
S1/S2/S3 はフィクスチャ (δ・V・ATR) を直接入力して決定的に検証する (出来高床は無効化)。
記録済み実データがあれば、床有効・taker/reconstruct Σδ ±30%・ATR ±1%・受け入れ警告の終値も検証する。
"""

from __future__ import annotations

import pytest

from setup_console import atr as atrmod, calibration as cal, config, klines
from setup_console.confirm import (
    ACCEPT_TEXT, VERDICT_TEXT, AcceptState, WindowBar, diagnosis_rows, evaluate_series, judge,
)
from setup_console.tz import paris_label, paris_to_ms

CFG = config.load_config("v0.3.3")


# ---------------- フィクスチャ → WindowBar ----------------
def fixture_windowbars(scene: cal.Scene) -> list[WindowBar]:
    # 終値はフィクスチャに無い。(c) は S2 (全 H < ref) / S3 15:15 (L > ref) で H/L の範囲から一意に決まるので
    # 中値を置く。ref=0 の S1 では (c) は常に真。
    return [WindowBar(t=b.t, h=b.h, l=b.l, c=(b.h + b.l) / 2, v=b.v, delta=b.delta) for b in scene.bars]


def fixture_series(scene: cal.Scene):
    bars = fixture_windowbars(scene)
    atrs = [b.atr for b in scene.bars]
    medians = [None] * len(bars)
    return evaluate_series(bars, atrs, medians, CFG, scene.mode, scene.ref_s, scene.ref_l, floor_enabled=False)


def by_label(result):
    return {paris_label(d.t): d for d in result.diagnoses}


# ---------------- §8 S1/S2/S3: 判定 ----------------
@pytest.mark.parametrize("scene", list(cal.SCENES.values()), ids=lambda s: s.id)
def test_scene_verdicts_match_expected(scene):
    got = by_label(fixture_series(scene))
    for label, exp in scene.expected.items():
        assert got[label].verdict == exp, f"{scene.id} {label}: {got[label].verdict} != {exp}"


def test_s1_numbers():
    d = by_label(fixture_series(cal.S1))
    w = d["2026-09-04 15:00"].L
    assert w.sum_delta == pytest.approx(-4842)
    assert w.sum_vol == pytest.approx(49720)
    assert w.ratio == pytest.approx(0.0974, abs=5e-5)
    assert w.prog == pytest.approx(235)
    assert w.prog_atr == pytest.approx(0.674, abs=5e-4)
    assert (w.a, w.b, w.d) == (True, True, True)
    assert d["2026-09-04 15:15"].L.sum_delta == pytest.approx(125)
    assert not d["2026-09-04 15:15"].L.dir_ok
    w = d["2026-09-04 15:30"].L
    assert (w.sum_delta, w.ratio, w.prog) == (pytest.approx(-726), pytest.approx(0.0501, abs=5e-5), pytest.approx(226))
    assert w.prog_atr == pytest.approx(0.623, abs=5e-4)
    w = d["2026-09-04 15:45"].L
    assert (w.sum_delta, w.ratio, w.prog) == (pytest.approx(-1248), pytest.approx(0.0788, abs=5e-5), pytest.approx(257))
    assert w.prog_atr == pytest.approx(0.693, abs=5e-4)


def test_s2_numbers_and_delta_sign_rule():
    d = by_label(fixture_series(cal.S2))
    assert d["2026-09-03 23:30"].S.ratio == pytest.approx(0.3247, abs=5e-5)
    assert d["2026-09-03 23:30"].S.prog == pytest.approx(686)
    assert d["2026-09-03 23:30"].S.prog_atr == pytest.approx(1.99, abs=5e-3)
    assert not d["2026-09-03 23:30"].S.b
    assert d["2026-09-03 23:45"].S.ratio == pytest.approx(0.2969, abs=5e-5)
    assert d["2026-09-03 23:45"].S.prog == pytest.approx(528)
    assert d["2026-09-03 23:45"].S.prog_atr == pytest.approx(1.53, abs=5e-3)
    w = d["2026-09-04 00:00"].S
    assert w.sum_delta == pytest.approx(3117)
    assert w.ratio == pytest.approx(0.2186, abs=5e-5)
    assert w.prog == 0
    assert w.d and w.c and w.verdict == "fire"
    assert d["2026-09-04 00:15"].S.sum_delta == pytest.approx(-498)
    assert not d["2026-09-04 00:15"].S.dir_ok


def test_s2_d_uses_delta_sign_not_candle_color():
    # 23:30 足の δ は +3006。終値を open より下 (陰線) にしても (d) は変わらない。
    bars = fixture_windowbars(cal.S2)
    i = [paris_label(b.t) for b in bars].index("2026-09-03 23:30")
    red = WindowBar(t=bars[i].t, h=bars[i].h, l=bars[i].l, c=bars[i].l, v=bars[i].v, delta=bars[i].delta)
    bars[i] = red
    window = [bars[i + 2], bars[i + 1], bars[i]]   # 0:00 窓
    diag = judge(window, 344.2, CFG, "S", 82300, 0, None, floor_enabled=False)
    assert diag.S.d and diag.verdict == "fire"


def test_s3_numbers_and_c_rejection():
    d = by_label(fixture_series(cal.S3))
    assert d["2026-09-03 14:30"].S.prog_atr == pytest.approx(1.72, abs=5e-3)
    assert d["2026-09-03 14:45"].S.prog_atr == pytest.approx(2.70, abs=5e-3)
    assert d["2026-09-03 15:00"].S.prog_atr == pytest.approx(1.36, abs=5e-3)
    w = d["2026-09-03 15:15"].S
    assert w.a and w.b and w.d and w.prog == 0
    assert not w.c and w.verdict == "rejected_c"
    assert d["2026-09-03 15:15"].to_dict()["verdict_text"] == VERDICT_TEXT["rejected_c"]


# ---------------- 受け入れ警告 (合成) ----------------
def test_accept_warning_fires_once_at_n_and_resets():
    st = AcceptState()
    ref = 100.0
    events = []
    for close in [101, 102, 103, 99, 101, 101]:
        st, ev = st.step(close, ref, 0, 2)
        events.append(ev)
    assert events == [None, "S", None, None, None, "S"]


def test_accept_warning_l_side_and_ref_zero_disabled():
    st = AcceptState()
    st, ev1 = st.step(90, 0, 100, 2)
    st, ev2 = st.step(90, 0, 100, 2)
    assert (ev1, ev2) == (None, "L")
    st = AcceptState()
    st, ev = st.step(90, 0, 0, 1)
    assert ev is None and st.count_l == 0


def test_accept_text_fixed():
    assert ACCEPT_TEXT["S"].startswith("受け入れ↑")
    assert ACCEPT_TEXT["L"].startswith("受け入れ↓")


# ---------------- 出来高床 (合成。週末・休場で沈黙になる) ----------------
def test_floor_silences_low_volume():
    bars = fixture_windowbars(cal.S1)
    i = [paris_label(b.t) for b in bars].index("2026-09-04 15:30")
    window = [bars[i], bars[i - 1], bars[i - 2]]     # ΣV = 14500
    ok = judge(window, 362.7, CFG, "L", 0, 0, median_v=4000.0)     # 床 12000 ≤ 14500
    assert ok.verdict == "fire" and ok.L.floor_ok
    low = judge(window, 362.7, CFG, "L", 0, 0, median_v=5000.0)    # 床 15000 > 14500
    assert low.verdict == "silent" and not low.L.floor_ok and not low.L.a
    nomed = judge(window, 362.7, CFG, "L", 0, 0, median_v=None)    # 履歴不足 → 床を満たさない
    assert nomed.verdict == "silent"


# ---------------- 文言・診断表 ----------------
def test_verdict_text_has_no_forbidden_words():
    banned = ("シグナル", "エントリー推奨", "ロング推奨", "ショート推奨", "Buy", "Sell", "買い時", "売り時")
    for txt in list(VERDICT_TEXT.values()) + list(ACCEPT_TEXT.values()):
        assert not any(b in txt for b in banned), txt


def test_diagnosis_rows_shape():
    d = by_label(fixture_series(cal.S2))["2026-09-04 00:00"]
    rows = diagnosis_rows(d, (0, 0))
    assert [r["row"] for r in rows] == ["Σδ", "ΣV", "攻め比率", "床", "向き整合", "前進÷ATR", "(c)位置", "(d)1本目", "受け入れカウント", "判定"]
    assert rows[-1]["S"] == "成立" and rows[-1]["L"] == "沈黙"
    assert d.to_dict()["verdict_text"] == "指値を残してよい"


def test_judge_rejects_wrong_window_and_mode():
    bars = fixture_windowbars(cal.S1)
    with pytest.raises(ValueError):
        judge(bars[:2], 300.0, CFG, "L", 0, 0, None, floor_enabled=False)
    with pytest.raises(ValueError):
        judge(bars[:3], 300.0, CFG, "X", 0, 0, None, floor_enabled=False)


# ================= 記録済み実データ (無ければ skip) =================
def recorded_series(doc: dict, scene: cal.Scene, method: str, floor_enabled: bool):
    bars15 = klines.parse_klines(doc["klines_15m"])
    bars1 = klines.parse_klines(doc.get("klines_1m", []))
    ds = klines.deltas(bars15, method, bars1)
    atrs = atrmod.atr_series(bars15, CFG.atr_len)
    meds = [klines.volume_median(bars15[: i + 1], CFG.vol_median_len) for i in range(len(bars15))]
    wbs = [WindowBar(t=b.t, h=b.h, l=b.l, c=b.c, v=b.v, delta=(d if d is not None else 0.0)) for b, d in zip(bars15, ds)]
    return bars15, ds, atrs, evaluate_series(wbs, atrs, meds, CFG, scene.mode, scene.ref_s, scene.ref_l, floor_enabled)


@pytest.mark.parametrize("scene", list(cal.SCENES.values()), ids=lambda s: s.id)
def test_recorded_atr_within_1pct(recorded, scene):
    doc = recorded(scene.id)
    bars15, _, atrs, _ = recorded_series(doc, scene, "taker", True)
    idx = {b.t: i for i, b in enumerate(bars15)}
    for fb in scene.bars:
        i = idx[fb.t]
        assert atrs[i] is not None
        assert abs(atrs[i] - fb.atr) / fb.atr <= 0.01, f"{scene.id} {fb.label}: ATR {atrs[i]:.1f} vs 校正 {fb.atr}"


@pytest.mark.parametrize("method", ["taker", "reconstruct"])
@pytest.mark.parametrize("scene", list(cal.SCENES.values()), ids=lambda s: s.id)
def test_recorded_sum_delta_sign_and_30pct(recorded, scene, method):
    doc = recorded(scene.id)
    _, _, _, res = recorded_series(doc, scene, method, floor_enabled=False)
    got = by_label(res)
    fx = by_label(fixture_series(scene))
    for label in scene.expected:
        side = scene.mode
        g, f = getattr(got[label], side).sum_delta, getattr(fx[label], side).sum_delta
        assert (g > 0) == (f > 0), f"{scene.id} {label} {method}: 符号不一致 Σδ={g:.0f} vs 校正 {f:.0f}"
        assert abs(g - f) <= 0.30 * abs(f), f"{scene.id} {label} {method}: Σδ={g:.0f} が校正 {f:.0f} の ±30% 外"


@pytest.mark.parametrize("scene", list(cal.SCENES.values()), ids=lambda s: s.id)
def test_recorded_verdicts_with_real_floor(recorded, scene):
    doc = recorded(scene.id)
    _, _, _, res = recorded_series(doc, scene, "taker", floor_enabled=True)
    got = by_label(res)
    for label, exp in scene.expected.items():
        d = got[label]
        assert d.median_v is not None, "床 median の履歴不足"
        assert d.verdict == exp, f"{scene.id} {label}: 床有効で {d.verdict} != {exp} (ΣV={getattr(d, scene.mode).sum_vol:.0f}, 床={getattr(d, scene.mode).floor_threshold:.0f})"


def test_recorded_s3_accept_warning_at_1445(recorded):
    doc = recorded("S3")
    _, _, _, res = recorded_series(doc, cal.S3, "taker", True)
    t = paris_to_ms("2026-09-03 14:45")
    assert res.accept_events.get(t) == "S", f"14:45 に受け入れ↑ が出ていない: {res.accept_events}"


def test_recorded_s2_no_accept_warning(recorded):
    doc = recorded("S2")
    _, _, _, res = recorded_series(doc, cal.S2, "taker", True)
    scene_ts = set(doc["scene_open_utc_ms"])
    assert not (scene_ts & set(res.accept_events)), "S2 では受け入れ警告なし (23:30 の高値はヒゲのみ)"


def test_recorded_negative_0828_no_fire_L(recorded):
    doc = recorded("NEG_0828")
    bars15 = klines.parse_klines(doc["klines_15m"])
    ds = klines.deltas(bars15, "taker")
    atrs = atrmod.atr_series(bars15, CFG.atr_len)
    meds = [klines.volume_median(bars15[: i + 1], CFG.vol_median_len) for i in range(len(bars15))]
    wbs = [WindowBar(t=b.t, h=b.h, l=b.l, c=b.c, v=b.v, delta=d) for b, d in zip(bars15, ds)]
    res = evaluate_series(wbs, atrs, meds, CFG, "L", 0, 0, floor_enabled=False)
    t = paris_to_ms(cal.NEGATIVE_0828["window_label"])
    d = {x.t: x for x in res.diagnoses}[t]
    assert d.verdict != "fire", "スパイク直後に成立L を出してはいけない ((d) が δ 符号で不成立)"
    assert not d.L.d
