"""
calibration.py — 校正場面の正解データ (SPEC §8)。テストと記録スクリプトの単一の正。
δ・V は校正シート (ネイティブフットプリント) の実測値。ATR は 15m ATR14。
足のラベルは Europe/Paris の open 時刻。
"""

from __future__ import annotations

from dataclasses import dataclass

from .tz import paris_to_ms


@dataclass(frozen=True)
class FixtureBar:
    label: str      # Paris の open 時刻 "YYYY-MM-DD HH:MM"
    h: float
    l: float
    delta: float
    v: float
    atr: float

    @property
    def t(self) -> int:
        return paris_to_ms(self.label)


@dataclass(frozen=True)
class Scene:
    id: str
    mode: str            # "S" | "L"
    ref_s: float
    ref_l: float
    bars: tuple[FixtureBar, ...]
    # 窓 (label = 最新確定足) → 期待判定 "fire" | "rejected_c" | "silent"
    expected: dict
    note: str = ""
    recorded_delta_check: bool = True          # 記録済み実データで Σδ ±30% / 床有効の判定を検証するか
    accept_warning_label: str | None = None    # 受け入れ警告が出る足 (open, Paris)。None = 出ない


S1 = Scene(
    id="S1", mode="L", ref_s=0, ref_l=0,
    bars=(
        FixtureBar("2026-09-04 14:30", 81299, 79361, -5180, 33490, 329.5),
        FixtureBar("2026-09-04 14:45", 79664, 79126, 457, 11490, 344.4),
        FixtureBar("2026-09-04 15:00", 79546, 79139, -119, 4740, 348.9),
        FixtureBar("2026-09-04 15:15", 79457, 79170, -213, 2840, 344.4),
        FixtureBar("2026-09-04 15:30", 79513, 78913, -394, 6920, 362.7),
        FixtureBar("2026-09-04 15:45", 79670, 79193, -641, 6080, 370.9),
    ),
    expected={
        "2026-09-04 15:00": "fire",
        "2026-09-04 15:15": "silent",
        "2026-09-04 15:30": "fire",
        "2026-09-04 15:45": "fire",
    },
    note="ロング側・成功。出来高床は無効化して判定。校正 δ は TradingView のフットプリント (ティックルール分類) から転記 → Pine (reconstruct) とも taker とも定義が異なり kline から再現不能 (README 13) → 実データ検証は ATR のみ",
    recorded_delta_check=False,
)

S2 = Scene(
    id="S2", mode="S", ref_s=82300, ref_l=0,
    bars=(
        FixtureBar("2026-09-03 23:00", 81596, 81254, 756, 2720, 310.0),
        FixtureBar("2026-09-03 23:15", 81754, 81465, 1160, 2580, 308.4),
        FixtureBar("2026-09-03 23:30", 82282, 81472, 3006, 9860, 344.3),
        FixtureBar("2026-09-03 23:45", 81721, 81371, 151, 2100, 344.7),
        FixtureBar("2026-09-04 00:00", 81570, 81233, -40, 2300, 344.2),
        FixtureBar("2026-09-04 00:15", 81372, 81080, -609, 2770, 340.4),
    ),
    expected={
        "2026-09-03 23:30": "silent",
        "2026-09-03 23:45": "silent",
        "2026-09-04 00:00": "fire",
        "2026-09-04 00:15": "silent",
    },
    note="ショート側・成功。0:00 窓は δ 符号版 (d) で復活した基準ケース (ローソク色で判定すると失敗)",
)

S3 = Scene(
    id="S3", mode="S", ref_s=78396.9, ref_l=0,
    bars=(
        FixtureBar("2026-09-03 14:00", 78068, 77854, 685, 1380, 188.9),
        FixtureBar("2026-09-03 14:15", 78113, 77902, 25, 1210, 190.5),
        FixtureBar("2026-09-03 14:30", 78433, 77934, 3900, 6180, 212.5),
        FixtureBar("2026-09-03 14:45", 78750, 78205, 4630, 6270, 236.3),
        FixtureBar("2026-09-03 15:00", 78680, 78479, -887, 2640, 233.7),
        FixtureBar("2026-09-03 15:15", 78574, 78418, -516, 1990, 228.2),
    ),
    expected={
        "2026-09-03 14:30": "silent",
        "2026-09-03 14:45": "silent",
        "2026-09-03 15:00": "silent",
        "2026-09-03 15:15": "rejected_c",
    },
    note="ショート側・失敗 = 偽陽性を (c) が弾く。受け入れ警告は Binance 終値では 15:00 確定 (14:30 終値 78,278 < ref)",
    accept_warning_label="2026-09-03 15:00",
)

SCENES = {s.id: s for s in (S1, S2, S3)}

# 校正 δ の出所 (2026-09-08 実データ突き合わせ): S2/S3 は Pine v0.3.3 (reconstruct) と単位まで一致。
# S1 は別ソース由来で再現不能 → 既定の delta_method は reconstruct (config v0.3.4)。
# 追加の否定テスト: 8/28 03:15 CEST の天井直後 (mode=L)。スパイク足の δ が正のため (d) 不成立。
# フィクスチャ表は無く、記録済み実データでのみ検証する。
NEGATIVE_0828 = {"id": "NEG_0828", "mode": "L", "window_label": "2026-08-28 03:15", "note": "スパイク直後の成立L を出さない"}

# 記録スクリプトの取得範囲: 各場面の先頭足の前に ATR 収束 + 床 median のための本数、後ろに +16 本 (§9 の価格経路)
WARMUP_BARS_15M = 400
AFTER_BARS_15M = 16
