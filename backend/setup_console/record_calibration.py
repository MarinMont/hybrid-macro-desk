"""
record_calibration.py — 校正場面の Binance 実データを記録する (Mac で実行)
==========================================================================
サンドボックス (Claude Code on the web) からは Binance に到達できないため、
このスクリプトをローカルで実行して tests/fixtures/calibration/<scene>.json を生成・コミットする。
テストは記録済みファイルを読み、床 median / taker Σδ ±30% / ATR ±1% / 受け入れ警告の終値 を検証する。

    cd backend && python -m setup_console.record_calibration [--out tests/fixtures/calibration]

取得内容 (場面ごと):
  - 15m klines: 先頭足の 400 本前 〜 末尾足の 16 本後
  - 1m  klines: 場面の足の範囲 (reconstruct 方式の検証用)
発注 API には接続しない。公開 klines のみ (キー不要)。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

import httpx

from . import calibration as cal
from .tz import paris_to_ms

FAPI = "https://fapi.binance.com"
SYMBOL = "BTCUSDT"
LIMIT = 1500  # fapi klines の上限


async def fetch_klines(http: httpx.AsyncClient, interval: str, start_ms: int, end_ms: int) -> list[list]:
    out: list[list] = []
    cur = start_ms
    while cur < end_ms:
        r = await http.get(
            f"{FAPI}/fapi/v1/klines",
            params={"symbol": SYMBOL, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": LIMIT},
        )
        r.raise_for_status()
        chunk = r.json()
        if not chunk:
            break
        out.extend(chunk)
        last_open = int(chunk[-1][0])
        if len(chunk) < LIMIT:
            break
        cur = last_open + 1
    # 重複除去 (openTime でユニーク化、昇順)
    seen: dict[int, list] = {}
    for k in out:
        seen[int(k[0])] = k
    return [seen[t] for t in sorted(seen)]


def scene_ranges(scene: cal.Scene) -> tuple[int, int, int, int]:
    first, last = scene.bars[0].t, scene.bars[-1].t
    s15 = first - cal.WARMUP_BARS_15M * 900_000
    e15 = last + (cal.AFTER_BARS_15M + 1) * 900_000
    return s15, e15, first, last + 900_000


async def record(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rc = 0
    async with httpx.AsyncClient(timeout=20.0) as http:
        for scene in cal.SCENES.values():
            s15, e15, s1, e1 = scene_ranges(scene)
            try:
                k15 = await fetch_klines(http, "15m", s15, e15)
                k1 = await fetch_klines(http, "1m", s1, e1)
            except Exception as e:  # noqa: BLE001
                print(f"[{scene.id}] 取得失敗: {e}", file=sys.stderr)
                rc = 1
                continue
            doc = {
                "scene": scene.id,
                "symbol": SYMBOL,
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "scene_open_utc_ms": [b.t for b in scene.bars],
                "klines_15m": k15,
                "klines_1m": k1,
            }
            p = out_dir / f"{scene.id}.json"
            p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            print(f"[{scene.id}] 15m {len(k15)}本 / 1m {len(k1)}本 → {p}")

        # 否定テスト (8/28 天井直後): 03:15 CEST の窓 + 前後
        t = paris_to_ms(cal.NEGATIVE_0828["window_label"])
        try:
            k15 = await fetch_klines(http, "15m", t - cal.WARMUP_BARS_15M * 900_000, t + 17 * 900_000)
            k1 = await fetch_klines(http, "1m", t - 3 * 900_000, t + 900_000)
            doc = {
                "scene": cal.NEGATIVE_0828["id"], "symbol": SYMBOL,
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "scene_open_utc_ms": [t], "klines_15m": k15, "klines_1m": k1,
            }
            p = out_dir / f"{cal.NEGATIVE_0828['id']}.json"
            p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            print(f"[NEG_0828] 15m {len(k15)}本 / 1m {len(k1)}本 → {p}")
        except Exception as e:  # noqa: BLE001
            print(f"[NEG_0828] 取得失敗: {e}", file=sys.stderr)
            rc = 1
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="校正場面の Binance klines を記録する")
    default_out = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "calibration"
    ap.add_argument("--out", default=os.getenv("CALIBRATION_OUT", str(default_out)))
    args = ap.parse_args()
    return asyncio.run(record(Path(args.out)))


if __name__ == "__main__":
    sys.exit(main())
