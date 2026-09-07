# Hybrid Macro Desk — BTC

Hyperliquid BTC無期限先物のための**環境認識ダッシュボード**。
売買推奨はせず、状況の説明に徹する。すべての判断と執行はトレーダー自身の拒否権に従う（**GO but WAIT**）。

> 仕様の正は [`handoff/SPEC.md`](handoff/SPEC.md) と [`handoff/API_DESIGN.md`](handoff/API_DESIGN.md)。
> リポジトリ規約は [`CLAUDE.md`](CLAUDE.md)。

## 構成

```
frontend/   Vite + React 18。ダッシュボード (src/App.jsx) と、#/setup で切り替わるセットアップコンソール (src/SetupConsole.jsx)
backend/    FastAPI。liqmap_service.py (HL清算マップ+Smart Money) + collectors/ (market/derivs/macro/ai/entry_state/setup_console)
            setup_console/ (コンソールの純関数群) / config/ (校正値、バージョン付き) / data/ (台帳 JSON、ローカル生成物)
handoff/    SPEC.md / API_DESIGN.md (ダッシュボード仕様) / SETUP_CONSOLE_SPEC.md (コンソール仕様)
```

## クイックスタート

### バックエンド (ポート 8787)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # ANTHROPIC_API_KEY / FINNHUB_API_KEY を記入 (任意)
uvicorn liqmap_service:app --port 8787 --reload
```

疎通確認:

```bash
curl localhost:8787/api/health
curl localhost:8787/api/market
curl localhost:8787/api/derivs
curl localhost:8787/api/macro
```

### フロントエンド

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

フロントは起動時に `localhost:8787` を自動検出する。
- バックエンド稼働時: 各パネルがライブデータ（清算マップはカバー率、Positioning/Macroはサンプルタグが消える）。
- バックエンド停止時: フロントはサンプルデータへフォールバックして動作を継続する。

## 設計思想 (実装判断の拠り所)

1. **環境認識ツールであり、売買推奨をしない。**
2. **GO but WAIT。** Entry EngineがTRIGGEREDでも最終判断は人間の拒否権。
3. **見えないものを見えるフリをしない。** 清算マップはカバー率を常に表示。サンプルには必ずサンプルタグ。
4. **判定ロジックの一元化。** ステートマシン/レジーム判定は既存Entry Consoleが正。本アプリは表示のみ（疎結合）。

## データソース (全て無料)

Hyperliquid public API / Binance Futures 公開API / Alternative.me Fear&Greed / Finnhub 経済カレンダー /
Deribit満期の自前計算 / Anthropic API（AI概況・カレンダー注釈、バックエンド経由）。

## デプロイ (Vercel + Render)

- **backend** → Render Web Service (Starter・常時稼働)。設定は直下の `render.yaml`。
- **frontend** → Vercel。Root Directory を `frontend` に指定し、環境変数 **`VITE_API_BASE`** に
  Render のバックエンドURL（例 `https://xxx.onrender.com`）を設定する。フロントはこの1か所だけを見る。
- 非エンジニア向けのクリック手順: [`handoff/DEPLOY_STEPS.md`](handoff/DEPLOY_STEPS.md)（設計方針は [`handoff/DEPLOY.md`](handoff/DEPLOY.md)）。

## セットアップコンソール (`#/setup`)

仕様は [`handoff/SETUP_CONSOLE_SPEC.md`](handoff/SETUP_CONSOLE_SPEC.md)。トレードを実行せず、判断もしない。
人間のルールで算術を検算し、POI到達時に吸収コンファーム (Pine v0.3.3 移植) を機械判定し、台帳に記録する。
緑のランプは「入れ」ではなく「消さなくてよい」。

- 校正値: `backend/config/confirm_v0.3.3.json` (上書き禁止。変更は新ファイル追加)
- 台帳/フロー/履歴: `backend/data/*.json` (`SETUP_CONSOLE_DATA_DIR` で変更可)
- 校正場面の実データ記録 (Mac で実行): `cd backend && python -m setup_console.record_calibration`
  → `backend/tests/fixtures/calibration/` に klines を保存し、床 median / taker Σδ / ATR / 終値の検証テストが有効になる

### 未回答事項 (仕様 §12: 推測で埋めない。回答があるまで該当水準は inactive)
台帳の値:
1. L03 フィボ0 (81,500.0) `valid_from` 2026-08-29 — 要確認
2. L04 上POI帯上端 (81,270.5) `born_on` / `valid_from` — 要記入
3. L05 0.236 (77,068.8) `valid_from` 2026-08-29 — 要確認
4. L07 Key Low (75,558.7) `born_on` / `valid_from` — 要記入
5. P_MID / P_LOWER の `ref_level` 具体値 ((c)判定に直結)
6. P_MID / P_LOWER の SL 具体値 (P_UPPER は 83,500)
7. L01 `born_on` "2026-05" は月のみ (暫定で 2026-05-01 を保持し、表示は「2026-05」)

仕様の確認:
8. §5.3 受け入れ警告の検証に終値が必要 — フィクスチャに C 列が無いため、記録済み実データで検証する方式を採用
9. §4 週末ATR「直近フル流動性セッション」の定義 (暫定: 直近の平日 (月〜金 Europe/Paris) 最終確定 1H 足時点の ATR を参考表示)
10. §2.2 台帳 `ts_local` は足の開始時刻で記録し、確定時刻を併記 (暫定)
11. §7 月末最終営業日の「営業日」は US 営業日 (土日 + `calendar.json` の休場日を除く) と暫定解釈
12. §9 CSV の列順: Google Sheet「POI水準台帳 v3」C表の列順が不明 — Touch モデルのフィールド順で暫定出力

## 免責

本画面は環境認識ツールであり、投資助言ではありません。判断と執行は常にトレーダー自身が行ってください。
