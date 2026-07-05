# API_DESIGN.md — バックエンドAPI設計とレート予算 v1.0

ベースURL: http://localhost:8787 (Phase 1)。全レスポンスはJSON。CORSは全許可(ローカル用途)。

## 1. エンドポイント契約

### 実装済み (liqmap_service.py)
| Path | 内容 | 更新頻度 |
|---|---|---|
| GET /api/health | 稼働状態・走査統計 | 即時 |
| GET /api/liq-map | 清算マップ: markPx, coveragePct, buckets[{px,longUsd,shortUsd}], topClusters | 5s再集計 |
| GET /api/top-traders | Smart Money: summary{longCount,shortCount,flatCount,longUsd,shortUsd,longNotionalPct}, traders[≤15] | 90s |

### 新規実装 (このプロジェクトで追加)
| Path | レスポンス骨子 | ソース | ポーリング |
|---|---|---|---|
| GET /api/market | { price, chg24, candles[320], fundingApr, oiUsd, vol24Usd, fundingCompare[{ex,apr}], fng{value,label} } | HL candleSnapshot / metaAndAssetCtxs / predictedFundings + Alternative.me | 20s (candlesは60s) |
| GET /api/derivs | { lsGlobalLongPct, lsTopLongPct, takerSeries[24], cvd[24], oiHist[48], oiChangePct24h } | Binance fapi 4本 | 60s |
| GET /api/macro | { events[{ts,cur,impact,name,note,f,p}], warning{active,eventName,ts} } | Finnhub + Deribit満期計算 + Claude注釈(日次) | 15min (注釈は日次) |
| GET /api/entry-state | entry_state.json の中身をそのまま (スキーマはSPEC §7) | ファイル監視 or 既存Console のHTTP | ファイル変更検知 |
| POST /api/ai-overview | body: メトリクスsnapshot → { text } | Anthropic API (claude-sonnet-4-6, max_tokens 1000) | 呼び出し時 (60sキャッシュ) |

フロント側規約: 各fetchは2.5sタイムアウト+個別try/catch。失敗パネルはサンプル値+黄タグへフォールバック(参照JSXの現挙動)。

## 2. 外部APIポーリング設計とレート予算

### Hyperliquid (上限~1200weight/分/IP、自主上限600)
| リクエスト | weight | 間隔 | 消費/分 |
|---|---|---|---|
| metaAndAssetCtxs | 20 | 20s | 60 |
| candleSnapshot | 20 | 60s | 20 |
| predictedFundings | 20 | 5min | 4 |
| clearinghouseState (清算マップ巡回) | 2 | 予算残で可変 | ~400 |
| clearinghouseState (Top50) | 2×50 | 90s | ~67 |
| **合計** | | | **~551 / 600** |

### Binance fapi (十分に緩い公開上限)
LS×2 + taker + openInterestHist を各60s → 4req/分。60sサーバーキャッシュ。

### その他
Alternative.me: 5min毎。Finnhub: 15min毎(無料枠60call/分に対し余裕)。
Anthropic: AI Overview は60sキャッシュ+手動更新時のみ、カレンダー注釈は1日1回バッチ(イベント数×1call)。

## 3. データ変換の要点

- Binance takerlongshortRatio → takerデルタ近似: delta_i = buyVol_i − sellVol_i (USD換算はquote volume使用)。CVDは累積和、24本。
- oiChangePct24h = (oiHist[-1] − oiHist[-25]) / oiHist[-25] ×100。フロントの4象限判定はこの値と価格24hを使用。
- predictedFundings のレートは各社の時間当たり。APR = rate×24×365×100。表示は HL / Binance / Bybit の3社。
- Finnhub impact→ {high:HIGH, medium:MED, low:LOW}。cur は通貨コード大文字。暗号イベントは cur:"CRYPTO"。
- 持ち越し警告: 現在時刻から24h以内に impact=HIGH が存在すれば active:true。判定はバックエンドでもフロントでも同一結果になるが、通知(Phase 3)を見据えてバックエンドで算出して返す。

## 4. 障害時のふるまい

- リーダーボード(非公式)不達 → WARNログのみ、WS収集で継続(実装済み)
- Binance不達 → /api/derivs は直近キャッシュを staleフラグ付きで返す(キャッシュも無ければ503)
- Anthropic不達 → /api/ai-overview は503。フロントはルールベース文へフォールバック(実装済み)
- entry_state.json 不在 → /api/entry-state は404。フロントはサンプル+黄タグ(実装済み)
