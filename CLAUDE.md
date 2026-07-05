# CLAUDE.md — Hybrid Macro Desk リポジトリ規約

このリポジトリは Hyperliquid BTC無期限先物のための環境認識ダッシュボード。
**SPEC.md と API_DESIGN.md が仕様の正。迷ったらコードよりドキュメントに従い、矛盾を見つけたら実装前に報告する。**

## 変更してはいけないもの (承認なしの変更禁止)
- 計算閾値 (CHOP 61.8/38.2、パーセンタイル 25/75・30/70、Edge Factor重み 30/25/20/15/10)
- カラーシステム (SPEC §2) とセマンティック色の割り当て
- 「GO but WAIT」拒否権注記、サンプルタグ、カバー率表示、フッター免責 — これらは思想の一部
- Entry Console の判定ロジック(本リポジトリには存在しない。表示のみ。移植しない)
- HLレート自主上限 600weight/分 (liqmap_service.py の WeightBucket)

## リポジトリ構成
```
frontend/   Vite + React。参照実装 hybrid-macro-desk-btc.jsx を App として移植
backend/    FastAPI。liqmap_service.py (実装済み) + collectors (新規: market/derivs/macro/ai)
handoff/    SPEC.md / API_DESIGN.md (仕様書)
```

## コーディング規約
- Python: 3.11+、型ヒント必須、async httpx、外部呼び出しは必ず timeout + try/except。
  新規依存はSPECに載っているもの以外追加しない(要相談)。
- React: 参照JSXのスタイル(カスタム色はインラインstyle、Tailwindはレイアウトのみ)を踏襲。
  localStorage/sessionStorage は使わない。
- 秘密情報は .env のみ (ANTHROPIC_API_KEY, FINNHUB_API_KEY)。.env はコミット禁止、.env.example を更新。
- コメント・UI文言は日本語。ログは英語可。

## 開発コマンド
```
backend:  uvicorn liqmap_service:app --port 8787 --reload
frontend: npm run dev
検証:     curl localhost:8787/api/health → /api/liq-map → /api/top-traders
```

## 実装タスクの優先順 (Phase 1)
1. frontend/ を Vite プロジェクト化し参照JSXを移植(見た目の変更なし)
2. backend に /api/market /api/derivs 実装(API_DESIGN §1) → フロントを接続しサンプルタグ解消
3. /api/macro (Finnhub + Deribit満期 + Claude日次注釈)
4. /api/ai-overview (Anthropic APIをバックエンド経由に移す。フロントの直接呼び出しを置換)
5. /api/entry-state (entry_state.json のファイル監視)
6. SPEC §8 の受け入れ基準を全て満たすことを確認

## テスト方針
- 計算関数 (CHOP / パーセンタイル / Edge Factor / 4象限 / CVD) は純関数として切り出しpytestを書く。
  参照JSX内の実装と同一結果になることを固定入力で検証する。
- 外部APIはモックで正常系+タイムアウト+不正レスポンスの3系統。

## してほしくないこと
- 大規模リファクタ、ディレクトリ再編、フレームワーク変更の提案
- パネルの追加・削除・並び替え (仕様外のUX変更)
- 「改善」目的での閾値・重み・文言の調整
