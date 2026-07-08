# DEPLOY.md — Vercel(フロント) + Render(バックエンド) デプロイ指示書

対象: Claude Code。目的: このリポジトリを Vercel(frontend) + Render(backend) で公開し、
デプロイされたフロントから実データが表示される状態にする。
バックエンドは Render の Web Service (Starter, 月$7固定・常時稼働) を使う。

## 前提と現状の問題
- frontend は現在 localhost:8787 を直接叩き、外部API(Hyperliquid等)にもブラウザから直接接続している。
- デプロイ環境ではブラウザからの外部API直叩きがCSP/CORSで不安定なため、**全ての外部データ取得を
  backend 経由に一本化**する。フロントは「自分のbackendのURL」1か所だけを見る構成へ変更する。

## タスク1: フロントのAPIベースURLを環境変数化
- frontend 内でハードコードされている `http://localhost:8787` を全て `import.meta.env.VITE_API_BASE` に置換。
- 未設定時のデフォルトは `http://localhost:8787`(ローカル開発を壊さない)。
- ブラウザから外部API(api.hyperliquid.xyz, api.alternative.me 等)を直接fetchしている箇所があれば、
  対応する backend エンドポイント(/api/market, /api/derivs 等。API_DESIGN.md参照)経由に変更する。
  backendに該当エンドポイントが未実装なら、API_DESIGN.md の契約に従って実装する。
- `frontend/.env.example` に `VITE_API_BASE=http://localhost:8787` を記載。

## タスク2: バックエンドのCORSと起動設定
- liqmap_service.py のCORS `allow_origins` を、環境変数 `FRONTEND_ORIGIN`(カンマ区切り可)から読む。
  未設定時は開発用に "*"。本番では Vercel のURLを設定する。
- ポートは環境変数 `PORT`(Renderが自動注入)から読む。未設定時8787。
- requirements.txt に不足があれば追記(uvicorn, fastapi, httpx, websockets, python-dotenv 等)。

## タスク3: Render用の設定 (render.yaml)
- リポジトリ直下に `render.yaml` (Blueprint) を作成し、backend を Web Service として定義する:
  - type: web / env: python / rootDir: backend / plan: starter
  - buildCommand: `pip install -r requirements.txt`
  - startCommand: `uvicorn liqmap_service:app --host 0.0.0.0 --port $PORT`
  - healthCheckPath: `/api/health`
  - 環境変数(envVars)に ANTHROPIC_API_KEY / FINNHUB_API_KEY / FRONTEND_ORIGIN を
    sync:false (ダッシュボードで手入力) として宣言する。
- `backend/runtime.txt` を作成: `python-3.12.x` (Renderが対応する形式で)。
- backend/README に「Renderデプロイ時に設定する環境変数」を明記
  (ANTHROPIC_API_KEY, FINNHUB_API_KEY, FRONTEND_ORIGIN。COINGLASS_API_KEYは不要)。

## タスク4: Vercel用の設定
- frontend/ をルートとした Vite ビルド。SPA用に `frontend/vercel.json` を作成し
  全ルートを index.html へ rewrite。
- ビルドコマンド `npm run build`、出力 `dist`。
- 環境変数 `VITE_API_BASE` に Render のバックエンドURL(https://xxx.onrender.com)を設定する旨をREADMEに明記。

## タスク5: 手順書の出力
作業完了後、`handoff/DEPLOY_STEPS.md` に「人間(非エンジニア)が行う操作」を
クリックレベルで順番に書き出す。含めるもの:
1. Renderアカウント作成→GitHub連携→New Web Service(または Blueprintでrender.yaml読込)→
   backendを指定→Starterプラン選択→環境変数入力→デプロイ→URL(https://xxx.onrender.com)取得
2. 動作確認: そのURL + /api/health をブラウザで開き ok:true が返るか
3. Vercelアカウント作成→GitHub連携→このリポジトリ選択→Root Directory を frontend に指定→
   VITE_API_BASE に Render URL を設定→デプロイ→Vercel URL 取得
4. Render の FRONTEND_ORIGIN に Vercel URL を設定して再デプロイ(CORS確定)
5. 最終確認: Vercel URLを開き、価格・清算マップ等が実データ(サンプルバナーが消える)になっているか
6. トラブル時の見方(ブラウザ開発者ツール Console/Network タブでどのエンドポイントが失敗しているか、
   Renderのログの見方、よくあるCORSエラーの直し方)
- キー未設定でも起動し、AI Overview と経済カレンダーだけサンプルになる点も明記(段階導入OK)。

## 制約(SPEC.md / CLAUDE.md を厳守)
- 計算閾値・カラー・文言・パネル構成は変更しない。デプロイに必要な配線変更のみ。
- 秘密情報はコミットしない。.env は .gitignore に含まれていることを確認。
- 変更後もローカル(localhost)で従来通り起動できることを確認する。

## 補足: Render を選んだ理由(将来の自分向けメモ)
- 月$7固定で青天井リスクがない(従量課金の不確実性を排除)。
- このバックエンドは24時間HL巡回する常時稼働型のため、Railwayの scale-to-zero の利点が活きない。
- 無料プランはスリープするため使わない。必ず Starter(有料・常時稼働)を選ぶこと。
