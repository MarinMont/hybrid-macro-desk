"""
flow.py — 執行フロー (SPEC §6)。旧 Entry Console の状態機械とは別物として並置する。
各遷移は**人間のチェック操作**で進む。自動遷移は「価格が帯に到達」「窓のカウント」「タイマー」のみ。
UI は各状態で「いま人間がやること」を1行で表示する。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .store import data_path, read_json, write_json_atomic
from .tz import paris_label

STATES = (
    "IDLE", "PLACED", "AT_POI", "PULLED_EVENT", "PULLED_WEEKEND",
    "CONFIRMED", "REJECTED", "FILLED_UNCONFIRMED", "MANAGE", "EXIT_PLAN", "CLOSED",
)

# 人間のチェック操作で進める遷移 (from → {to: 説明})
HUMAN_TRANSITIONS: dict[str, dict[str, str]] = {
    "IDLE": {"PLACED": "敷設済み (算術OK かつ カレンダーゲート開)"},
    "PLACED": {
        "PULLED_EVENT": "イベント窓に入る → 未約定を撤去",
        "PULLED_WEEKEND": "金曜引け → 未約定を撤去",
        "AT_POI": "帯に到達 (手動で記録)",
        "IDLE": "撤去してリセット",
    },
    "AT_POI": {
        "CONFIRMED": "成立 → 未約定を残す",
        "REJECTED": "(c)棄却 / 受け入れ警告 / 不成立のまま帯離脱 → 未約定撤去",
        "FILLED_UNCONFIRMED": "約定通知 (人間が入力)",
    },
    "CONFIRMED": {
        "FILLED_UNCONFIRMED": "約定通知 (人間が入力)",
        "REJECTED": "その後の (c)棄却 / 受け入れ警告 → 未約定撤去",
        "IDLE": "撤去してリセット",
    },
    "FILLED_UNCONFIRMED": {
        "MANAGE": "成立 → 管理へ",
        "EXIT_PLAN": "(c)棄却 / 受け入れ警告 → 撤退計画",
    },
    "MANAGE": {"CLOSED": "決済済み"},
    "EXIT_PLAN": {"CLOSED": "決済済み (撤退指値 / 時間スクラッチ / SL)"},
    "REJECTED": {"IDLE": "全部引いた → リセット"},
    "PULLED_EVENT": {"IDLE": "窓が閉じた → リセット (再敷設は IDLE から)"},
    "PULLED_WEEKEND": {"IDLE": "月曜 → リセット"},
    "CLOSED": {"IDLE": "台帳に結果を記入した → リセット"},
}

# 自動遷移 (エンジン/価格/タイマーが起こす)
AUTO_TRANSITIONS = {
    ("PLACED", "AT_POI"): "価格が帯に到達",
    ("FILLED_UNCONFIRMED", "EXIT_PLAN"): "4窓経過で成立も棄却もなし (時間スクラッチ)",
}

TODO_TEXT = {
    "IDLE": "算術OK かつ カレンダーゲート開 を確認してから敷設する",
    "PLACED": "指値は帯・SL・ロットの算術値で敷設済みか",
    "AT_POI": "15分の確定ごとに判定を見る。形成中は見ない",
    "PULLED_EVENT": "未約定は撤去済みか。窓が閉じるまで待つ",
    "PULLED_WEEKEND": "未約定は撤去済みか。月曜に IDLE へ戻す",
    "CONFIRMED": "未約定を残す。15分の確定ごとに判定を見続ける",
    "REJECTED": "未約定を全部引く",
    "FILLED_UNCONFIRMED": "SLは動かさない。枠を足さない",
    "MANAGE": "SLは動かさない。計画どおりに管理する",
    "EXIT_PLAN": "次の戻りで建値±0.25×ATR。戻らなければ時間スクラッチ、最悪SL",
    "CLOSED": "台帳に結果 (4h後・構造) を記入する",
}

EXIT_ATR_MULT = 0.25      # 撤退指値 = 建値 ± 0.25×ATR
TIME_SCRATCH_WINDOWS = 4  # FILLED_UNCONFIRMED で成立も棄却もない窓が 4 → EXIT_PLAN
TIME_SCRATCH_TIMER_MS = 3_600_000  # 1H 時間スクラッチのタイマー


@dataclass
class FlowState:
    state: str = "IDLE"
    since_utc: int = 0
    band_id: str | None = None
    side: str | None = None
    sl: float | None = None              # PLACED 時に固定 (変更不可)
    avg_entry: float | None = None
    windows_since_fill: int = 0          # FILLED_UNCONFIRMED 以降に評価した確定窓の数
    last_verdict: str | None = None
    exit_plan: dict | None = None        # {price_upper, price_lower, timer_end_utc}
    suggested: dict | None = None        # エンジンが示す次の遷移 {to, reason}
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["since_local"] = paris_label(self.since_utc) if self.since_utc else None
        d["todo"] = TODO_TEXT[self.state]
        d["allowed"] = [{"to": to, "label": lbl} for to, lbl in HUMAN_TRANSITIONS.get(self.state, {}).items()]
        return d


FLOW_FILE = "flow.json"


def load_flow(path=None) -> FlowState:
    d = read_json(path or data_path(FLOW_FILE), None)
    if d is None:
        return FlowState()
    d = {k: v for k, v in d.items() if k in FlowState.__dataclass_fields__}
    return FlowState(**d)


def save_flow(fs: FlowState, path=None) -> None:
    write_json_atomic(path or data_path(FLOW_FILE), asdict(fs))


class TransitionError(ValueError):
    pass


def _enter(fs: FlowState, to: str, now_ms: int, who: str, note: str) -> FlowState:
    fs.history.append({"from": fs.state, "to": to, "at_utc": now_ms, "at_local": paris_label(now_ms), "by": who, "note": note})
    fs.history = fs.history[-200:]
    fs.state = to
    fs.since_utc = now_ms
    fs.suggested = None
    if to == "IDLE":
        fs.band_id = fs.side = fs.sl = fs.avg_entry = None
        fs.windows_since_fill = 0
        fs.last_verdict = None
        fs.exit_plan = None
    if to == "FILLED_UNCONFIRMED":
        fs.windows_since_fill = 0
    return fs


def advance(
    fs: FlowState, to: str, now_ms: int, note: str = "", *,
    arith_ok: bool | None = None, gate_open: bool | None = None,
    band_id: str | None = None, side: str | None = None, sl: float | None = None, avg_entry: float | None = None,
    atr_1h: float | None = None,
) -> FlowState:
    """人間のチェック操作。許可された遷移のみ。IDLE→PLACED は算術OK かつ ゲート開 が条件。"""
    if to not in STATES:
        raise TransitionError(f"不明な状態 {to}")
    allowed = HUMAN_TRANSITIONS.get(fs.state, {})
    if to not in allowed:
        raise TransitionError(f"{fs.state} → {to} は人間の操作で進めない")
    if fs.state == "IDLE" and to == "PLACED":
        if not arith_ok:
            raise TransitionError("算術に赤フラグがある (敷設不可)")
        if not gate_open:
            raise TransitionError("カレンダーゲートが閉じている (敷設不可)")
        if not band_id or side not in ("S", "L") or sl is None:
            raise TransitionError("帯・方向・SL を指定する")
        fs.band_id, fs.side, fs.sl, fs.avg_entry = band_id, side, sl, avg_entry
    if to == "EXIT_PLAN":
        fs.exit_plan = make_exit_plan(fs.avg_entry, atr_1h, now_ms)
    return _enter(fs, to, now_ms, "human", note)


def make_exit_plan(avg_entry: float | None, atr: float | None, now_ms: int) -> dict:
    """建値 ± 0.25×ATR の撤退指値と 1H 時間スクラッチのタイマー。ATR は 1H ATR (SL設計用)。"""
    plan = {"timer_end_utc": now_ms + TIME_SCRATCH_TIMER_MS, "timer_end_local": paris_label(now_ms + TIME_SCRATCH_TIMER_MS)}
    if avg_entry is not None and atr:
        plan["price_upper"] = avg_entry + EXIT_ATR_MULT * atr
        plan["price_lower"] = avg_entry - EXIT_ATR_MULT * atr
    return plan


# ---------------- 自動遷移 (エンジン/価格が起こす) ----------------
def on_touch(fs: FlowState, band_id: str, bar_open_ms: int) -> FlowState:
    """PLACED 中に flow の帯へ価格が到達 → AT_POI。"""
    if fs.state == "PLACED" and fs.band_id == band_id and bar_open_ms >= fs.since_utc:
        return _enter(fs, "AT_POI", bar_open_ms + 900_000, "auto", AUTO_TRANSITIONS[("PLACED", "AT_POI")])
    return fs


def on_verdict(fs: FlowState, verdict: str, accept_event: str | None, bar_open_ms: int, atr_1h: float | None) -> FlowState:
    """
    確定足ごとの判定を受けて、提案 (suggested) を更新する。遷移自体は人間が行う。
    例外: FILLED_UNCONFIRMED で 4 窓経過して成立も棄却もなし → EXIT_PLAN (自動)。
    """
    fs.last_verdict = verdict
    if fs.state in ("AT_POI", "CONFIRMED"):
        if verdict == "fire" and fs.state == "AT_POI":
            fs.suggested = {"to": "CONFIRMED", "reason": "成立 — 未約定を残してよい"}
        elif verdict == "rejected_c" or accept_event:
            fs.suggested = {"to": "REJECTED", "reason": "(c)棄却 / 受け入れ警告 — 未約定を撤去"}
    elif fs.state == "FILLED_UNCONFIRMED":
        fs.windows_since_fill += 1
        if verdict == "fire":
            fs.suggested = {"to": "MANAGE", "reason": "成立"}
        elif verdict == "rejected_c" or accept_event:
            fs.suggested = {"to": "EXIT_PLAN", "reason": "(c)棄却 / 受け入れ警告 — 撤退計画へ"}
        elif fs.windows_since_fill >= TIME_SCRATCH_WINDOWS:
            fs.exit_plan = make_exit_plan(fs.avg_entry, atr_1h, bar_open_ms + 900_000)
            return _enter(fs, "EXIT_PLAN", bar_open_ms + 900_000, "auto", AUTO_TRANSITIONS[("FILLED_UNCONFIRMED", "EXIT_PLAN")])
    return fs


def on_band_left(fs: FlowState, band_id: str) -> FlowState:
    """AT_POI で不成立のまま帯離脱 → REJECTED を提案 (人間が確定)。"""
    if fs.state == "AT_POI" and fs.band_id == band_id and fs.suggested is None:
        fs.suggested = {"to": "REJECTED", "reason": "不成立のまま帯離脱 — 未約定を撤去"}
    return fs
