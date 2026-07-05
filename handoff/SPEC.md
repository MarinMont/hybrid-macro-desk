# SPEC.md — Hybrid Macro Desk (BTC) 実装仕様書 v1.0

作成: 2026-07-05 / 対象: Claude Code による本実装
参照実装: `frontend/hybrid-macro-desk-btc.jsx` (UIとフロントロジックの完成形。デザイン・計算式・文言はこのファイルが正)
参照実装: `backend/liqmap_service.py` (清算マップ+Smart Moneyサービスの完成形)

---

## 0. プロジェクト哲学 (実装判断に迷ったらここに戻る)

1. **環境認識ツールであり、売買推奨をしない。** すべてのパネルは「状況の説明」に徹する。
2. **GO but WAIT。** Entry EngineがTRIGGEREDでも最終判断は人間の拒否権。この注意書きはUI上で常時表示。
3. **見えないものを見えるフリをしない。** 清算マップはカバー率を常に表示。サンプルデータには必ずサンプルタグ。
4. **判定ロジックの一元化。** ステートマシン/レジーム判定は既存のEntry Console側が正。本アプリは表示のみ(疎結合)。

## 1. 技術スタック

- フロントエンド: Vite + React 18 (単一ページ)。`hybrid-macro-desk-btc.jsx` をほぼそのまま移植。
  Tailwindはコアクラスのみ使用中(カスタム色はインラインstyle)。フォント: JetBrains Mono + Zen Kaku Gothic New (Google Fonts)。
- バックエンド: Python 3.11+ / FastAPI / httpx / websockets。ポート8787。
- 永続化: 当面なし(インメモリ)。Phase 2でSQLiteキャッシュ検討。
- AI: Anthropic API (バックエンド経由、`ANTHROPIC_API_KEY` は .env)。
- 外部API契約: **なし(全て無料ソース)**。Coinglassは不採用 — 清算履歴はCoinglassサイトを目視。

## 2. カラーシステム (変更禁止)

| 用途 | 値 |
|---|---|
| 背景 / パネル / ボーダー | #141210 / #1D1915 / #332C22 |
| アクセント(Claudeオレンジ) | #D97757 (明: #F0906B, 暗: #8A4E3B) |
| 上昇・健全 | #3DD68C |
| 下落・過熱 | #F16A5D |
| 注意・レンジ・サンプル | #E8B93E |
| 静穏・情報 | #5CA8F5 |

オレンジは「AI要素・Edge Factor・Entry Engine・現在値マーカー」限定。方向・状態は必ずセマンティック色。

## 3. 画面構成 (上から順に)

1. ヘッダー: BTC-PERP / LIVE・DEMOバッジ / 更新時刻 / 更新ボタン
2. 価格+1h足チャート(96本) + EMA20/50 + CHOP(14) | Edge Factorリング+内訳5項目
3. AI Overview (オレンジ左ボーダー)
4. **Entry Engine**: Entry State(進行+3本柱チェック+次条件+拒否権注記) | Regime(3択+確信度+戦略配分+降格条件)
5. 環境認識: Market Mood(F&Gゲージ) | デリバティブ(Funding APR/OI/24h出来高/EMA乖離) | Bearing | Flow | Pulse
6. **Positioning**: 清算マップ(実データ/サンプル切替) | OI×価格4象限 | L/S比率 | Taker Buy/Sell | Funding比較 | CVD(24h) | OI推移(48h) | Smart Money上位50人
7. **Macro**: 持ち越し警告バナー(24h以内の高インパクト有無で赤/緑自動切替) + イベントリスト
8. フッター: データソースと免責

## 4. 計算仕様 (閾値は勝手に変えない)

| 指標 | 定義 | 閾値 |
|---|---|---|
| Choppiness(14) | 100·log10(ΣATR/(HH−LL))/log10(14)、1h足 | >61.8 レンジ / <38.2 トレンド |
| Bearing | EMA20の24本勾配(価格比%) × CHOP | \|slope\|<0.15% or CHOP≥61.8 → レンジ。CHOP<50でトレンド、それ以外チョッピー |
| Pulse | BB幅(20,±2σ)/(SMA) の履歴パーセンタイル | <25 QUIET(青) / ≤75 TRADABLE(緑) / >75 WILD(赤) |
| Flow | 24本出来高合計のローリングパーセンタイル | <30 THIN(黄) / ≤70 HEALTHY(緑) / >70 CROWDED(赤) |
| Edge Factor | 0.30·(100−CHOP) + 0.25·EMA整列(整列90/不一致35) + 0.20·ボラ環境(TRADABLE90/QUIET45/WILD40) + 0.15·参加度(HEALTHY90/THIN50/CROWDED55) + 0.10·センチメント(F&G極端40/通常75) | ≥65 明瞭(緑) / ≥45 混在(黄) / <45 低明瞭(橙) |
| OI×価格4象限 | 24h価格変化 × 24hOI変化 | ↑↑新規ロング / ↑↓ショートカバー / ↓↑新規ショート / ↓↓ロング投げ |
| CVDダイバージェンス | 価格24h方向 vs CVD符号の不一致 | 不一致時に弱気/強気候補を表示 |
| Funding APR | 時間足funding×24×365 | >15%赤(ロング過熱) / <0青 / それ以外緑 |

## 5. データソースとエンドポイント (全て無料)

### Hyperliquid (POST https://api.hyperliquid.xyz/info, WS wss://api.hyperliquid.xyz/ws)
- `candleSnapshot` (BTC, 1h, 320本) — チャートと全テクニカル
- `metaAndAssetCtxs` — markPx / prevDayPx / funding / openInterest / dayNtlVlm
- `clearinghouseState` — 清算マップ・Smart Money (liquidationPx, szi, entryPx, unrealizedPnl, leverage)
- `predictedFundings` — 取引所別Funding比較 (HL/Binance/Bybit)
- WS `trades` — アドレス収集(users欄) + CVD構築用の約定フロー
- 非公式: `https://stats-data.hyperliquid.xyz/Mainnet/leaderboard` — 落ちてもWS収集で継続する設計(実装済み)

### Binance Futures 公開API (https://fapi.binance.com, キー不要)
- `/futures/data/globalLongShortAccountRatio` (period=1h) — 個人口座L/S
- `/futures/data/topLongShortPositionRatio` (period=1h) — トップトレーダーL/S
- `/futures/data/takerlongshortRatio` (period=1h, 24本) — Takerデルタ→CVD
- `/futures/data/openInterestHist` (period=1h, 48本) — OI推移と24h変化率
- symbol=BTCUSDT。HLとBinanceの市場差は許容(注記済みの近似)

### その他
- Alternative.me `GET /fng/?limit=1` — Fear & Greed
- Finnhub `GET /calendar/economic` (無料キー、`FINNHUB_API_KEY`) — 経済指標。国=US中心、impactマップ: high→HIGH等
- 暗号イベント: Deribit満期(毎週金曜08:00 UTC+月末)を自前計算で生成
- Anthropic API — AI Overview(市況スナップショットから2文+リスク1文、日本語)とカレンダーAI注釈(1日1回バッチ)

### Coinglass — 不採用
清算履歴パネルは実装しない。必要時はCoinglassサイトを目視。将来再導入する場合の
プロキシ雛形は `liqmap_service.py` に残置(キー未設定なら503を返すだけで無害)。

## 6. バックエンドAPI (フロントが消費する契約)

詳細は API_DESIGN.md。一覧:
`/api/health` `/api/liq-map` `/api/top-traders` (実装済み) /
`/api/market` (HL集約+テクニカル計算済みメトリクス) / `/api/derivs` (Binance系: LS/taker/oiHist) /
`/api/macro` (カレンダー+AI注釈) / `/api/entry-state` (Entry Console連携) / `/api/ai-overview`

フロントは起動時に localhost:8787 を自動検出。未検出パネルはサンプル表示+黄タグ(現挙動を維持)。

## 7. Entry Console 連携 (疎結合)

既存Entry Consoleが `entry_state.json` を出力し、バックエンドは監視して `/api/entry-state` で配信。
スキーマは liqmap_service.py 冒頭コメントおよび frontend の generateDemo 内定義が正:
state(IDLE|ARMED|PULLBACK|TRIGGERED) / direction / since / pillars{structure,cvd,oi}(ok+value) /
next_condition / regime(uptrend|range|downtrend) / regime_confidence / regime_note。
**判定ロジックの移植・改変は行わない。**

## 8. フェーズ計画と受け入れ基準

**Phase 1 (今回のスコープ)**: ローカルで全パネルがライブデータ動作
- [ ] Viteプロジェクト化した参照JSXが崩れなく描画される
- [ ] バックエンド起動後、清算マップ/Smart Moneyが実データ表示(カバー率が出る)
- [ ] /api/derivs 実装でPositioning系のサンプルタグが消える
- [ ] /api/macro 実装で実カレンダー+持ち越し警告が動く
- [ ] AI OverviewがバックエンドのAnthropic API経由で生成される(キーはフロントに出ない)
- [ ] バックエンド停止時もフロントはサンプルにフォールバックして動く

**Phase 2**: 多銘柄(ETH/SOL)、entry_state.json実接続、VPS/Vercelデプロイ
**Phase 3**: ジャーナル統合、HL実データの大口ポジション一覧、通知(持ち越し警告のプッシュ)

## 9. 非機能・セキュリティ

- .env のみに秘密情報(ANTHROPIC_API_KEY, FINNHUB_API_KEY)。.gitignore必須。フロントにキーを置かない。
- HLレート: 600weight/分以内(実装済みトークンバケット)。Binance: 各1分ポーリング+60sキャッシュで十分。
- 全ての外部呼び出しはタイムアウトと例外処理を持ち、失敗はパネル単位のフォールバックに留める(画面全体を殺さない)。
- 免責文言(フッター)は削除しない。
