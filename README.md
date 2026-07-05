# Hybrid Macro Desk — BTC

Hyperliquid BTC無期限先物のための**環境認識ダッシュボード**。
売買推奨はせず、状況の説明に徹する。すべての判断と執行はトレーダー自身の拒否権に従う（**GO but WAIT**）。

> 仕様の正は [`handoff/SPEC.md`](handoff/SPEC.md) と [`handoff/API_DESIGN.md`](handoff/API_DESIGN.md)。
> リポジトリ規約は [`CLAUDE.md`](CLAUDE.md)。

## 構成

```
frontend/   Vite + React 18。単一ページ (src/App.jsx)。全パネルの描画。
backend/    FastAPI。liqmap_service.py (HL清算マップ+Smart Money) + collectors/ (market/derivs/macro/ai/entry_state)
handoff/    SPEC.md / API_DESIGN.md (仕様書)
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

## 免責

本画面は環境認識ツールであり、投資助言ではありません。判断と執行は常にトレーダー自身が行ってください。
