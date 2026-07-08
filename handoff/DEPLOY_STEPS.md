# DEPLOY_STEPS.md — 公開するためのクリック手順（非エンジニア向け）

このアプリをインターネットに公開する手順です。**上から順にそのまま進めれば完了**します。
2つのサービスを使います。

- **Render**（レンダー）… バックエンド（データ取得サーバー）を動かす。月 **$7**（常時稼働）。
- **Vercel**（バーセル）… フロントエンド（画面）を動かす。**無料**。

必要なもの: GitHub アカウント（このコードが置いてある場所）、クレジットカード（Render の支払い用）。
所要時間: 20〜30分ほど。

> 💡 **順番が大事です。** 先に Render（バックエンド）を作って URL を取得 → その URL を Vercel に設定 →
> 最後に Vercel の URL を Render に教える、という流れになります。

---

## 事前確認（1分）
- コードが GitHub の **`MarinMont/hybrid-macro-desk`** にあり、ブランチ
  **`claude/hyper-liquid-trading-app-4zgadr`** に最新が入っていること。
- （もしこのブランチが本流 `main` にマージ済みなら、以下の「ブランチ選択」では `main` を選んでOKです。）

---

## STEP 1. Render でバックエンドを公開する

### 1-1. アカウント作成 & GitHub 連携
1. https://render.com を開き **Get Started**（または **Sign in**）→ **GitHub** で登録/ログイン。
2. 途中で GitHub の認可を求められたら **Authorize Render** を押す。
3. 「リポジトリへのアクセス許可」で **`hybrid-macro-desk`** を選べるようにする
   （All repositories でも、Only select repositories で当該リポジトリを選ぶのでもOK）。

### 1-2. Blueprint（render.yaml）から作成する ★おすすめ
このリポジトリには設定ファイル `render.yaml` が入っているので、それを読み込むだけで作れます。

1. Render 左上の **New +** → **Blueprint** をクリック。
2. リポジトリ一覧から **`hybrid-macro-desk`** を選ぶ → **Connect**。
3. ブランチを選ぶ欄が出たら **`claude/hyper-liquid-trading-app-4zgadr`**（または `main`）を選ぶ。
4. Render が `render.yaml` を読み、**`hybrid-macro-desk-api`** という Web Service を提案する。**Apply**（または Create）を押す。
5. 環境変数の入力を求められます（`render.yaml` で「後で手入力」に設定済みのため）。ここでは:
   - `ANTHROPIC_API_KEY` … **空のままでOK**（後から追加できます。未設定だと AI 概況が定型文になるだけ）
   - `FINNHUB_API_KEY` … **空のままでOK**（未設定だと経済カレンダーが満期予定だけになる）
   - `FRONTEND_ORIGIN` … **今は空のままでOK**（STEP 4 で Vercel の URL を入れます）
   → 入力できるものだけ入れて先へ進みます。

> 🅰️ **Blueprint が見当たらない/うまくいかない場合の代替（手動作成）**
> 1. **New +** → **Web Service** → リポジトリ `hybrid-macro-desk` を選ぶ。
> 2. 設定を手入力:
>    - **Root Directory**: `backend`
>    - **Runtime**: Python
>    - **Build Command**: `pip install -r requirements.txt`
>    - **Start Command**: `uvicorn liqmap_service:app --host 0.0.0.0 --port $PORT`
>    - **Health Check Path**: `/api/health`
>    - **Instance Type / Plan**: **Starter（$7/月）** を選ぶ ← ここ重要
> 3. 下の方の **Environment Variables** で上記3つのキーを（分かるものだけ）追加。
> 4. **Create Web Service**。

### 1-3. プランの確認（重要）
- **必ず Starter（$7/月・常時稼働）** になっていることを確認してください。
- **Free プランは使わない**でください（一定時間でスリープし、24時間データ収集が止まります）。
- 支払い方法（クレジットカード）の登録を求められたら登録します。

### 1-4. デプロイ完了を待つ
- 画面にログが流れ、数分で **Live**（緑）になります。
- 上部に **サービスの URL**（例: `https://hybrid-macro-desk-api.onrender.com`）が表示されます。
  **この URL をコピーしてメモ**してください。以降「**Render URL**」と呼びます。

---

## STEP 2. バックエンドが動いているか確認する（30秒）
ブラウザのアドレスバーに、**Render URL の後ろに `/api/health` を付けて**開きます:

```
https://hybrid-macro-desk-api.onrender.com/api/health
```

次のような表示が出れば成功です:
```json
{"ok":true,"uptimeSec":12,"addresses":0,"positions":0,"scans":0,"markPx":null}
```
- `"ok":true` が出ればOK。`markPx` などは起動直後は空でも問題ありません（1〜2分でデータが入ります）。
- 出ない場合は STEP 6 の「困ったとき」を参照。

---

## STEP 3. Vercel でフロントエンド（画面）を公開する

### 3-1. アカウント作成 & GitHub 連携
1. https://vercel.com を開き **Sign Up**（または **Log In**）→ **Continue with GitHub**。
2. 認可を求められたら許可する。

### 3-2. プロジェクト作成
1. Vercel の **Add New…** → **Project**。
2. リポジトリ一覧から **`hybrid-macro-desk`** を **Import**。
   （見つからなければ **Adjust GitHub App Permissions** でこのリポジトリへのアクセスを許可）
3. **設定画面で以下を必ず指定**します:
   - **Root Directory**: **`frontend`** ← 「Edit」を押して `frontend` を選ぶ（**最重要**）。
   - Framework Preset: **Vite**（自動検出されるはず）。
   - Build Command / Output Directory はそのまま（`npm run build` / `dist`）。
4. **Environment Variables** を開き、次の1件を追加:
   - **Name**: `VITE_API_BASE`
   - **Value**: STEP 1 でメモした **Render URL**（例 `https://hybrid-macro-desk-api.onrender.com`、末尾スラッシュ無し）
   - Add を押す。
5. （ブランチ: 既定では本番ブランチがデプロイされます。`claude/...` ブランチで運用する場合は
   デプロイ後に Settings → Git の Production Branch を合わせてください。まずは Import 時に出るブランチのままでOK。）
6. **Deploy** を押す。1〜2分で完了し、**Vercel の URL**（例 `https://hybrid-macro-desk.vercel.app`）が表示されます。
   **この URL をコピーしてメモ**してください。以降「**Vercel URL**」と呼びます。

---

## STEP 4. Render に「フロントの URL」を教える（CORS 確定）
ブラウザのセキュリティ上、バックエンドは「どの画面からのアクセスを許すか」を知る必要があります。

1. **Render** のダッシュボードに戻り、`hybrid-macro-desk-api` サービスを開く。
2. 左メニュー **Environment**（環境変数）→ **Add Environment Variable**（または既存の `FRONTEND_ORIGIN` を編集）。
   - **Key**: `FRONTEND_ORIGIN`
   - **Value**: STEP 3 でメモした **Vercel URL**（例 `https://hybrid-macro-desk.vercel.app`、末尾スラッシュ無し）
3. **Save Changes**。Render が自動で再デプロイします（数分）。再び **Live** になれば完了。

> 補足: 独自ドメインやプレビューURLも許可したい場合は、カンマ区切りで複数指定できます。
> 例: `https://hybrid-macro-desk.vercel.app,https://www.example.com`

---

## STEP 5. 最終確認 🎉
1. ブラウザで **Vercel URL** を開く。
2. しばらく（〜30秒）待つと、価格・チャート・清算マップなどが表示されます。
3. 次を確認:
   - 画面上部に**青いお知らせバナー（「デモデータを表示…」）が出ていない**こと
     → 出ていなければ、バックエンド経由の**実データ**で動いています。
   - 各パネルのタグが「サンプル」ではなく「実データ」になっていること
     （Positioning / Macro 見出しの右のタグ、清算マップの「カバー率 xx%」など）。
   - ※ 清算マップのカバー率は、バックエンド起動から **1〜2分**かけて立ち上がります。少し待ってから再読み込みを。

これで公開完了です。以後、GitHub のそのブランチに変更を push すると自動で再デプロイされます。

---

## 任意: AI 概況 と 経済カレンダー を後から有効化（段階導入OK）
キー無しでも公開できます（AI 概況は定型文、経済カレンダーは満期予定のみ）。使いたくなったら:

1. **Render** → サービス → **Environment** で追加:
   - `ANTHROPIC_API_KEY` … Anthropic のキー（https://console.anthropic.com で発行・**従量課金**）
   - `FINNHUB_API_KEY` … Finnhub のキー（https://finnhub.io で無料発行）
2. **Save Changes** → 自動再デプロイ。以後 AI 概況が生成文に、カレンダーに経済指標が並びます。
   （フロント側の再設定は不要です。キーはバックエンドにだけ置きます。）

---

## STEP 6. 困ったとき（トラブルシューティング）

### A. 画面が「デモデータ」バナーのまま／サンプルが消えない
最もよくあるのは **URL 設定ミス** か **CORS 未設定**です。順に確認:

1. **開発者ツールで原因を見る**（Chrome の場合）:
   - 画面上で右クリック → **検証**（または `⌘ + Option + I`）→ 上部の **Console** / **Network** タブ。
   - **Console** に赤いエラーが出ていないか見る。
     - `CORS` や `Access-Control-Allow-Origin` という語が出ていたら → **STEP 4 の `FRONTEND_ORIGIN` 設定漏れ/URL 違い**。Vercel URL を正確に（末尾スラッシュ無しで）入れ直して Render を再デプロイ。
     - `Failed to fetch` / `net::ERR` が `onrender.com` 宛てに出ていたら → **`VITE_API_BASE` の値ミス**、または Render がまだ起動中/停止。
   - **Network** タブで再読み込みし、`api/market` や `api/health` の行の **Status** を見る:
     - `200` … 正常。`(failed)` や `CORS error` … 上記の設定を見直す。
     - `503` … バックエンドが起動直後。1〜2分待って再読み込み。

2. **VITE_API_BASE を直したら再デプロイが必要**:
   Vercel の環境変数を変えたら、**Deployments → 最新 → 右の「…」→ Redeploy** で反映されます
   （フロントの環境変数はビルド時に埋め込まれるため）。

### B. `/api/health` が開けない・Render が Live にならない
- Render の該当サービス → **Logs** タブでエラーを確認。
- `pip install` で失敗 → `backend/requirements.txt` が読めているか（Root Directory が `backend` か）確認。
- ずっと起動中のまま → プランが **Free** だとスリープします。**Starter** か確認。

### C. Render のログの見方
- サービス画面の **Logs** タブ。起動時に `liqmap service started ...` と出ていれば起動成功。
- `WARNING ... 403` などデータ取得の警告が一部出ても、パネルごとにフォールバックするので**画面全体は落ちません**。

### D. お金まわり
- 課金されるのは **Render の Starter（$7/月）** のみ。Vercel は無料枠で足ります。
- Anthropic のキーを入れた場合のみ、AI 概況の生成に**ごく少額の従量課金**が発生します
  （コンソールで利用上限を設定可）。

---

困ったら、**どの STEP で・どんなエラー文が出たか**（Console/Network の赤い文字や Render の Logs）を
そのまま伝えてください。ピンポイントで直し方をご案内します。
