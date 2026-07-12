# Hybrid Macro Desk — Backend (HL実データ清算マップ + Smart Money)

## 1. Coinglass について (現方針: 契約しない)

本プロジェクトは全データを無料ソース(Hyperliquid / Binance公開API / Alternative.me / Finnhub)で
賄う構成に確定した。清算履歴(ロング/ショート別清算額)が必要な時はCoinglassのサイトを目視する。

将来API契約する場合に備えたプロキシ雛形は liqmap_service.py に残してある
(`COINGLASS_API_KEY` 未設定なら該当エンドポイントが503を返すだけで、他機能に影響なし)。
契約時の手順: ダッシュボードでキー発行 → `.env` に記載 → curlで疎通確認。
キーはバックエンドの .env のみに置き、フロントとgitには絶対に入れない。

## 2. HL清算マップサービスの起動

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # キーを記入
uvicorn liqmap_service:app --port 8787
```

起動後の確認:
- `http://localhost:8787/api/health` — 走査済みアドレス数・ポジション数
- `http://localhost:8787/api/liq-map` — 清算マップ本体 (起動後1〜2分でカバー率が立ち上がる)
- `http://localhost:8787/api/coinglass/liquidation-history?symbol=BTC&interval=1h`

ダッシュボード(.jsx)はローカルで開くと自動的に `localhost:8787/api/liq-map` を検出し、
清算マップパネルが「サンプル」から「HL実データ (カバー率xx%)」に切り替わる。

### 追加エンドポイント (collectors/)

`liqmap_service.py` は起動時に `collectors/` の各モジュールを取り込み、以下を配信する
(契約は `handoff/API_DESIGN.md §1`)。いずれも外部API不達時はパネル単位でフォールバックし、
画面全体を落とさない。

| Path | 内容 | ソース |
|---|---|---|
| `GET /api/market` | price/chg24/candles[320]/fundingApr/oiUsd/vol24Usd/fundingCompare/fng | HL candleSnapshot・metaAndAssetCtxs・predictedFundings・Alternative.me |
| `GET /api/derivs` | lsGlobalLongPct/lsTopLongPct/takerSeries[24]/cvd[24]/oiHist[48]/oiChangePct24h | Binance fapi 公開4本 |
| `GET /api/macro` | events[]/warning{active,eventName,ts} | Finnhub・Deribit満期計算・Claude注釈(日次) |
| `POST /api/ai-overview` | メトリクスsnapshot → { text } | Anthropic API (60sキャッシュ) |
| `GET /api/entry-state` | entry_state.json をそのまま | ファイル監視 (不在なら404) |
| `GET /api/agg-delta` | interval{buyUsd,sellUsd,deltaUsd,count}/cvdUsd/series[] | Binance aggTrades を約定単位で集計した精緻なテイカーデルタ/CVD |

`/api/agg-delta` は `takerlongshortRatio` 近似より精緻な、aggTrade 単位のデルタ計算 (増分aggIdで二重計上防止・
メモリは固定長リングで有界)。既存の `/api/derivs` やフロントの各パネルには影響しない追加エンドポイント。
関連env: `AGG_DELTA_POLL_SEC` (既定12s) / `AGG_DELTA_SERIES_MAX` (既定360)。

環境変数 (`.env`, `.env.example` 参照): `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `FINNHUB_API_KEY` /
`ENTRY_STATE_PATH`。未設定でも該当パネルがフォールバックするだけで他機能に影響はない。

### テスト

```bash
pip install -r requirements-dev.txt
pytest        # 計算純関数のJSXパリティ + collectors のパース/フォールバック
```

### Render デプロイ (本番)

リポジトリ直下の `render.yaml` (Blueprint) で backend を Web Service として定義済み。
Render ダッシュボードで手入力する環境変数は次の3つ (詳細な手順は `handoff/DEPLOY_STEPS.md`):

| 環境変数 | 用途 | 未設定時 |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI Overview / カレンダーAI注釈 | ルールベース文にフォールバック |
| `FINNHUB_API_KEY` | 経済カレンダーの経済指標 | Deribit満期イベントのみ |
| `FRONTEND_ORIGIN` | CORS許可元。Vercel の公開URL (例 `https://xxx.vercel.app`。カンマ区切りで複数可) | `*` (全許可・開発用) |

- `COINGLASS_API_KEY` は現方針では不要。
- ポートは Render が `PORT` を注入し、`startCommand` の `--port $PORT` が受ける。
- プランは **Starter (常時稼働)** を選ぶこと。無料プランはスリープするため 24h HL巡回に不向き。

## 3. 仕組みと限界 (正直な注意書き)

- **実データである根拠**: HLはポジションが透明で、各アドレスの `liquidationPx` を
  公開APIで取得できる。本サービスはリーダーボード上位 + 約定WSに現れた
  アドレスを巡回して実際の清算価格を集計する。
- **カバー率**: 全アドレスの列挙は不可能なので、走査済み建玉 ÷ 全体OI を
  カバー率として常に表示する。大口はOIの大半を占めるため、実用上は
  50〜80%程度で十分に機能する想定。
- **クロスマージンの揺らぎ**: 清算価格は口座全体の証拠金で動くため、
  大口(>$1M)は2分毎、中口は5分毎に再走査する。
- **リーダーボードは非公式エンドポイント**: 落ちてもWS収集だけで継続する。
- 本ツールは環境認識用であり、投資助言ではない。

## 4. Claude Code への引き渡し時の注意

- `.env` は渡さない (キーは環境変数で注入)
- 「このREADMEとliqmap_service.pyのdocstringが仕様。挙動を変えずに
  リファクタ・テスト追加・多銘柄対応をせよ」という指示が最も消費が少ない
