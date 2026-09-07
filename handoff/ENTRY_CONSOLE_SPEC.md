# ENTRY_CONSOLE_SPEC.md — Entry Console 仕様書 (下書き v0.1)

> **状態: 下書き。** 「■未決」の付いた項目は未確定で、実装前にオーナー(hibiki)との議論で確定させる。
> 確定済みの箇所は Hybrid Macro Desk 側の仕様 (SPEC.md §7 / API_DESIGN.md) と実装から導かれた**動かせない契約**。
>
> 新しいチャットで開発を始めるときは、このファイルと SPEC.md §7 を最初に読むこと。

## 1. 目的と位置づけ

Entry Console は、Hyperliquid BTC無期限先物のエントリー判定を行う**独立したステートマシン**。
判定結果を `entry_state.json` に書き出し、Hybrid Macro Desk (ダッシュボード) がそれを表示する。

```
[Entry Console (本仕様)] --書き出し--> entry_state.json --読み取り--> [backend /api/entry-state] --> [フロント表示]
```

- ダッシュボードとの結合は **`entry_state.json` ただ1点**(疎結合)。互いのコードには一切触れない。
- ダッシュボード側 (hybrid-macro-desk リポジトリ) の CLAUDE.md により、**判定ロジックをダッシュボード側へ移植することは禁止**。逆方向 (ダッシュボードの表示コードを Console へ移植) も不要。

### やらないこと (思想として固定)
- **発注・自動売買はしない。** SPEC の第一原則「GO but WAIT」= TRIGGERED でも最終判断は人間の拒否権。
  Console は状態を示すだけで、注文APIには接続しない。秘密鍵・APIキー(取引権限)を持たない。
- ダッシュボードのパネル構成・閾値・文言には手を出さない。

## 2. 出力契約 — entry_state.json (確定・変更不可)

スキーマの正は SPEC.md §7 と、ダッシュボード実装 (`backend/collectors/entry_state.py`,
`frontend/src/App.jsx` の `adaptEntryState` / `normalizeSetup`)。見本: `backend/entry_state.sample.json`。

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `state` | `"IDLE" \| "ARMED" \| "PULLBACK" \| "TRIGGERED"` | ✔ | ステートマシンの現在状態 |
| `direction` | `"LONG" \| "SHORT" \| null` | ✔ | 判定方向。未確定なら null |
| `since` | ISO8601 UTC 文字列 | ✔ | 現在の state に入った時刻 (フロントが経過分数を計算) |
| `pillars.structure` | `{ok: bool, value: string}` | ✔ | 柱1: 価格構造。value は表示文 (日本語自由文) |
| `pillars.cvd` | `{ok: bool, value: string}` | ✔ | 柱2: CVD |
| `pillars.oi` | `{ok: bool, value: string}` | ✔ | 柱3: OI |
| `next_condition` | string | ✔ | 次の遷移に必要な条件の一文 |
| `regime` | `"uptrend" \| "range" \| "downtrend"` | ✔ | レジーム3択 |
| `regime_confidence` | number 0..1 | ✔ | レジーム確信度 |
| `regime_note` | string | ✔ | 降格条件などの注記 |
| `setup` | object / 省略可 | — | 具体的な指値プラン (下記)。無ければダッシュボードは機械式(EMA20×ATR)にフォールバック |
| `setup.side` | `"LONG" \| "SHORT"` / 省略可 | — | 省略時はダッシュボードが stop と entry の位置関係から推定 |
| `setup.entry` / `setup.stop` | number | setup 内で✔ | 建値 / 損切り。**数値でないと setup 全体が無効扱い** |
| `setup.targets` | number[] (1件以上) | setup 内で✔ | 利確目標 (T1, T2, …) |
| `setup.note` | string / 省略可 | — | プランの根拠メモ |

### 書き出しの規約
- **アトミックに書く**: 一時ファイルに書いて `rename` で置き換える (ダッシュボードはリクエスト毎に
  ファイルを読むため、書きかけJSONを読むと 503 になる。rename なら半端な状態が見えない)。
- 文字コード UTF-8。`pillars` が欠けるとダッシュボードはサンプル表示に落ちる (= 必須フィールドは常に出す)。
- 置き場所: ダッシュボード backend の起動ディレクトリ直下 `entry_state.json`
  (backend は環境変数 `ENTRY_STATE_PATH` で参照先を変更可能)。
- 更新頻度: ■未決 (§5)。ダッシュボード側はポーリング表示なので、書き過ぎても壊れはしない。

## 3. ステートマシン (骨組みは確定・遷移条件は■未決)

状態遷移の骨組み (SPEC §7 の語彙より):

```
IDLE ──(環境が整う)──> ARMED ──(押し目/戻り形成)──> PULLBACK ──(トリガー成立)──> TRIGGERED
  ^                     |                            |                            |
  └──────(条件喪失で降格・リセット)───────────────────────────────────────────────┘
```

- **IDLE**: 3本柱が揃っていない。方向なし。
- **ARMED**: 環境認識が方向を支持 (柱の充足)。エントリー待機。
- **PULLBACK**: エントリーに適した押し目/戻りが形成中。
- **TRIGGERED**: エントリー条件成立。**ただし発注はしない** (GO but WAIT — 人間が最終判断)。

### ■未決 — オーナーと決める判定ルール (新チャットの議題そのもの)
1. **柱1「価格構造」の定義**: 何をもって ok とするか (例: H4 で HH/HL 継続かつ EMA20 上、など)。時間足は?
2. **柱2「CVD」の定義**: 集計窓 (24h?) と ok 条件 (ネット買い越し? 傾き?)。
3. **柱3「OI」の定義**: 増減率の窓と閾値。「新規流入」をどう判定するか。
4. **ARMED の条件**: 3本柱のうち何本必要か (3/3? 2/3+方向一致?)。
5. **PULLBACK の条件**: 何への押し目か (EMA20? 直近ブレイク水準?)、深すぎる押しの扱い。
6. **TRIGGERED の条件**: 反発確認の定義 (足の確定? 出来高?)。
7. **降格・リセットの条件**: 各状態から IDLE に戻る条件、TRIGGERED の有効期限。
8. **regime 判定**: 3択の判定式と confidence の算出法。ダッシュボードにも独自の簡易レジーム表示
   (CHOP 61.8/38.2 等) があるが、**Console 側が正** (SPEC 原則4)。一致させる必要はないが乖離の扱いを決める。
9. **setup の生成**: 自動算出するか、人間が対話で入力するか (当面は手動入力でも可)。

## 4. データ源 (■未決 — 推奨案あり)

**推奨: ダッシュボード backend の既存APIを読む** (`/api/market`, `/api/derivs` 等)。
- 理由: Hyperliquid のレート自主上限 600weight/分は**IP単位**。同じマシンで Console が直接
  HL を叩くと予算をダッシュボードと食い合う。既存APIなら追加消費ゼロで、CVD/OI/価格が揃っている。
- 代替案: HL / Binance を直接叩く (別マシンで動かす場合)。その場合は Console 側にも
  レート管理とタイムアウト+リトライを実装する。

## 5. 実行形態 (■未決)

| 案 | 内容 | 向き |
|---|---|---|
| a. 常駐デーモン | N秒毎に判定して書き出す (例: 60s) | 完成形。放置で動く |
| b. 手動実行CLI | 実行した時だけ判定・書き出し | 最初の一歩に良い |
| c. 対話ハイブリッド | 判定は自動、setup だけ人間が対話で入力 | §3-9 と連動 |

## 6. 置き場所 (■未決)

- **推奨: 別リポジトリ** (例: `entry-console`)。hybrid-macro-desk の CLAUDE.md
  「判定ロジックは本リポジトリには存在しない」と整合し、境界が壊れない。
- 同リポジトリ内の別ディレクトリに置く案は CLAUDE.md の改訂が必要になるため非推奨。
- 実行環境: 当面は**ローカル(Mac)のみ**を想定。デプロイ版 (Render) の backend は
  ファイルシステムが揮発するため、Console をデプロイ版と繋ぐには
  `POST /api/entry-state` (プッシュ受け口) をダッシュボード側に追加する必要がある — これは
  ダッシュボード側の将来課題として別途扱う (Phase 2「entry_state.json実接続」の一部)。

## 7. 受け入れ基準 (下書き)

- [ ] 出力した `entry_state.json` で、ダッシュボードの Entry Engine が緑「実データ · Entry Console 連携」になる
- [ ] `setup` を含めた出力で、セットアップ・カードが指定値表示になる
- [ ] 判定ルール (§3 で確定させたもの) が固定入力に対して決定的で、pytest で検証できる
- [ ] 書き出しがアトミックで、ダッシュボード側で 503 が観測されない
- [ ] 発注・取引権限を一切持たないことをコードレビューで確認できる
- [ ] HLレート予算をダッシュボードと食い合わない (§4 の方式で確認)

## 8. 実装者への引き継ぎメモ

- スキーマ実例: `backend/entry_state.sample.json` (これが最も手っ取り早い正)
- ダッシュボード側の読み取り実装: `backend/collectors/entry_state.py` (ファイル不在=404 / 壊れたJSON=503)
- フロントの受け側: `frontend/src/App.jsx` の `adaptEntryState` (pillars 必須) と `normalizeSetup` (setup 検証規則)
- 手動運用の先例: このファイル作成時点では Console 未実装のため、`entry_state.json` を人間が
  直接編集する運用を行っている。Console はこれを置き換えるもの。
