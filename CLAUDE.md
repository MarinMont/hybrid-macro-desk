# CLAUDE.md — Hybrid Macro Desk リポジトリ規約

このリポジトリは Hyperliquid BTC無期限先物のための環境認識ダッシュボード **+ セットアップコンソール**。
**SPEC.md と API_DESIGN.md (ダッシュボード) / SETUP_CONSOLE_SPEC.md (セットアップコンソール) が仕様の正。
迷ったらコードよりドキュメントに従い、矛盾を見つけたら実装前に報告する。**

## 変更してはいけないもの (承認なしの変更禁止)
- 計算閾値 (CHOP 61.8/38.2、パーセンタイル 25/75・30/70、Edge Factor重み 30/25/20/15/10)
- カラーシステム (SPEC §2) とセマンティック色の割り当て。
  例外: セットアップコンソール画面 (`frontend/src/SetupConsole.jsx`) は SETUP_CONSOLE_SPEC §10 の判定色
  (成立=青緑 / (c)棄却=橙 / 受け入れ警告=紫 / 違反=赤 / 沈黙=灰) を持つ。**緑を「入れ」の意味で使わない。**
- 「GO but WAIT」拒否権注記、サンプルタグ、カバー率表示、フッター免責 — これらは思想の一部
- ダッシュボードの Entry Engine パネルは表示のみ (entry_state.json を読む)。判定ロジックをパネル側へ持ち込まない
- セットアップコンソールの校正値 (x_ratio 0.05 / atr_coef 0.75 / vol_floor ×3 / median 96本 / accept 2本)。
  `backend/config/confirm_v0.3.3.json` / `confirm_v0.3.4.json` は**上書き禁止**。変更は新バージョンのファイル追加 + UI のバージョン表示更新のみ。
  既定は v0.3.4 (閾値は v0.3.3 と同一、δ は reconstruct = TradingView の Pine と同一。taker は校正を再現しない)。
  テストが通らないときは実装を疑い、閾値を疑わない
- HLレート自主上限 600weight/分 (liqmap_service.py の WeightBucket)
- **発注・約定・ポジション操作のコードを書かない** (取引権限のあるキーを持たない)

## リポジトリ構成
```
frontend/   Vite + React。参照実装 hybrid-macro-desk-btc.jsx を App として移植。
            SetupConsole.jsx はハッシュ #/setup で切り替わる別画面 (ダッシュボードのパネルには触れない)
backend/    FastAPI。liqmap_service.py (実装済み) + collectors (market/derivs/macro/ai/entry_state/aggdelta/setup_console)
            setup_console/  セットアップコンソールの純関数群 (klines/atr/delta/confirm/ledger/arithmetic/flow/calendar/replay)
            config/         校正値ファイル (バージョン付き。上書き禁止)
            data/           台帳・フロー・履歴の JSON (ローカル生成物。コミットしない)
handoff/    SPEC.md / API_DESIGN.md (ダッシュボード仕様) / SETUP_CONSOLE_SPEC.md (コンソール仕様)
```

## セットアップコンソールの決定記録 (2026-09-07 オーナー承認)
1. 置き場所: **同リポジトリに新規モジュールとして追加** (本ファイルの旧条項「判定ロジックは本リポジトリに存在しない」は廃止)
2. ENTRY_CONSOLE_SPEC v0.1 (entry_state.json を書く Entry Console) は**破棄**。セットアップコンソールが後継
3. §8 の出来高床は**実データ (記録済み Binance klines) から median を計算**して検証する
4. 台帳の永続化先は **backend の JSON ファイル** (アトミック書き込み)
5. δ の正は **reconstruct** (2026-09-08)。校正場面 S2/S3 の δ が Pine の再構成と単位まで一致し、指示書 §2.4 の既定 taker は S3 の (c)棄却を再現しないため。
   S1 の校正 δ は別ソース由来で再現不能 → 実データ検証は ATR のみ

## コーディング規約
- Python: 3.11+、型ヒント必須、async httpx、外部呼び出しは必ず timeout + try/except。
  新規依存はSPECに載っているもの以外追加しない(要相談)。
- React: 参照JSXのスタイル(カスタム色はインラインstyle、Tailwindはレイアウトのみ)を踏襲。
  localStorage/sessionStorage は使わない。
- 秘密情報は .env のみ (ANTHROPIC_API_KEY, FINNHUB_API_KEY)。.env はコミット禁止、.env.example を更新。
- コメント・UI文言は日本語。ログは英語可。
- セットアップコンソールの UI に「シグナル」「エントリー推奨」「ロング/ショート推奨」「Buy/Sell」「買い時」「売り時」を出さない。
- 時刻: 内部は UTC、コンソールの表示・記録・入力は Europe/Paris。

## 開発コマンド
```
backend:  uvicorn liqmap_service:app --port 8787 --reload
frontend: npm run dev
検証:     curl localhost:8787/api/health → /api/liq-map → /api/top-traders → /api/setup/state
テスト:   cd backend && python -m pytest -q
校正データ記録 (Mac で実行、サンドボックスからは Binance 不達):
          cd backend && python -m setup_console.record_calibration
```

## 実装タスクの優先順
Phase 1 (ダッシュボード) は完了。セットアップコンソールは SETUP_CONSOLE_SPEC §11 の順:
§2 データ層 → §5 Confirm Engine → §3 台帳 → §4 算術 → §6/§7/§10 フロー・カレンダー・UI → §9 リプレイ。
コミットは段階ごと、メッセージに「§番号」を含める。

## テスト方針
- 計算関数 (CHOP / パーセンタイル / Edge Factor / 4象限 / CVD) は純関数として切り出しpytestを書く。
  参照JSX内の実装と同一結果になることを固定入力で検証する。
- 外部APIはモックで正常系+タイムアウト+不正レスポンスの3系統。
- セットアップコンソール: SETUP_CONSOLE_SPEC §8 のフィクスチャ (S1/S2/S3/否定) は固定入力で決定的に検証する。
  実データ依存の検証 (床 median / taker Σδ ±30% / ATR ±1% / 受け入れ警告の終値) は
  `backend/tests/fixtures/calibration/` の記録済み klines を読む。未記録なら skip (理由を表示)。

## してほしくないこと
- 大規模リファクタ、ディレクトリ再編、フレームワーク変更の提案
- ダッシュボードのパネルの追加・削除・並び替え (仕様外のUX変更)
- 「改善」目的での閾値・重み・文言の調整
- 仕様の不明点を推測で埋めること (質問リストを出して止まる。特に「要記入」「要確認」の値)
