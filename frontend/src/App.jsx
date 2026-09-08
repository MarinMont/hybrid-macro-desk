import { useState, useEffect, useCallback, useMemo } from "react";

// ============================================================
// Hybrid Macro Desk — BTC (Prototype)
// Data: Hyperliquid public API + Alternative.me Fear & Greed
// AI overview: Anthropic API (claude-sonnet-4-6)
// Theme: Claude orange × warm dark / semantic up-down colors
// ============================================================

export const C = {
  bg: "#141210",
  panel: "#1D1915",
  panelSoft: "#242019",
  border: "#332C22",
  borderSoft: "#2A241C",
  orange: "#D97757",
  orangeBright: "#F0906B",
  orangeDim: "#8A4E3B",
  text: "#EDE6DB",
  muted: "#9C9082",
  faint: "#6B6156",
  green: "#3DD68C",
  greenDim: "#1E5C40",
  red: "#F16A5D",
  redDim: "#6B2E28",
  yellow: "#E8B93E",
  yellowDim: "#6B5518",
  blue: "#5CA8F5",
  blueDim: "#27456B",
};

export const FONT_MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
export const FONT_UI = "'Zen Kaku Gothic New', 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif";

// ---------- backend (ローカル or デプロイ先) ----------
// 全ての外部データ取得は自分のbackend 1か所に一本化する (デプロイ時のCSP/CORS対策)。
// デプロイでは VITE_API_BASE に Render のURL(https://xxx.onrender.com)を設定する。
// 未設定時はローカル開発用に localhost:8787。未検出パネルはサンプル表示+黄タグ (SPEC §6)。
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8787";

export const jget = async (path, ms = 2500) => {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  try {
    const r = await fetch(`${API_BASE}${path}`, { signal: ac.signal });
    clearTimeout(t);
    return r.ok ? await r.json() : null;
  } catch {
    clearTimeout(t);
    return null;
  }
};

export const jpost = async (path, body, ms = 6000) => {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  try {
    const r = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: ac.signal,
      body: JSON.stringify(body),
    });
    clearTimeout(t);
    return r.ok ? await r.json() : null;
  } catch {
    clearTimeout(t);
    return null;
  }
};

// バックエンドの /api/entry-state (SPEC §7 スキーマ) → フロントの entry 表示形へ変換。
// **判定ロジックには一切触れない。表示用の整形のみ。**
const adaptEntryState = (j) => {
  if (!j || !j.pillars) return null;
  const sinceMs = j.since ? Date.parse(j.since) : NaN;
  const sinceMin = isNaN(sinceMs) ? 0 : Math.max(0, Math.floor((Date.now() - sinceMs) / 60000));
  const p = j.pillars;
  return {
    state: j.state,
    direction: j.direction ?? null,
    sinceMin,
    pillars: [
      // 柱の名前は entry_state.json に name があればそれを使う (セットアップコンソール連携。既定は従来の3本柱)
      { name: p.structure?.name ?? "価格構造", ok: !!p.structure?.ok, value: p.structure?.value ?? "" },
      { name: p.cvd?.name ?? "CVD", ok: !!p.cvd?.ok, value: p.cvd?.value ?? "" },
      { name: p.oi?.name ?? "OI", ok: !!p.oi?.ok, value: p.oi?.value ?? "" },
    ],
    nextCondition: j.next_condition ?? "",
    regime: j.regime,
    regimeConf: j.regime_confidence ?? 0,
    regimeNote: j.regime_note ?? "",
    // 任意: entry_state.json に setup(建値/損切り/利確) があればそのまま渡す。
    // 無ければ null → セットアップ・カードは従来の機械式(EMA20×ATR)にフォールバック。
    setup: normalizeSetup(j.setup),
  };
};

// entry_state.json の setup を検証・正規化。entry/stop/targets が数値で揃っていなければ null。
const normalizeSetup = (s) => {
  if (!s) return null;
  const entry = Number(s.entry);
  const stop = Number(s.stop);
  const targets = Array.isArray(s.targets) ? s.targets.map(Number).filter((x) => !isNaN(x)) : [];
  if (isNaN(entry) || isNaN(stop) || !targets.length) return null;
  // side は明示があれば優先、無ければ損切りが建値の下=LONG / 上=SHORT で推定
  const side = s.side === "LONG" || s.side === "SHORT" ? s.side : stop < entry ? "LONG" : "SHORT";
  return { side, entry, stop, targets, note: s.note ?? null };
};

// ---------- math helpers ----------
const ema = (vals, p) => {
  const k = 2 / (p + 1);
  let e = vals[0];
  const out = [e];
  for (let i = 1; i < vals.length; i++) {
    e = vals[i] * k + e * (1 - k);
    out.push(e);
  }
  return out;
};

const percentileRank = (arr, val) => {
  if (!arr.length) return 50;
  let c = 0;
  for (const x of arr) if (x <= val) c++;
  return (100 * c) / arr.length;
};

const choppiness = (candles, n = 14) => {
  const seg = candles.slice(-n - 1);
  if (seg.length < n + 1) return 50;
  let atrSum = 0;
  for (let i = 1; i < seg.length; i++) {
    const h = seg[i].h, l = seg[i].l, pc = seg[i - 1].c;
    atrSum += Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
  }
  const body = seg.slice(1);
  const hh = Math.max(...body.map((c) => c.h));
  const ll = Math.min(...body.map((c) => c.l));
  if (hh - ll <= 0) return 100;
  return (100 * Math.log10(atrSum / (hh - ll))) / Math.log10(n);
};

const bbWidthSeries = (closes, p = 20) => {
  const out = [];
  for (let i = p - 1; i < closes.length; i++) {
    const win = closes.slice(i - p + 1, i + 1);
    const m = win.reduce((a, b) => a + b, 0) / p;
    const sd = Math.sqrt(win.reduce((a, b) => a + (b - m) ** 2, 0) / p);
    out.push((4 * sd) / m);
  }
  return out;
};

const fmtUsd = (n) => {
  if (n == null || isNaN(n)) return "—";
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${Math.round(n).toLocaleString()}`;
};

const fmtPx = (n) =>
  n == null || isNaN(n) ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

// ---------- demo data (artifact sandbox fallback) ----------
// The artifact sandbox blocks external APIs (CSP). When live fetch fails,
// we fall back to a realistic, seeded snapshot (~$61.8K, recovery-from-lows regime).
const mulberry32 = (seed) => () => {
  seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

const generateDemo = () => {
  const rnd = mulberry32(20260705);
  const candles = [];
  let px = 59200;
  const now = Date.now();
  const N = 320;
  for (let i = 0; i < N; i++) {
    // regime: choppy grind up with pullbacks (recovery from lows)
    const phase = i / N;
    const drift = phase < 0.35 ? -0.0002 : phase < 0.55 ? 0.0009 : 0.0004;
    const vol = phase < 0.55 ? 0.0035 : 0.0028;
    const shock = rnd() < 0.04 ? (rnd() - 0.5) * 0.012 : 0;
    const o = px;
    const move = (rnd() - 0.5) * 2 * vol + drift + shock;
    const c = o * (1 + move);
    const h = Math.max(o, c) * (1 + rnd() * vol * 0.9);
    const l = Math.min(o, c) * (1 - rnd() * vol * 0.9);
    const baseV = 220 + rnd() * 180;
    const v = baseV * (1 + Math.abs(move) * 220) * (phase > 0.5 ? 1.25 : 1);
    candles.push({ t: now - (N - i) * 3600 * 1000, o, h, l, c, v });
    px = c;
  }
  const last = candles[candles.length - 1].c;
  // --- positioning (Coinglass互換のデモ) ---
  // 本実装: Coinglass API v4 (要バックエンド+APIキー)
  //   清算ヒートマップ: /api/futures/liquidation/heatmap/model2
  //   L/S比率: /api/futures/global-long-short-account-ratio/history
  //   テイカー比率: /api/futures/taker-buy-sell-volume/history
  // 無料代替: Binance fapi (globalLongShortAccountRatio, takerlongshortRatio,
  //   openInterestHist), Hyperliquid predictedFundings
  const liqLevels = [];
  for (let i = 1; i <= 8; i++) {
    liqLevels.push({
      price: last * (1 + i * 0.009),
      size: (rnd() * 0.6 + 0.35) * (i === 2 || i === 6 ? 2.4 : 1) * 95e6,
      side: "short",
    });
    liqLevels.push({
      price: last * (1 - i * 0.009),
      size: (rnd() * 0.6 + 0.35) * (i === 3 ? 2.8 : 1) * 95e6,
      side: "long",
    });
  }
  const taker = Array.from({ length: 24 }, (_, i) => {
    const bias = i > 14 ? 0.18 : -0.08;
    return (rnd() - 0.5 + bias) * 42e6;
  });
  // OI推移 48h (現在OIに対する倍率): 高値から縮小 → 底打ちのデモ
  const oiHistMult = Array.from({ length: 48 }, (_, i) => {
    const t = i / 47;
    return 1.0 + (1 - t) * 0.052 - 0.01 * Math.sin(t * Math.PI * 2.2) + (rnd() - 0.5) * 0.006;
  });
  const deriv = {
    liqLevels,
    oiHistMult,
    lsGlobalLongPct: 68.2, // 個人口座: 恐怖圏で逆張りロングに偏りがち
    lsTopLongPct: 47.6, // トップトレーダー: 中立〜やや売り
    takerSeries: taker,
    oiChangePct24h: -3.4, // 価格↑ × OI↓ = ショートカバー主導のデモ設定
    fundingCompare: [
      { ex: "Hyperliquid", apr: 10.9 },
      { ex: "Binance", apr: 8.2 },
      { ex: "Bybit", apr: 9.6 },
    ],
  };
  // --- Entry Console 連携 (疎結合インターフェース) ---
  // 既存のEntry Console(ステートマシン)が entry_state.json を出力し、
  // バックエンドが /api/entry-state で配信、本ダッシュボードは表示のみ。
  // スキーマ:
  // {
  //   "state": "ARMED",              // IDLE | ARMED | PULLBACK | TRIGGERED
  //   "direction": "LONG",           // LONG | SHORT | null
  //   "since": "2026-07-05T04:10:00Z",
  //   "pillars": {
  //     "structure": { "ok": true,  "value": "HH/HL継続 · EMA20上" },
  //     "cvd":       { "ok": true,  "value": "24hネット買い越し" },
  //     "oi":        { "ok": false, "value": "24h -3.4% 新規流入なし" }
  //   },
  //   "next_condition": "EMA20への押し目形成 → PULLBACK",
  //   "regime": "uptrend",           // uptrend | range | downtrend
  //   "regime_confidence": 0.62,
  //   "regime_note": "H4終値がEMA50割れでレンジへ降格"
  // }
  // --- マクロ経済カレンダー (デモ) ---
  // 本実装: Finnhub /calendar/economic (無料枠あり) or Trading Economics /FMP。
  // 暗号通貨イベント: Deribitオプション満期(計算可能) + FOMC等の固定日程 + Coinglassトークンアンロック。
  // AI注釈: 各イベントをClaude APIに渡して1文の影響分析を生成 (デイリーバッチ)。
  const nowTs = Date.now();
  const macro = [
    { inH: 12, cur: "USD", impact: "HIGH", name: "ISM非製造業景況指数", note: "サービス業の減速が確認されれば利下げ観測が再燃 — リスク資産に追い風だが初動は荒れやすい", f: "52.0", p: "52.9" },
    { inH: 14.5, cur: "USD", impact: "MED", name: "FRB理事講演 (金融政策見通し)", note: "QT再開への言及があれば米金利上昇 → BTCに逆風", f: null, p: null },
    { inH: 26, cur: "CRYPTO", impact: "MED", name: "BTC/ETH 週次オプション満期 (Deribit)", note: "満期前はMax Pain付近への価格吸着に注意。満期通過後に本来の方向へ動きやすい", f: null, p: null },
    { inH: 38, cur: "USD", impact: "LOW", name: "新規失業保険申請件数", note: "単発では影響軽微。ただしNFP前の地ならしとして意識される", f: "225K", p: "218K" },
    { inH: 60, cur: "USD", impact: "HIGH", name: "雇用統計 (NFP)", note: "週最大のイベント。労働市場の強弱でFedの利下げパスと米金利が大きく動く", f: "+140K", p: "+98K" },
  ].map((e) => ({ ...e, ts: nowTs + e.inH * 3600e3 }));

  // --- Smart Money: リーダーボード上位50人 (30日PnL順) のデモ ---
  // 本実装: バックエンド /api/top-traders (90秒毎更新)
  const topTraders = {
    summary: { longCount: 19, shortCount: 19, flatCount: 12, longUsd: 212e6, shortUsd: 247e6, longNotionalPct: 46.2 },
    traders: [
      { rank: 3, addr: "0x7f3a…c2e1", monthPnl: 12400000, side: "short", posUsd: 48200000, entryPx: 64850, liqPx: 74120, upnl: 2270000, lev: 8, levType: "cross" },
      { rank: 1, addr: "0xa91b…44f7", monthPnl: 18100000, side: "long", posUsd: 36900000, entryPx: 57940, liqPx: 41300, upnl: 2300000, lev: 5, levType: "cross" },
      { rank: 12, addr: "0x3c88…9ab0", monthPnl: 4300000, side: "short", posUsd: 22400000, entryPx: 62410, liqPx: 68930, upnl: 220000, lev: 12, levType: "isolated" },
      { rank: 7, addr: "0xde45…1c6d", monthPnl: 6900000, side: "long", posUsd: 19800000, entryPx: 60120, liqPx: 52480, upnl: 540000, lev: 6, levType: "cross" },
      { rank: 28, addr: "0x11f2…e803", monthPnl: 1800000, side: "short", posUsd: 14100000, entryPx: 61050, liqPx: 66240, upnl: -170000, lev: 15, levType: "isolated" },
      { rank: 5, addr: "0xb670…7d19", monthPnl: 8200000, side: "long", posUsd: 11600000, entryPx: 62480, liqPx: 55900, upnl: -130000, lev: 10, levType: "cross" },
      { rank: 33, addr: "0x9e0c…52aa", monthPnl: 1400000, side: "short", posUsd: 9300000, entryPx: 59720, liqPx: 65880, upnl: -320000, lev: 20, levType: "isolated" },
      { rank: 19, addr: "0x62d1…b3f4", monthPnl: 2700000, side: "long", posUsd: 8800000, entryPx: 61940, liqPx: 48150, upnl: -20000, lev: 4, levType: "cross" },
      { rank: 41, addr: "0xf7a8…08ce", monthPnl: 900000, side: "short", posUsd: 6200000, entryPx: 63180, liqPx: 69510, upnl: 130000, lev: 18, levType: "isolated" },
      { rank: 24, addr: "0x40b5…d97e", monthPnl: 2100000, side: "long", posUsd: 5400000, entryPx: 60860, liqPx: 54020, upnl: 80000, lev: 7, levType: "cross" },
    ],
  };

  const entry = {
    state: "ARMED",
    direction: "LONG",
    sinceMin: 142,
    pillars: [
      { name: "価格構造", ok: true, value: `HH/HL 継続 · EMA20上 (+1.2%)` },
      { name: "CVD", ok: true, value: "24h ネット買い越し · 価格と一致" },
      { name: "OI", ok: false, value: "24h −3.4% · 新規流入なし (ショートカバー)" },
    ],
    nextCondition: `EMA20 (≈${Math.round(last * 0.988).toLocaleString()}) への押し目形成 → PULLBACK`,
    regime: "uptrend",
    regimeConf: 0.62,
    regimeNote: "H4終値がEMA50を下回った場合はレンジへ降格",
  };
  return {
    candles,
    entry,
    macro,
    topTraders,
    deriv,
    ctx: {
      markPx: last,
      prevDayPx: candles[candles.length - 25].c,
      funding: "0.0000125",
      openInterest: String(Math.round(1.62e9 / last)),
      dayNtlVlm: 2.14e9,
    },
    fng: { value: 38, label: "Fear" },
  };
};

// ---------- metric derivation ----------
function deriveMetrics(candles, ctx, fng) {
  const closes = candles.map((c) => c.c);
  const price = ctx?.markPx ?? closes[closes.length - 1];
  const prevDay = ctx?.prevDayPx ?? closes[Math.max(0, closes.length - 25)];
  const chg24 = ((price - prevDay) / prevDay) * 100;

  const e20 = ema(closes, 20);
  const e50 = ema(closes, 50);
  const chop = choppiness(candles, 14);

  // slope of EMA20 over last 24 bars, normalized by price
  const lookback = Math.min(24, e20.length - 1);
  const slope = ((e20[e20.length - 1] - e20[e20.length - 1 - lookback]) / price) * 100;

  // Bearing
  let bearing, bearingColor, bearingNote;
  const trendy = chop < 50;
  if (chop >= 61.8 || Math.abs(slope) < 0.15) {
    bearing = "レンジ / 方向感なし";
    bearingColor = C.yellow;
    bearingNote = "明確なインパルスが出るまでブレイク狙いは見送りが無難";
  } else if (slope > 0) {
    bearing = trendy ? "上昇トレンド" : "チョッピー上昇";
    bearingColor = C.green;
    bearingNote = trendy
      ? "構造は上方向で一致。押し目のPULLBACK待ちが機能しやすい"
      : "上方向バイアスだがノイズ多め。ストップは広め or サイズ縮小";
  } else {
    bearing = trendy ? "下落トレンド" : "チョッピー下落";
    bearingColor = C.red;
    bearingNote = trendy
      ? "構造は下方向で一致。戻り売りが機能しやすい"
      : "下方向バイアスだがRSIクロス頻発。ストップアウトが増えやすい局面";
  }

  // Pulse: BB width percentile
  const bbw = bbWidthSeries(closes, 20);
  const bbPct = percentileRank(bbw.slice(0, -1), bbw[bbw.length - 1]);
  let pulse, pulseColor, pulseNote;
  if (bbPct < 25) {
    pulse = "QUIET";
    pulseColor = C.blue;
    pulseNote = "ボラ収縮中 — スクイーズ警戒。ブレイク後の初動は追わない";
  } else if (bbPct <= 75) {
    pulse = "TRADABLE";
    pulseColor = C.green;
    pulseNote = "ボラは正常レンジ — 標準のR:Rとストップ幅が機能する環境";
  } else {
    pulse = "WILD";
    pulseColor = C.red;
    pulseNote = "ボラ拡大 — サイズを落としストップを広く。往復ビンタ注意";
  }

  // Flow: 24h volume percentile vs rolling history
  const vols = candles.map((c) => c.v);
  const roll = [];
  for (let i = 24; i <= vols.length; i++) {
    roll.push(vols.slice(i - 24, i).reduce((a, b) => a + b, 0));
  }
  const volNow = roll[roll.length - 1];
  const volPct = percentileRank(roll.slice(0, -1), volNow);
  let flow, flowColor, flowNote;
  if (volPct < 30) {
    flow = "THIN";
    flowColor = C.yellow;
    flowNote = "参加者が薄い — ダマシのブレイクが出やすい。約定も滑りやすい";
  } else if (volPct <= 70) {
    flow = "HEALTHY";
    flowColor = C.green;
    flowNote = "参加度は健全 — モメンタムに追随する価値がある環境";
  } else {
    flow = "CROWDED";
    flowColor = C.red;
    flowNote = "参加過熱 — モメンタムは伸び切りの可能性。急反転リスクに備える";
  }

  // Funding / OI
  const fundingHr = ctx ? parseFloat(ctx.funding) : null;
  const fundingApr = fundingHr != null ? fundingHr * 24 * 365 * 100 : null;
  const oiUsd = ctx ? parseFloat(ctx.openInterest) * price : null;
  const vol24Usd = ctx ? parseFloat(ctx.dayNtlVlm) : null;

  // ATR(14) 現在値
  const atrSeg = candles.slice(-15);
  let atrSum14 = 0;
  for (let i = 1; i < atrSeg.length; i++) {
    const h = atrSeg[i].h, l = atrSeg[i].l, pc = atrSeg[i - 1].c;
    atrSum14 += Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
  }
  const atr = atrSum14 / 14;

  // Edge Factor composite
  const chopScore = Math.max(0, Math.min(100, 100 - chop));
  const aligned =
    (price > e20[e20.length - 1] && e20[e20.length - 1] > e50[e50.length - 1]) ||
    (price < e20[e20.length - 1] && e20[e20.length - 1] < e50[e50.length - 1]);
  const alignScore = aligned ? 90 : 35;
  const volRegimeScore = bbPct < 25 ? 45 : bbPct <= 75 ? 90 : 40;
  const flowScore = volPct < 30 ? 50 : volPct <= 70 ? 90 : 55;
  const fngVal = fng?.value ?? 50;
  const moodScore = fngVal <= 10 || fngVal >= 90 ? 40 : 75;
  const edge = Math.round(
    0.3 * chopScore + 0.25 * alignScore + 0.2 * volRegimeScore + 0.15 * flowScore + 0.1 * moodScore
  );
  let edgeLabel, edgeColor, edgeNote;
  if (edge >= 65) {
    edgeLabel = "明瞭 / 条件一致";
    edgeColor = C.green;
    edgeNote = "テクニカルと環境の一致度が高い。計画通りのセットアップを粛々と執行する局面。";
  } else if (edge >= 45) {
    edgeLabel = "混在 / 中程度";
    edgeColor = C.yellow;
    edgeNote = "一部の条件は揃うが確証に欠ける。サイズを落とすか、より明確なトリガーを待つ。";
  } else {
    edgeLabel = "混在 / 低明瞭度";
    edgeColor = C.orange;
    edgeNote = "方向性の合意が弱い。資本を温存し、より良い条件が揃うまで待機が最適。";
  }

  return {
    price, chg24, chop, slope, atr,
    bbWidthNow: bbw[bbw.length - 1],
    edgeParts: [
      { label: "レジーム (CHOP)", score: Math.round(chopScore), w: 30 },
      { label: "EMA整列", score: alignScore, w: 25 },
      { label: "ボラ環境", score: volRegimeScore, w: 20 },
      { label: "参加度", score: flowScore, w: 15 },
      { label: "センチメント", score: moodScore, w: 10 },
    ],
    bearing, bearingColor, bearingNote,
    pulse, pulseColor, pulseNote, bbPct,
    flow, flowColor, flowNote, volPct,
    fundingApr, fundingHr, oiUsd, vol24Usd,
    edge, edgeLabel, edgeColor, edgeNote,
    e20: e20[e20.length - 1], e50: e50[e50.length - 1],
  };
}

// ---------- small components ----------
export const Tag = ({ children, color }) => (
  <span
    className="px-2 py-0.5 rounded text-xs font-semibold tracking-wide"
    style={{ background: `${color}22`, color, fontFamily: FONT_MONO }}
  >
    {children}
  </span>
);

export const Panel = ({ title, right, children, accent }) => (
  <div
    className="rounded-xl p-4 flex flex-col"
    style={{ background: C.panel, border: `1px solid ${accent ? C.orangeDim : C.borderSoft}` }}
  >
    {(title || right) && (
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs uppercase tracking-widest" style={{ color: C.muted, fontFamily: FONT_MONO }}>
          {title}
        </div>
        {right}
      </div>
    )}
    {children}
  </div>
);

// tri-segment state bar (Flow / Pulse)
const StateBar = ({ labels, colors, pct }) => (
  <div className="mt-3">
    <div className="relative flex gap-1 h-2">
      {colors.map((col, i) => (
        <div key={i} className="flex-1 rounded-full" style={{ background: `${col}55` }} />
      ))}
      <div
        className="absolute top-1/2 w-1 h-4 rounded-full"
        style={{
          left: `${Math.max(1, Math.min(99, pct))}%`,
          transform: "translate(-50%,-50%)",
          background: C.text,
          boxShadow: "0 0 6px rgba(0,0,0,0.6)",
        }}
      />
    </div>
    <div className="flex justify-between mt-1.5 text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
      {labels.map((l) => (
        <span key={l}>{l}</span>
      ))}
    </div>
  </div>
);

// Edge Factor ring
const EdgeRing = ({ value, color }) => {
  const r = 34, cx = 42, cy = 42;
  const circ = 2 * Math.PI * r;
  const dash = (value / 100) * circ;
  return (
    // viewBox + flex-shrink無効化で、狭いパネルでもリングが潰れて数字が切れないようにする
    <svg width="84" height="84" viewBox="0 0 84 84" style={{ flexShrink: 0 }}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={C.border} strokeWidth="7" />
      <circle
        cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth="7"
        strokeLinecap="round" strokeDasharray={`${dash} ${circ - dash}`}
        transform={`rotate(-90 ${cx} ${cy})`}
        style={{ transition: "stroke-dasharray 0.8s ease" }}
      />
      <text x={cx} y={cy + 7} textAnchor="middle" fontSize="22" fontWeight="700" fill={color} fontFamily={FONT_MONO}>
        {value}
      </text>
    </svg>
  );
};

// Fear & Greed semicircle gauge
const MoodGauge = ({ value }) => {
  const angle = -90 + (value / 100) * 180;
  const arc = (from, to, col) => {
    const a1 = ((from / 100) * 180 - 180) * (Math.PI / 180);
    const a2 = ((to / 100) * 180 - 180) * (Math.PI / 180);
    const r = 54, cx = 70, cy = 70;
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const x2 = cx + r * Math.cos(a2), y2 = cy + r * Math.sin(a2);
    return <path d={`M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`} stroke={col} strokeWidth="9" fill="none" strokeLinecap="round" />;
  };
  return (
    <svg width="140" height="84" viewBox="0 0 140 84">
      {arc(2, 24, C.red)}
      {arc(27, 44, C.orange)}
      {arc(47, 53, C.yellow)}
      {arc(56, 73, C.green)}
      {arc(76, 98, C.yellow)}
      <g transform={`rotate(${angle} 70 70)`} style={{ transition: "transform 0.8s ease" }}>
        <line x1="70" y1="70" x2="70" y2="26" stroke={C.text} strokeWidth="2.5" strokeLinecap="round" />
      </g>
      <circle cx="70" cy="70" r="4" fill={C.text} />
    </svg>
  );
};

// candlestick chart
const CandleChart = ({ candles }) => {
  const data = candles.slice(-96);
  if (!data.length) return null;
  const W = 560, H = 180, padR = 52;
  const hi = Math.max(...data.map((c) => c.h));
  const lo = Math.min(...data.map((c) => c.l));
  const y = (v) => H - ((v - lo) / (hi - lo)) * (H - 12) - 6;
  const bw = (W - padR) / data.length;
  const mid = (hi + lo) / 2;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "180px" }}>
      {[hi, mid, lo].map((v, i) => (
        <g key={i}>
          <line x1="0" x2={W - padR} y1={y(v)} y2={y(v)} stroke={C.border} strokeDasharray="3 4" strokeWidth="1" />
          <text x={W - padR + 6} y={y(v) + 4} fontSize="10" fill={C.faint} fontFamily={FONT_MONO}>
            {Math.round(v).toLocaleString()}
          </text>
        </g>
      ))}
      {data.map((c, i) => {
        const up = c.c >= c.o;
        const col = up ? C.green : C.red;
        const x = i * bw + bw / 2;
        return (
          <g key={i}>
            <line x1={x} x2={x} y1={y(c.h)} y2={y(c.l)} stroke={col} strokeWidth="1" opacity="0.85" />
            <rect
              x={x - Math.max(1, bw * 0.32)}
              y={Math.min(y(c.o), y(c.c))}
              width={Math.max(2, bw * 0.64)}
              height={Math.max(1.2, Math.abs(y(c.o) - y(c.c)))}
              fill={col}
              opacity="0.95"
              rx="0.5"
            />
          </g>
        );
      })}
    </svg>
  );
};

// liquidation map (price levels vs estimated cluster size)
const LiqMap = ({ levels, price }) => {
  const sorted = [...levels].sort((a, b) => b.price - a.price);
  const max = Math.max(...levels.map((l) => l.size));
  return (
    <div className="flex flex-col gap-1">
      {sorted.map((l, i) => {
        const isCurrent =
          i < sorted.length - 1 && sorted[i].price > price && sorted[i + 1].price < price;
        const col = l.side === "short" ? C.green : C.red;
        return (
          <div key={i}>
            <div className="flex items-center gap-2">
              <div className="w-16 text-right text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
                {Math.round(l.price).toLocaleString()}
              </div>
              <div className="flex-1 h-3 rounded-sm" style={{ background: C.panelSoft }}>
                <div
                  className="h-3 rounded-sm"
                  style={{ width: `${(l.size / max) * 100}%`, background: `${col}`, opacity: 0.4 + 0.6 * (l.size / max) }}
                />
              </div>
              <div className="w-14 text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
                {fmtUsd(l.size)}
              </div>
            </div>
            {isCurrent && (
              <div className="flex items-center gap-2 my-1">
                <div className="w-16 text-right text-xs font-bold" style={{ color: C.orangeBright, fontFamily: FONT_MONO }}>
                  {Math.round(price).toLocaleString()}
                </div>
                <div className="flex-1 border-t border-dashed" style={{ borderColor: C.orange }} />
                <div className="w-14 text-xs" style={{ color: C.orangeBright, fontFamily: FONT_MONO }}>現在値</div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

// taker buy/sell delta bars — hover readout + axis values
const TakerBars = ({ series }) => {
  const [hover, setHover] = useState(null);
  const max = Math.max(...series.map((v) => Math.abs(v)));
  const W = 260, H = 72, padR = 42, bw = (W - padR) / series.length;
  const buyTotal = series.filter((v) => v > 0).reduce((a, b) => a + b, 0);
  const sellTotal = series.filter((v) => v < 0).reduce((a, b) => a + b, 0);
  const net = buyTotal + sellTotal;
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5 text-xs" style={{ fontFamily: FONT_MONO }}>
        {hover != null ? (
          <span style={{ color: series[hover] >= 0 ? C.green : C.red }}>
            {hover - series.length + 1}h時点: {series[hover] >= 0 ? "+" : "−"}{fmtUsd(Math.abs(series[hover]))}
          </span>
        ) : (
          <span style={{ color: C.faint }}>バーにカーソルで各時間の値</span>
        )}
        <span style={{ color: net >= 0 ? C.green : C.red }}>Net {net >= 0 ? "+" : "−"}{fmtUsd(Math.abs(net))}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "72px" }} onMouseLeave={() => setHover(null)}>
        <line x1="0" x2={W - padR} y1={H / 2} y2={H / 2} stroke={C.border} strokeWidth="1" />
        <text x={W - padR + 4} y={12} fontSize="9" fill={C.faint} fontFamily={FONT_MONO}>+{fmtUsd(max)}</text>
        <text x={W - padR + 4} y={H / 2 + 3} fontSize="9" fill={C.faint} fontFamily={FONT_MONO}>0</text>
        <text x={W - padR + 4} y={H - 4} fontSize="9" fill={C.faint} fontFamily={FONT_MONO}>−{fmtUsd(max)}</text>
        {series.map((v, i) => {
          const h = (Math.abs(v) / max) * (H / 2 - 6);
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              <rect x={i * bw} y={0} width={bw} height={H} fill="transparent" />
              <rect
                x={i * bw + 1}
                y={v >= 0 ? H / 2 - h : H / 2}
                width={Math.max(2, bw - 2)}
                height={Math.max(1, h)}
                fill={v >= 0 ? C.green : C.red}
                opacity={hover === i ? 1 : 0.75}
                rx="1"
              />
            </g>
          );
        })}
      </svg>
      <div className="flex justify-between text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
        <span>-24h</span><span>-12h</span><span>現在</span>
      </div>
      <div className="flex gap-4 mt-2 text-xs" style={{ fontFamily: FONT_MONO }}>
        <span style={{ color: C.green }}>買 +{fmtUsd(buyTotal)}</span>
        <span style={{ color: C.red }}>売 −{fmtUsd(Math.abs(sellTotal))}</span>
        <span style={{ color: C.muted }}>比率 {(buyTotal / Math.abs(sellTotal)).toFixed(2)}</span>
      </div>
    </div>
  );
};

// generic line/area chart with axis values + end callout
const DetailLine = ({ points, color, fmtVal, hours, baselineZero }) => {
  const [hover, setHover] = useState(null);
  const W = 260, H = 96, padR = 46;
  const hi = Math.max(...points), lo = Math.min(...points);
  const span = hi - lo || 1;
  const y = (v) => H - ((v - lo) / span) * (H - 14) - 7;
  const x = (i) => (i / (points.length - 1)) * (W - padR);
  const path = points.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(v)}`).join(" ");
  const area = `${path} L ${x(points.length - 1)} ${H} L 0 ${H} Z`;
  const cur = hover != null ? points[hover] : points[points.length - 1];
  const curIdx = hover != null ? hover : points.length - 1;
  return (
    <div>
      <div className="text-xs mb-1" style={{ fontFamily: FONT_MONO, color }}>
        {hover != null ? `${Math.round((curIdx / (points.length - 1) - 1) * hours)}h時点: ` : "現在: "}
        {fmtVal(cur)}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: "96px" }}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const i = Math.round(((e.clientX - r.left) / r.width) * (points.length - 1) * (W / (W - padR)));
          setHover(Math.max(0, Math.min(points.length - 1, i)));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {[hi, (hi + lo) / 2, lo].map((v, i) => (
          <g key={i}>
            <line x1="0" x2={W - padR} y1={y(v)} y2={y(v)} stroke={C.border} strokeDasharray="3 4" strokeWidth="1" />
            <text x={W - padR + 4} y={y(v) + 3} fontSize="9" fill={C.faint} fontFamily={FONT_MONO}>{fmtVal(v)}</text>
          </g>
        ))}
        {baselineZero && lo < 0 && hi > 0 && (
          <line x1="0" x2={W - padR} y1={y(0)} y2={y(0)} stroke={C.muted} strokeWidth="1" />
        )}
        <path d={area} fill={color} opacity="0.10" />
        <path d={path} fill="none" stroke={color} strokeWidth="1.8" />
        <circle cx={x(curIdx)} cy={y(cur)} r="3" fill={color} />
      </svg>
      <div className="flex justify-between text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
        <span>-{hours}h</span><span>-{hours / 2}h</span><span>現在</span>
      </div>
    </div>
  );
};

// ---------- main ----------
export default function HybridMacroDeskBTC() {
  const [candles, setCandles] = useState([]);
  const [ctx, setCtx] = useState(null);
  const [fng, setFng] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [updated, setUpdated] = useState(null);
  const [aiText, setAiText] = useState(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [dataMode, setDataMode] = useState(null); // 'live' | 'demo'
  const [deriv, setDeriv] = useState(null); // ポジショニング系 (現状はCoinglass接続前のためサンプル)
  const [entry, setEntry] = useState(null); // Entry Console 連携 (現状はサンプル)
  const [macro, setMacro] = useState(null); // 経済カレンダー (現状はサンプル)
  const [liqReal, setLiqReal] = useState(null); // HL実データ清算マップ (localhost:8787 検出時)
  const [topT, setTopT] = useState(null); // Smart Money サンプル
  const [topReal, setTopReal] = useState(null); // Smart Money 実データ (バックエンド検出時)
  const [derivMode, setDerivMode] = useState("demo"); // Positioning: 'live' (バックエンド) | 'demo'
  const [macroMode, setMacroMode] = useState("demo"); // Macro: 'live' | 'demo'
  const [entryMode, setEntryMode] = useState("demo"); // Entry Engine: 'live' | 'demo'

  const STATES = ["IDLE", "ARMED", "PULLBACK", "TRIGGERED"];
  const REGIMES = {
    uptrend: {
      label: "上昇", color: C.green,
      rows: [["現物 BTC", "ホールド継続"], ["パーペチュアル", "ロング (押し目のみ)"], ["グリッド", "停止 / 縮小"]],
    },
    range: {
      label: "レンジ", color: C.yellow,
      rows: [["現物 BTC", "ホールド"], ["パーペチュアル", "ノーポジション"], ["グリッド", "稼働 (主戦略)"]],
    },
    downtrend: {
      label: "下落", color: C.red,
      rows: [["現物 BTC", "ヘッジ"], ["パーペチュアル", "ショート (戻り売り)"], ["グリッド", "停止"]],
    },
  };

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setErr(null);

    const demo = generateDemo();

    // 1) バックエンド (localhost:8787) の自動検出
    const health = await jget("/api/health", 2500);
    const backendUp = !!health;

    // 2) 市場データ: /api/market 優先 → 直接HL → デモ
    let marketLive = false;
    let liveFundingCompare = null;
    if (backendUp) {
      const mk = await jget("/api/market", 3000);
      if (mk && mk.candles?.length && mk.price) {
        const price = mk.price;
        const prevDayPx = mk.chg24 != null ? price / (1 + mk.chg24 / 100) : price;
        const funding = mk.fundingApr != null ? mk.fundingApr / (24 * 365 * 100) : 0;
        const openInterest = mk.oiUsd != null && price ? mk.oiUsd / price : 0;
        setCandles(mk.candles);
        setCtx({
          markPx: price,
          prevDayPx,
          funding: String(funding),
          openInterest: String(openInterest),
          dayNtlVlm: mk.vol24Usd ?? 0,
        });
        setFng(mk.fng ?? null);
        liveFundingCompare = mk.fundingCompare?.length ? mk.fundingCompare : null;
        setDataMode("live");
        marketLive = true;
      }
    }

    if (!marketLive) {
      // 外部APIはブラウザから直接叩かず backend に一本化する方針のため、
      // /api/market が使えない (backend未検出/起動直後) 場合はデモへフォールバックする。
      setCandles(demo.candles);
      setCtx(demo.ctx);
      setFng(demo.fng);
      setDataMode("demo");
    }

    // 3) Positioning (deriv): /api/derivs 優先 → サンプル
    let derivLive = false;
    if (backendUp) {
      const dv = await jget("/api/derivs", 3000);
      if (dv && Array.isArray(dv.takerSeries) && dv.takerSeries.length) {
        setDeriv({
          ...dv,
          fundingCompare: liveFundingCompare ?? demo.deriv.fundingCompare,
          liqLevels: demo.deriv.liqLevels,   // 清算マップ実データは /api/liq-map 検出時に差し替え
          oiHistMult: demo.deriv.oiHistMult, // oiHist 不在時のフォールバック用
        });
        setDerivMode("live");
        derivLive = true;
      }
    }
    if (!derivLive) { setDeriv(demo.deriv); setDerivMode("demo"); }

    // 4) Macro: /api/macro 優先 → サンプル
    let macroLive = false;
    if (backendUp) {
      const mc = await jget("/api/macro", 3000);
      if (mc && Array.isArray(mc.events) && mc.events.length) {
        setMacro(mc.events);
        setMacroMode("live");
        macroLive = true;
      }
    }
    if (!macroLive) { setMacro(demo.macro); setMacroMode("demo"); }

    // 5) Entry Console 連携: /api/entry-state (不在なら 404 → サンプル+黄タグ)
    let entryLive = false;
    if (backendUp) {
      const adapted = adaptEntryState(await jget("/api/entry-state", 2500));
      if (adapted) { setEntry(adapted); setEntryMode("live"); entryLive = true; }
    }
    if (!entryLive) { setEntry(demo.entry); setEntryMode("demo"); }

    // 6) HL実データ清算マップ / Smart Money (バックエンド検出時)
    const liq = backendUp ? await jget("/api/liq-map", 2500) : null;
    if (liq && liq.topClusters?.length) {
      const levels = liq.topClusters.map((c) => ({ price: c.px, size: c.usd, side: c.side }));
      setLiqReal({ coveragePct: liq.coveragePct, positionsTracked: liq.positionsTracked, levels });
    } else {
      setLiqReal(null);
    }
    const top = backendUp ? await jget("/api/top-traders", 2500) : null;
    setTopReal(top && top.traders?.length ? top : null);
    setTopT(demo.topTraders);

    setUpdated(new Date());
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const m = useMemo(
    () => (candles.length > 60 && ctx ? deriveMetrics(candles, ctx, fng) : null),
    [candles, ctx, fng]
  );

  // AI overview
  useEffect(() => {
    if (!m || aiText || aiLoading) return;
    let cancelled = false;
    (async () => {
      setAiLoading(true);
      try {
        const payload = {
          価格: Math.round(m.price),
          前日比pct: +m.chg24.toFixed(2),
          Choppiness指数: +m.chop.toFixed(1),
          方向性: m.bearing,
          ボラ状態: m.pulse,
          BB幅パーセンタイル: Math.round(m.bbPct),
          参加度: m.flow,
          出来高パーセンタイル: Math.round(m.volPct),
          ファンディングAPR_pct: m.fundingApr != null ? +m.fundingApr.toFixed(1) : null,
          OI_USD: m.oiUsd ? Math.round(m.oiUsd / 1e6) + "M" : null,
          FearGreed: fng ? `${fng.value} (${fng.label})` : "不明",
          EdgeFactor: m.edge,
        };
        // AI概況はバックエンド経由 (Anthropic APIキーはフロントに出さない — SPEC §9)。
        // バックエンド未検出/失敗時は null のままにし、下のレンダリングでルールベース文へフォールバック。
        const data = await jpost("/api/ai-overview", payload, 12000);
        const text = (data?.text || "").trim();
        if (!cancelled) setAiText(text || null);
      } catch {
        if (!cancelled) setAiText(null);
      } finally {
        if (!cancelled) setAiLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [m]); // eslint-disable-line

  const up = m ? m.chg24 >= 0 : true;

  return (
    <div className="min-h-screen w-full px-4 py-6 md:px-8" style={{ background: C.bg, color: C.text, fontFamily: FONT_UI }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap');`}</style>

      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-4 max-w-6xl mx-auto">
        <div>
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded-full flex items-center justify-center text-sm font-bold"
              style={{ background: `${C.orange}22`, color: C.orange, border: `1px solid ${C.orangeDim}`, fontFamily: FONT_MONO }}
            >
              ₿
            </div>
            <div>
              <h1 className="text-3xl font-bold tracking-tight" style={{ fontFamily: FONT_MONO }}>
                BTC-PERP
              </h1>
              <div className="text-sm" style={{ color: C.muted }}>
                Bitcoin 無期限先物 · Hyperliquid
              </div>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {dataMode && (
            <Tag color={dataMode === "live" ? C.green : C.yellow}>
              {dataMode === "live" ? "● LIVE" : "◌ DEMO"}
            </Tag>
          )}
          <div className="text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
            {updated ? `更新 ${updated.toLocaleTimeString("ja-JP")}` : ""}
          </div>
          <button
            onClick={() => { setAiText(null); fetchAll(); }}
            className="px-3 py-1.5 rounded-lg text-sm font-medium"
            style={{ background: `${C.orange}1e`, color: C.orangeBright, border: `1px solid ${C.orangeDim}` }}
          >
            {loading ? "取得中…" : "更新"}
          </button>
          <a
            href="#/setup"
            className="px-3 py-1.5 rounded-lg text-sm font-medium"
            style={{ color: C.muted, border: `1px solid ${C.borderSoft}`, textDecoration: "none" }}
            title="セットアップコンソール (別画面)"
          >
            セットアップ ⇢
          </a>
        </div>
      </div>

      {dataMode === "demo" && (
        <div className="max-w-6xl mx-auto mt-4 rounded-lg px-4 py-3 text-sm leading-relaxed" style={{ background: `${C.blue}14`, color: C.blue, border: `1px solid ${C.blueDim}` }}>
          プレビュー環境(サンドボックス)は外部APIへの接続を制限しているため、デモデータを表示しています。
          レイアウト・配色・判定ロジックの確認用です。ローカル環境やデプロイ後は同じコードがHyperliquidのライブデータで動作します。
        </div>
      )}
      {err && (
        <div className="max-w-6xl mx-auto mt-4 rounded-lg px-4 py-3 text-sm" style={{ background: `${C.red}18`, color: C.red, border: `1px solid ${C.redDim}` }}>
          {err}
        </div>
      )}

      {m && (
        <div className="max-w-6xl mx-auto mt-6 grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* price + chart */}
          <div className="lg:col-span-2">
            <Panel title="価格 · 1時間足 (直近4日)">
              <div className="flex items-baseline gap-4 mb-2">
                <div className="text-4xl font-bold" style={{ fontFamily: FONT_MONO, color: C.text }}>
                  {fmtPx(m.price)}
                </div>
                <div className="text-lg font-semibold" style={{ fontFamily: FONT_MONO, color: up ? C.green : C.red }}>
                  {up ? "▲" : "▼"} {m.chg24.toFixed(2)}%
                </div>
                <div className="text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>24h</div>
              </div>
              <CandleChart candles={candles} />
              <div className="flex gap-5 mt-2 text-xs" style={{ color: C.muted, fontFamily: FONT_MONO }}>
                <span>EMA20 <span style={{ color: C.text }}>{fmtPx(m.e20)}</span></span>
                <span>EMA50 <span style={{ color: C.text }}>{fmtPx(m.e50)}</span></span>
                <span>CHOP(14) <span style={{ color: m.chop > 61.8 ? C.yellow : C.text }}>{m.chop.toFixed(1)}</span></span>
              </div>
            </Panel>
          </div>

          {/* Edge Factor */}
          <Panel title="Edge Factor" accent>
            <div className="flex items-center gap-4">
              <EdgeRing value={m.edge} color={m.edgeColor} />
              <div>
                <Tag color={m.edgeColor}>{m.edgeLabel}</Tag>
                <div className="text-xs mt-2 leading-relaxed" style={{ color: C.muted }}>
                  {m.edgeNote}
                </div>
              </div>
            </div>
            <div className="mt-4 pt-3 flex flex-col gap-2" style={{ borderTop: `1px solid ${C.borderSoft}` }}>
              {m.edgeParts.map((p) => (
                <div key={p.label} className="flex items-center gap-2 text-xs" style={{ fontFamily: FONT_MONO }}>
                  <div className="w-28" style={{ color: C.faint }}>{p.label}</div>
                  <div className="flex-1 h-1.5 rounded-full" style={{ background: C.panelSoft }}>
                    <div className="h-1.5 rounded-full" style={{ width: `${p.score}%`, background: p.score >= 70 ? C.green : p.score >= 45 ? C.yellow : C.red }} />
                  </div>
                  <div className="w-8 text-right" style={{ color: C.text }}>{p.score}</div>
                  <div className="w-8 text-right" style={{ color: C.faint }}>×{p.w}%</div>
                </div>
              ))}
            </div>
          </Panel>

          {/* AI overview */}
          <div className="lg:col-span-3">
            <div className="rounded-xl p-4" style={{ background: C.panelSoft, borderLeft: `3px solid ${C.orange}`, border: `1px solid ${C.borderSoft}`, borderLeftWidth: "3px", borderLeftColor: C.orange }}>
              <div className="flex items-center gap-2 mb-1.5">
                <span style={{ color: C.orangeBright }}>✦</span>
                <span className="text-xs uppercase tracking-widest font-semibold" style={{ color: C.orangeBright, fontFamily: FONT_MONO }}>
                  AI Overview{dataMode === "demo" ? " (デモデータ基準)" : ""}
                </span>
              </div>
              <div className="text-sm leading-relaxed" style={{ color: C.text }}>
                {aiLoading ? (
                  <span style={{ color: C.muted }}>指標データからAI概況を生成中…</span>
                ) : aiText ? (
                  aiText
                ) : (
                  <span style={{ color: C.muted }}>
                    {m.bearing}。ボラは{m.pulse}、参加度は{m.flow}。{m.edgeNote}
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* ===== Entry Engine section ===== */}
          {entry && (() => {
            const stateIdx = STATES.indexOf(entry.state);
            const reg = REGIMES[entry.regime];
            const dirColor = entry.direction === "LONG" ? C.green : entry.direction === "SHORT" ? C.red : C.faint;
            const okCount = entry.pillars.filter((p) => p.ok).length;
            return (
              <>
                <div className="lg:col-span-3 flex items-center gap-3 mt-2">
                  <div className="text-xs uppercase tracking-widest font-semibold" style={{ color: C.orangeBright, fontFamily: FONT_MONO }}>
                    Entry Engine — ステートマシン
                  </div>
                  <div className="flex-1 border-t" style={{ borderColor: C.borderSoft }} />
                  {entryMode === "live"
                    ? <Tag color={C.green}>実データ · Entry Console 連携</Tag>
                    : <Tag color={C.yellow}>サンプル · Entry Console 連携予定</Tag>}
                </div>

                {/* Entry State */}
                <div className="lg:col-span-2">
                  <Panel
                    title="Entry State — 判定進行"
                    accent
                    right={
                      <div className="flex items-center gap-2">
                        <Tag color={dirColor}>{entry.direction ?? "—"}</Tag>
                        <Tag color={C.orange}>{entry.state} · {Math.floor(entry.sinceMin / 60)}h{entry.sinceMin % 60}m経過</Tag>
                      </div>
                    }
                  >
                    {/* progress */}
                    <div className="flex items-center mt-1 mb-4">
                      {STATES.map((s, i) => (
                        <div key={s} className="flex items-center" style={{ flex: i < STATES.length - 1 ? 1 : "none" }}>
                          <div className="flex flex-col items-center gap-1.5">
                            <div
                              className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold"
                              style={{
                                fontFamily: FONT_MONO,
                                background: i < stateIdx ? `${C.green}22` : i === stateIdx ? `${C.orange}2b` : C.panelSoft,
                                color: i < stateIdx ? C.green : i === stateIdx ? C.orangeBright : C.faint,
                                border: `1.5px solid ${i < stateIdx ? C.green : i === stateIdx ? C.orange : C.borderSoft}`,
                                boxShadow: i === stateIdx ? `0 0 10px ${C.orange}55` : "none",
                              }}
                            >
                              {i < stateIdx ? "✓" : i + 1}
                            </div>
                            <div className="text-xs" style={{ fontFamily: FONT_MONO, color: i === stateIdx ? C.orangeBright : i < stateIdx ? C.green : C.faint }}>
                              {s}
                            </div>
                          </div>
                          {i < STATES.length - 1 && (
                            <div className="flex-1 h-px mx-2 mb-5" style={{ background: i < stateIdx ? C.green : C.borderSoft }} />
                          )}
                        </div>
                      ))}
                    </div>

                    {/* 3 pillars checklist */}
                    <div className="text-xs uppercase tracking-widest mb-2" style={{ color: C.muted, fontFamily: FONT_MONO }}>
                      3本柱 条件充足 <span style={{ color: okCount === 3 ? C.green : C.yellow }}>{okCount}/3</span>
                    </div>
                    <div className="flex flex-col gap-2">
                      {entry.pillars.map((p) => (
                        <div key={p.name} className="flex items-center gap-3 rounded-lg px-3 py-2" style={{ background: C.panelSoft }}>
                          <div className="w-5 text-center font-bold" style={{ color: p.ok ? C.green : C.red, fontFamily: FONT_MONO }}>
                            {p.ok ? "✓" : "✗"}
                          </div>
                          <div className="w-28 text-sm font-semibold" style={{ color: C.text }}>{p.name}</div>
                          <div className="flex-1 text-xs" style={{ color: C.muted, fontFamily: FONT_MONO }}>{p.value}</div>
                        </div>
                      ))}
                    </div>

                    <div className="mt-3 text-xs" style={{ color: C.muted, fontFamily: FONT_MONO }}>
                      次の遷移条件: <span style={{ color: C.text }}>{entry.nextCondition}</span>
                    </div>

                    {/* human veto */}
                    <div className="mt-3 rounded-lg px-3 py-2.5 text-xs leading-relaxed" style={{ background: `${C.orange}12`, border: `1px dashed ${C.orangeDim}`, color: C.orangeBright }}>
                      ⚖ GO but WAIT — 本エンジンは仮説の提示まで。TRIGGEREDに達しても執行の最終判断は常にトレーダーの拒否権に従う。
                    </div>
                  </Panel>
                </div>

                {/* Regime */}
                <Panel title="Regime — レジーム判定">
                  <div className="grid grid-cols-3 gap-1.5 mb-3">
                    {Object.entries(REGIMES).map(([key, r]) => (
                      <div
                        key={key}
                        className="rounded-lg py-2 text-center text-xs font-semibold"
                        style={{
                          background: key === entry.regime ? `${r.color}26` : C.panelSoft,
                          color: key === entry.regime ? r.color : C.faint,
                          border: `1px solid ${key === entry.regime ? r.color : C.borderSoft}`,
                        }}
                      >
                        {r.label}
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center gap-2 mb-1">
                    <div className="text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>確信度</div>
                    <div className="flex-1 h-1.5 rounded-full" style={{ background: C.panelSoft }}>
                      <div className="h-1.5 rounded-full" style={{ width: `${entry.regimeConf * 100}%`, background: reg.color }} />
                    </div>
                    <div className="text-xs font-bold" style={{ color: reg.color, fontFamily: FONT_MONO }}>{Math.round(entry.regimeConf * 100)}%</div>
                  </div>
                  <div className="mt-3 text-xs uppercase tracking-widest" style={{ color: C.muted, fontFamily: FONT_MONO }}>
                    戦略配分
                  </div>
                  <div className="mt-2 flex flex-col gap-1.5">
                    {reg.rows.map(([k, v]) => (
                      <div key={k} className="flex justify-between text-xs rounded-lg px-3 py-2" style={{ background: C.panelSoft }}>
                        <span style={{ color: C.muted }}>{k}</span>
                        <span className="font-semibold" style={{ color: C.text, fontFamily: FONT_MONO }}>{v}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 text-xs leading-relaxed" style={{ color: C.muted }}>
                    降格条件: {entry.regimeNote}
                  </div>
                </Panel>

                {/* セットアップ — 指定値(entry_state.json)を最優先、無ければ方向性ベースの機械式目安 */}
                {(() => {
                  // (1) entry_state.json の setup(指定値)があればそれを表示 — アプリは表示のみ (SPEC §7)
                  const sv = entry.setup;
                  if (sv) {
                    const svColor = sv.side === "LONG" ? C.green : C.red;
                    const svR = Math.abs(sv.entry - sv.stop) || 1;
                    const svRPct = (svR / m.price) * 100;
                    const svTiles = [
                      { k: "エントリー", sub: "指定値", v: sv.entry, c: C.orangeBright },
                      { k: "損切り", sub: "指定値", v: sv.stop, c: C.red },
                      ...sv.targets.map((t, i) => ({
                        k: `利確 T${i + 1}`, sub: `${(Math.abs(t - sv.entry) / svR).toFixed(1)}R`, v: t, c: C.green,
                      })),
                    ];
                    return (
                      <div className="lg:col-span-3">
                        <Panel
                          title="セットアップ — 指定値 (Entry Console 連携)"
                          accent
                          right={
                            <div className="flex items-center gap-2">
                              <Tag color={svColor}>{sv.side}</Tag>
                              <Tag color={C.green}>指定値 · entry_state.json</Tag>
                            </div>
                          }
                        >
                          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                            {svTiles.map((t) => (
                              <div key={t.k} className="rounded-lg px-3 py-2.5" style={{ background: C.panelSoft, border: `1px solid ${C.borderSoft}` }}>
                                <div className="text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>{t.k}</div>
                                <div className="text-lg font-bold" style={{ color: t.c, fontFamily: FONT_MONO }}>{fmtPx(t.v)}</div>
                                <div className="text-xs" style={{ color: C.faint }}>{t.sub}</div>
                              </div>
                            ))}
                          </div>
                          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs" style={{ fontFamily: FONT_MONO }}>
                            <span style={{ color: C.muted }}>リスク幅(1R) <span style={{ color: C.text }}>${Math.round(svR).toLocaleString()} ({svRPct.toFixed(2)}%)</span></span>
                            <span style={{ color: C.muted }}>リスク:リワード <span style={{ color: C.green }}>{sv.targets.map((t, i) => `T${i + 1} 1:${(Math.abs(t - sv.entry) / svR).toFixed(1)}`).join(" · ")}</span></span>
                            <span style={{ color: C.muted }}>現在値 <span style={{ color: C.text }}>{fmtPx(m.price)}</span> はエントリーの <span style={{ color: m.price >= sv.entry ? C.green : C.red }}>{m.price >= sv.entry ? "上" : "下"}</span></span>
                          </div>
                          {sv.note && (
                            <div className="text-xs mt-3 leading-relaxed" style={{ color: C.muted }}>
                              <span style={{ color: C.orangeBright }}>✦ </span>{sv.note}
                            </div>
                          )}
                          <div className="mt-3 rounded-lg px-3 py-2.5 text-xs leading-relaxed" style={{ background: `${C.orange}12`, border: `1px dashed ${C.orangeDim}`, color: C.orangeBright }}>
                            ⚖ GO but WAIT — Entry Console で設定した指定値の表示。建玉サイズ・執行・最終判断は常にトレーダーの拒否権に従う。
                          </div>
                        </Panel>
                      </div>
                    );
                  }

                  // (2) 指定値が無ければ方向性ベースの機械式目安 (ユーザー承認のもと SPEC§0 を上書きして追加)
                  const dir = entry.direction; // LONG | SHORT | null
                  const long = dir === "LONG";
                  const short = dir === "SHORT";
                  if (!long && !short) {
                    return (
                      <div className="lg:col-span-3">
                        <Panel title="セットアップ — 方向性ベースの目安 (EMA20 × ATR × R)" accent>
                          <div className="text-sm" style={{ color: C.muted }}>
                            方向未確定 ({dir ?? "—"}) — Entry Engine が LONG / SHORT を示すと、その方向に沿った
                            エントリー・損切り・利確の目安を表示します。
                          </div>
                        </Panel>
                      </div>
                    );
                  }
                  const sign = long ? 1 : -1;
                  const dirColor = long ? C.green : C.red;
                  const atr = m.atr;
                  const R = 1.5 * atr;                 // 損切り幅 = 1.5×ATR(14)
                  const entryPx = m.e20;               // 押し目/戻りの基準 = EMA20
                  const stop = entryPx - sign * R;
                  const t1 = entryPx + sign * R;       // 1R
                  const t2 = entryPx + sign * 2 * R;   // 2R
                  const t3 = entryPx + sign * 3 * R;   // 3R
                  const rPct = (R / m.price) * 100;
                  const tiles = [
                    { k: "エントリー目安", sub: "EMA20 押し目/戻り", v: entryPx, c: C.orangeBright },
                    { k: "損切り", sub: "1.5×ATR", v: stop, c: C.red },
                    { k: "利確 T1", sub: "1R", v: t1, c: C.green },
                    { k: "利確 T2", sub: "2R", v: t2, c: C.green },
                    { k: "利確 T3", sub: "3R", v: t3, c: C.green },
                  ];
                  return (
                    <div className="lg:col-span-3">
                      <Panel
                        title="セットアップ — 方向性ベースの目安 (EMA20 × ATR × R)"
                        accent
                        right={
                          <div className="flex items-center gap-2">
                            <Tag color={dirColor}>{dir}</Tag>
                            <Tag color={C.orange}>ATR(14) ${Math.round(atr).toLocaleString()}</Tag>
                          </div>
                        }
                      >
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                          {tiles.map((t) => (
                            <div key={t.k} className="rounded-lg px-3 py-2.5" style={{ background: C.panelSoft, border: `1px solid ${C.borderSoft}` }}>
                              <div className="text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>{t.k}</div>
                              <div className="text-lg font-bold" style={{ color: t.c, fontFamily: FONT_MONO }}>{fmtPx(t.v)}</div>
                              <div className="text-xs" style={{ color: C.faint }}>{t.sub}</div>
                            </div>
                          ))}
                        </div>
                        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs" style={{ fontFamily: FONT_MONO }}>
                          <span style={{ color: C.muted }}>リスク幅(1R) <span style={{ color: C.text }}>${Math.round(R).toLocaleString()} ({rPct.toFixed(2)}%)</span></span>
                          <span style={{ color: C.muted }}>リスク:リワード <span style={{ color: C.green }}>T1 1:1 · T2 1:2 · T3 1:3</span></span>
                          <span style={{ color: C.muted }}>現在値 <span style={{ color: C.text }}>{fmtPx(m.price)}</span> はエントリー目安の <span style={{ color: m.price >= entryPx ? C.green : C.red }}>{m.price >= entryPx ? "上" : "下"}</span></span>
                        </div>
                        <div className="text-xs mt-3 leading-relaxed" style={{ color: C.muted }}>
                          Entry Engine の方向({dir})に沿った機械式の目安 — EMA20への押し目/戻りをエントリー、1.5×ATRを損切り、
                          そこからのR倍数を利確に置いた素案。上下の<span style={{ color: C.text }}>清算クラスター</span>を磁石として利確位置を微調整すると精度が上がる。
                        </div>
                        <div className="mt-3 rounded-lg px-3 py-2.5 text-xs leading-relaxed" style={{ background: `${C.orange}12`, border: `1px dashed ${C.orangeDim}`, color: C.orangeBright }}>
                          ⚖ GO but WAIT — これは方向性から導いた目安であって売買助言ではない。建玉サイズ・執行・最終判断は常にトレーダーの拒否権に従う。
                        </div>
                      </Panel>
                    </div>
                  );
                })()}
              </>
            );
          })()}

          {/* Market Mood */}
          <Panel title="Market Mood" right={fng && <Tag color={fng.value < 45 ? C.red : fng.value > 55 ? C.green : C.yellow}>{fng.label}</Tag>}>
            {fng ? (
              <div className="flex items-center gap-4">
                <MoodGauge value={fng.value} />
                <div>
                  <div className="text-3xl font-bold" style={{ fontFamily: FONT_MONO, color: fng.value < 45 ? C.red : fng.value > 55 ? C.green : C.yellow }}>
                    {fng.value}
                  </div>
                  <div className="text-xs mt-1" style={{ color: C.muted }}>
                    Crypto Fear & Greed Index
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-sm" style={{ color: C.faint }}>Fear & Greed を取得できませんでした</div>
            )}
            <div className="text-xs mt-3 leading-relaxed" style={{ color: C.muted }}>
              {fng && fng.value >= 75 && "極端な強欲圏 — 群衆と同方向のポジションは利確圧力に注意。"}
              {fng && fng.value >= 55 && fng.value < 75 && "リスクオン寄り — 市場は買い意欲を維持。過熱の兆候は監視。"}
              {fng && fng.value >= 45 && fng.value < 55 && "中立 — センチメント単体では方向のエッジなし。"}
              {fng && fng.value >= 25 && fng.value < 45 && "恐怖寄り — 悲観の織り込みが進行。逆張りはトリガー必須。"}
              {fng && fng.value < 25 && "極端な恐怖圏 — 投げ売り局面。ナイフ掴みはサイズ管理を厳格に。"}
            </div>
          </Panel>

          {/* Derivatives */}
          <Panel title="デリバティブ">
            <div className="grid grid-cols-2 gap-x-4 gap-y-4">
              <div>
                <div className="text-xs mb-1" style={{ color: C.faint, fontFamily: FONT_MONO }}>Funding (APR)</div>
                <div className="text-xl font-bold" style={{ fontFamily: FONT_MONO, color: m.fundingApr > 15 ? C.red : m.fundingApr < 0 ? C.blue : C.green }}>
                  {m.fundingApr != null ? `${m.fundingApr.toFixed(1)}%` : "—"}
                </div>
                <div className="text-xs mt-0.5" style={{ color: C.faint }}>
                  {m.fundingApr > 15 ? "ロング過熱気味" : m.fundingApr < 0 ? "ショート優勢" : "正常圏"}
                </div>
              </div>
              <div>
                <div className="text-xs mb-1" style={{ color: C.faint, fontFamily: FONT_MONO }}>Open Interest</div>
                <div className="text-xl font-bold" style={{ fontFamily: FONT_MONO, color: C.text }}>{fmtUsd(m.oiUsd)}</div>
              </div>
              <div>
                <div className="text-xs mb-1" style={{ color: C.faint, fontFamily: FONT_MONO }}>24h 出来高</div>
                <div className="text-xl font-bold" style={{ fontFamily: FONT_MONO, color: C.text }}>{fmtUsd(m.vol24Usd)}</div>
              </div>
              <div>
                <div className="text-xs mb-1" style={{ color: C.faint, fontFamily: FONT_MONO }}>EMA20/50 乖離</div>
                <div className="text-xl font-bold" style={{ fontFamily: FONT_MONO, color: m.e20 >= m.e50 ? C.green : C.red }}>
                  {(((m.e20 - m.e50) / m.price) * 100).toFixed(2)}%
                </div>
              </div>
            </div>
          </Panel>

          {/* Bearing */}
          <Panel title="Bearing — 方向性">
            <div className="text-xl font-bold" style={{ color: m.bearingColor, fontFamily: FONT_MONO }}>
              {m.bearing}
            </div>
            <div className="text-xs mt-2 leading-relaxed" style={{ color: C.muted }}>{m.bearingNote}</div>
            <div className="mt-3 text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
              EMA20勾配(24h): <span style={{ color: m.slope >= 0 ? C.green : C.red }}>{m.slope >= 0 ? "+" : ""}{m.slope.toFixed(2)}%</span>
            </div>
          </Panel>

          {/* Flow */}
          <Panel title="Flow — 参加度">
            <div className="flex items-baseline justify-between">
              <div className="text-xl font-bold" style={{ color: m.flowColor, fontFamily: FONT_MONO }}>{m.flow}</div>
              <div className="text-xs" style={{ color: C.muted, fontFamily: FONT_MONO }}>P{Math.round(m.volPct)}</div>
            </div>
            <StateBar labels={["Thin", "Healthy", "Crowded"]} colors={[C.yellow, C.green, C.red]} pct={m.volPct} />
            <div className="mt-3 text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
              24h出来高 <span style={{ color: C.text }}>{fmtUsd(m.vol24Usd)}</span> · 過去13日中の位置 <span style={{ color: m.flowColor }}>{Math.round(m.volPct)}%ile</span>
            </div>
            <div className="text-xs mt-2 leading-relaxed" style={{ color: C.muted }}>{m.flowNote}</div>
          </Panel>

          {/* Pulse */}
          <Panel title="Pulse — ボラティリティ">
            <div className="flex items-baseline justify-between">
              <div className="text-xl font-bold" style={{ color: m.pulseColor, fontFamily: FONT_MONO }}>{m.pulse}</div>
              <div className="text-xs" style={{ color: C.muted, fontFamily: FONT_MONO }}>P{Math.round(m.bbPct)}</div>
            </div>
            <StateBar labels={["Quiet", "Tradable", "Wild"]} colors={[C.blue, C.green, C.red]} pct={m.bbPct} />
            <div className="mt-3 text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
              BB幅(20) <span style={{ color: C.text }}>{(m.bbWidthNow * 100).toFixed(2)}%</span> · ATR(14) <span style={{ color: C.text }}>${Math.round(m.atr).toLocaleString()}</span> <span style={{ color: C.faint }}>({((m.atr / m.price) * 100).toFixed(2)}%)</span>
            </div>
            <div className="text-xs mt-2 leading-relaxed" style={{ color: C.muted }}>{m.pulseNote}</div>
          </Panel>

          {/* ===== Positioning section ===== */}
          {deriv && (() => {
            const oiUp = deriv.oiChangePct24h >= 0;
            const pxUp = m.chg24 >= 0;
            const quad = pxUp
              ? oiUp
                ? { label: "新規ロング主導", color: C.green, note: "価格↑×OI↑ — 新規買いがトレンドを牽引。継続性が高い上昇" }
                : { label: "ショートカバー主導", color: C.yellow, note: "価格↑×OI↓ — 買い戻しによる上昇。燃料切れに注意、追随は慎重に" }
              : oiUp
                ? { label: "新規ショート主導", color: C.red, note: "価格↓×OI↑ — 新規売りが下落を牽引。継続性が高い下落" }
                : { label: "ロング投げ", color: C.yellow, note: "価格↓×OI↓ — ロング解消の下落。売り一巡後の反発余地あり" };
            const takerSum = deriv.takerSeries.reduce((a, b) => a + b, 0);
            const quads = [
              { key: "LB", label: "新規ロング", active: pxUp && oiUp, color: C.green },
              { key: "SC", label: "ショートカバー", active: pxUp && !oiUp, color: C.yellow },
              { key: "SB", label: "新規ショート", active: !pxUp && oiUp, color: C.red },
              { key: "LL", label: "ロング投げ", active: !pxUp && !oiUp, color: C.yellow },
            ];
            return (
              <>
                <div className="lg:col-span-3 flex items-center gap-3 mt-2">
                  <div className="text-xs uppercase tracking-widest font-semibold" style={{ color: C.orangeBright, fontFamily: FONT_MONO }}>
                    Positioning — ポジショニング
                  </div>
                  <div className="flex-1 border-t" style={{ borderColor: C.borderSoft }} />
                  {derivMode === "live"
                    ? <Tag color={C.green}>実データ · Binance / Hyperliquid</Tag>
                    : <Tag color={C.yellow}>サンプル · Coinglass/Binance 連携予定</Tag>}
                </div>

                {/* Liquidation map */}
                <div className="lg:col-span-2">
                  <Panel
                    title={liqReal ? "清算マップ — Hyperliquid 実データ" : "清算マップ — 推定清算クラスター"}
                    right={
                      liqReal ? (
                        <Tag color={C.green}>実データ · カバー率 {liqReal.coveragePct ?? "—"}% · {liqReal.positionsTracked}件</Tag>
                      ) : (
                        <Tag color={C.orange}>±7.2% レンジ · サンプル</Tag>
                      )
                    }
                  >
                    <LiqMap levels={liqReal ? liqReal.levels : deriv.liqLevels} price={m.price} />
                    <div className="flex gap-5 mt-3 text-xs" style={{ color: C.muted }}>
                      <span><span style={{ color: C.green }}>■</span> 上方 = ショート清算 (上昇の燃料)</span>
                      <span><span style={{ color: C.red }}>■</span> 下方 = ロング清算 (下落の磁石)</span>
                    </div>
                    <div className="text-xs mt-2 leading-relaxed" style={{ color: C.muted }}>
                      大きなクラスターは価格を引き寄せやすく、到達後は反転しやすい。ストップ設置はクラスターの「向こう側」を意識。
                    </div>
                  </Panel>
                </div>

                {/* OI × Price quadrant */}
                <Panel title="OI × 価格 — 4象限判定">
                  <div className="grid grid-cols-2 gap-1.5 mb-3">
                    {quads.map((q) => (
                      <div
                        key={q.key}
                        className="rounded-lg px-2 py-2.5 text-center text-xs font-semibold"
                        style={{
                          background: q.active ? `${q.color}26` : C.panelSoft,
                          color: q.active ? q.color : C.faint,
                          border: `1px solid ${q.active ? q.color : C.borderSoft}`,
                        }}
                      >
                        {q.label}
                      </div>
                    ))}
                  </div>
                  <div className="text-sm font-bold" style={{ color: quad.color, fontFamily: FONT_MONO }}>{quad.label}</div>
                  <div className="text-xs mt-1.5 leading-relaxed" style={{ color: C.muted }}>{quad.note}</div>
                  <div className="mt-3 text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
                    OI 24h: <span style={{ color: oiUp ? C.green : C.red }}>{oiUp ? "+" : ""}{deriv.oiChangePct24h.toFixed(1)}%</span>
                    {"  ·  "}価格 24h: <span style={{ color: pxUp ? C.green : C.red }}>{pxUp ? "+" : ""}{m.chg24.toFixed(1)}%</span>
                  </div>
                </Panel>

                {/* Long/Short ratio */}
                <Panel title="Long/Short 比率">
                  <div className="mb-4">
                    <div className="flex justify-between text-xs mb-1" style={{ color: C.muted }}>
                      <span>個人口座 (全体)</span>
                      <span style={{ fontFamily: FONT_MONO, color: C.text }}>L {deriv.lsGlobalLongPct.toFixed(1)}%</span>
                    </div>
                    <div className="flex h-2.5 rounded-full overflow-hidden">
                      <div style={{ width: `${deriv.lsGlobalLongPct}%`, background: C.green }} />
                      <div style={{ width: `${100 - deriv.lsGlobalLongPct}%`, background: C.red }} />
                    </div>
                  </div>
                  <div>
                    <div className="flex justify-between text-xs mb-1" style={{ color: C.muted }}>
                      <span>トップトレーダー (建玉)</span>
                      <span style={{ fontFamily: FONT_MONO, color: C.text }}>L {deriv.lsTopLongPct.toFixed(1)}%</span>
                    </div>
                    <div className="flex h-2.5 rounded-full overflow-hidden">
                      <div style={{ width: `${deriv.lsTopLongPct}%`, background: C.green }} />
                      <div style={{ width: `${100 - deriv.lsTopLongPct}%`, background: C.red }} />
                    </div>
                  </div>
                  <div className="text-xs mt-3 leading-relaxed" style={{ color: C.muted }}>
                    {deriv.lsGlobalLongPct - deriv.lsTopLongPct > 10
                      ? "個人とトップ層の乖離が大きい — 個人の偏りは逆行の燃料になりやすい"
                      : "個人とトップ層の方向感は概ね一致"}
                  </div>
                </Panel>

                {/* Taker flow */}
                <Panel title="Taker Buy/Sell — 攻撃的注文フロー" right={<Tag color={takerSum >= 0 ? C.green : C.red}>{takerSum >= 0 ? "買い優勢" : "売り優勢"}</Tag>}>
                  <TakerBars series={deriv.takerSeries} />
                  <div className="text-xs mt-2 leading-relaxed" style={{ color: C.muted }}>
                    1h毎の成行(テイカー)デルタ。この累積値が下のCVDパネル — 単発の大きなバーは攻撃的な参入の痕跡。
                  </div>
                </Panel>

                {/* Funding comparison */}
                <Panel title="Funding 比較 — 取引所別 (APR)">
                  <div className="flex flex-col gap-2.5">
                    {deriv.fundingCompare.map((f) => (
                      <div key={f.ex} className="flex items-center gap-3">
                        <div className="w-24 text-xs" style={{ color: C.muted, fontFamily: FONT_MONO }}>{f.ex}</div>
                        <div className="flex-1 h-2 rounded-full" style={{ background: C.panelSoft }}>
                          <div className="h-2 rounded-full" style={{ width: `${Math.min(100, (Math.abs(f.apr) / 20) * 100)}%`, background: f.apr > 15 ? C.red : f.apr < 0 ? C.blue : C.green }} />
                        </div>
                        <div className="w-14 text-right text-sm font-bold" style={{ fontFamily: FONT_MONO, color: f.apr > 15 ? C.red : f.apr < 0 ? C.blue : C.green }}>
                          {f.apr.toFixed(1)}%
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="text-xs mt-3 leading-relaxed" style={{ color: C.muted }}>
                    取引所間の乖離はポジション偏りの偏在を示す。全所で高進 = ロング過密、マイナス転落 = ショート過密。
                  </div>
                </Panel>

                {/* CVD */}
                {(() => {
                  // バックエンドが cvd[24] を返せばそれを使用、無ければ takerSeries から累積 (JSXと同一)
                  let cvd;
                  if (Array.isArray(deriv.cvd) && deriv.cvd.length) {
                    cvd = deriv.cvd;
                  } else {
                    cvd = [];
                    let acc = 0;
                    for (const v of deriv.takerSeries) { acc += v; cvd.push(acc); }
                  }
                  const cvdEnd = cvd[cvd.length - 1];
                  const divg =
                    pxUp && cvdEnd < 0
                      ? { t: "弱気ダイバージェンス候補", c: C.red, n: "価格は上昇しているがCVDは売り越し — 買い上がりではなく売り吸収での上昇。反落警戒。" }
                      : !pxUp && cvdEnd > 0
                        ? { t: "強気ダイバージェンス候補", c: C.green, n: "価格は下落しているがCVDは買い越し — 売りが吸収されている。反発警戒。" }
                        : { t: "価格と一致", c: C.muted, n: "CVDと価格の方向が一致 — フロー面での逆行シグナルなし。" };
                  return (
                    <div className="lg:col-span-2">
                      <Panel
                        title="CVD — 累積出来高デルタ (24h)"
                        right={<Tag color={cvdEnd >= 0 ? C.green : C.red}>{cvdEnd >= 0 ? "+" : "−"}{fmtUsd(Math.abs(cvdEnd))}</Tag>}
                      >
                        <DetailLine
                          points={cvd}
                          color={cvdEnd >= 0 ? C.green : C.red}
                          fmtVal={(v) => `${v < 0 ? "−" : ""}${fmtUsd(Math.abs(v))}`}
                          hours={24}
                          baselineZero
                        />
                        <div className="mt-2 text-xs" style={{ fontFamily: FONT_MONO }}>
                          <span style={{ color: divg.c }}>◆ {divg.t}</span>
                        </div>
                        <div className="text-xs mt-1 leading-relaxed" style={{ color: C.muted }}>{divg.n}</div>
                      </Panel>
                    </div>
                  );
                })()}

                {/* OI history */}
                {(() => {
                  // バックエンドが oiHist[48] (絶対USD) を返せばそれを使用、無ければデモの倍率×現OI
                  const oiPts = (Array.isArray(deriv.oiHist) && deriv.oiHist.length)
                    ? deriv.oiHist
                    : deriv.oiHistMult.map((mu) => mu * m.oiUsd);
                  const oiStart = oiPts[0];
                  const oiNow = oiPts[oiPts.length - 1];
                  const oiChg48 = ((oiNow - oiStart) / oiStart) * 100;
                  return (
                    <Panel
                      title="Open Interest 推移 (48h)"
                      right={<Tag color={oiChg48 >= 0 ? C.green : C.red}>{oiChg48 >= 0 ? "+" : ""}{oiChg48.toFixed(1)}%</Tag>}
                    >
                      <DetailLine
                        points={oiPts}
                        color={oiChg48 >= 0 ? C.green : C.red}
                        fmtVal={(v) => fmtUsd(v)}
                        hours={48}
                      />
                      <div className="mt-2 text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>
                        現在 <span style={{ color: C.text }}>{fmtUsd(oiNow)}</span> · 48h前 <span style={{ color: C.text }}>{fmtUsd(oiStart)}</span>
                      </div>
                      <div className="text-xs mt-2 leading-relaxed" style={{ color: C.muted }}>
                        OIの増減は4象限判定の入力。減少局面はポジション解消(手仕舞い)主導の値動きを示唆。
                      </div>
                    </Panel>
                  );
                })()}

                {/* Smart Money — top traders */}
                {(topReal || topT) && (() => {
                  const T = topReal || topT;
                  const s = T.summary;
                  const total = s.longUsd + s.shortUsd;
                  const lPct = total ? (s.longUsd / total) * 100 : 50;
                  return (
                    <div className="lg:col-span-3">
                      <Panel
                        title="Smart Money — リーダーボード上位50人の目線 (30日PnL順)"
                        right={
                          topReal ? (
                            <Tag color={C.green}>実データ · 90秒毎更新</Tag>
                          ) : (
                            <Tag color={C.yellow}>サンプル</Tag>
                          )
                        }
                      >
                        {/* summary */}
                        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mb-2">
                          <div className="text-sm" style={{ fontFamily: FONT_MONO }}>
                            <span style={{ color: C.green }}>ロング {s.longCount}人</span>
                            <span style={{ color: C.faint }}> / </span>
                            <span style={{ color: C.red }}>ショート {s.shortCount}人</span>
                            <span style={{ color: C.faint }}> / ノーポジ {s.flatCount}人</span>
                          </div>
                          <div className="text-xs" style={{ color: C.muted, fontFamily: FONT_MONO }}>
                            建玉総額 <span style={{ color: C.text }}>{fmtUsd(total)}</span>
                          </div>
                        </div>
                        <div className="flex h-3 rounded-full overflow-hidden mb-1">
                          <div style={{ width: `${lPct}%`, background: C.green }} />
                          <div style={{ width: `${100 - lPct}%`, background: C.red }} />
                        </div>
                        <div className="flex justify-between text-xs mb-4" style={{ fontFamily: FONT_MONO }}>
                          <span style={{ color: C.green }}>L {lPct.toFixed(1)}% ({fmtUsd(s.longUsd)})</span>
                          <span style={{ color: C.red }}>S {(100 - lPct).toFixed(1)}% ({fmtUsd(s.shortUsd)})</span>
                        </div>

                        {/* table */}
                        <div className="overflow-x-auto">
                          <table className="w-full text-xs" style={{ fontFamily: FONT_MONO }}>
                            <thead>
                              <tr style={{ color: C.faint }}>
                                {["Rank", "Addr", "方向", "建玉", "建値", "清算価格", "含み損益", "Lev", "30日PnL"].map((h, i) => (
                                  <th key={h} className={`py-1.5 font-normal ${i >= 3 ? "text-right" : "text-left"}`}>{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {T.traders.map((t) => (
                                <tr key={t.rank + t.addr} style={{ borderTop: `1px solid ${C.borderSoft}`, color: C.text }}>
                                  <td className="py-2" style={{ color: C.faint }}>#{t.rank}</td>
                                  <td style={{ color: C.muted }}>{t.addr}</td>
                                  <td>
                                    <span style={{ color: t.side === "long" ? C.green : C.red, fontWeight: 700 }}>
                                      {t.side === "long" ? "LONG" : "SHORT"}
                                    </span>
                                  </td>
                                  <td className="text-right">{fmtUsd(t.posUsd)}</td>
                                  <td className="text-right" style={{ color: C.muted }}>{t.entryPx?.toLocaleString()}</td>
                                  <td className="text-right" style={{ color: C.muted }}>{t.liqPx ? t.liqPx.toLocaleString() : "—"}</td>
                                  <td className="text-right" style={{ color: t.upnl >= 0 ? C.green : C.red }}>
                                    {t.upnl >= 0 ? "+" : "−"}{fmtUsd(Math.abs(t.upnl))}
                                  </td>
                                  <td className="text-right" style={{ color: C.faint }}>{t.lev ? `${t.lev}x` : "—"}</td>
                                  <td className="text-right" style={{ color: C.green }}>+{fmtUsd(t.monthPnl)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <div className="text-xs mt-3 leading-relaxed" style={{ color: C.muted }}>
                          直近30日で最も稼いだ50人の現在の建玉。集団の偏りと個人口座L/Sとの乖離は有力な環境認識になるが、
                          他会場のヘッジや現物との組み合わせの可能性があるため、個別ポジションの単純コピーは不可。
                        </div>
                      </Panel>
                    </div>
                  );
                })()}
              </>
            );
          })()}

          {/* ===== Macro calendar section ===== */}
          {macro && (() => {
            const now = Date.now();
            const upcoming = macro.filter((e) => e.ts > now).sort((a, b) => a.ts - b.ts);
            const nextHigh = upcoming.find((e) => e.impact === "HIGH");
            const hrsTo = (ts) => (ts - now) / 3600e3;
            const cd = (ts) => {
              const h = hrsTo(ts);
              if (h < 1) return "まもなく";
              if (h < 24) return `あと${Math.round(h)}h`;
              return `あと${Math.floor(h / 24)}日${Math.round(h % 24)}h`;
            };
            const jst = (ts) =>
              new Date(ts).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
            const IMPACT = { HIGH: C.red, MED: C.yellow, LOW: C.blue };
            const CUR = { USD: C.blue, CRYPTO: C.orange, EUR: C.green, JPY: C.red };
            const highSoon = nextHigh && hrsTo(nextHigh.ts) <= 24;
            return (
              <>
                <div className="lg:col-span-3 flex items-center gap-3 mt-2">
                  <div className="text-xs uppercase tracking-widest font-semibold" style={{ color: C.orangeBright, fontFamily: FONT_MONO }}>
                    Macro — 経済カレンダー
                  </div>
                  <div className="flex-1 border-t" style={{ borderColor: C.borderSoft }} />
                  {macroMode === "live"
                    ? <Tag color={C.green}>実データ · Finnhub / Deribit</Tag>
                    : <Tag color={C.yellow}>サンプル · Finnhub/Deribit 連携予定</Tag>}
                </div>

                <div className="lg:col-span-3">
                  {/* carry-over warning */}
                  <div
                    className="rounded-xl px-4 py-3 mb-4 text-sm leading-relaxed"
                    style={{
                      background: highSoon ? `${C.red}14` : `${C.green}10`,
                      border: `1px solid ${highSoon ? C.redDim : C.greenDim}`,
                      color: highSoon ? C.red : C.green,
                    }}
                  >
                    {highSoon ? (
                      <>
                        <span className="font-bold">⚠ 持ち越し注意 — </span>
                        {cd(nextHigh.ts)} ({jst(nextHigh.ts)} JST) に
                        <span className="font-bold"> {nextHigh.cur}・高インパクト「{nextHigh.name}」</span>。
                        発表を跨ぐポジション保有はボラ急拡大リスク。サイズ縮小・ストップ再確認・または手仕舞いを検討。
                      </>
                    ) : (
                      <>
                        <span className="font-bold">✓ 直近24hに高インパクト指標なし — </span>
                        イベントリスクは低め。テクニカル主導の環境。
                        {nextHigh && <>次の高インパクトは {cd(nextHigh.ts)}「{nextHigh.name}」。</>}
                      </>
                    )}
                  </div>

                  {/* event list */}
                  <Panel title="今後のイベント (48h+)">
                    <div className="flex flex-col">
                      {upcoming.map((e, i) => (
                        <div
                          key={i}
                          className="flex flex-wrap items-start gap-x-4 gap-y-1 py-3"
                          style={{ borderTop: i > 0 ? `1px solid ${C.borderSoft}` : "none" }}
                        >
                          <div className="w-24">
                            <div className="text-sm font-bold" style={{ fontFamily: FONT_MONO, color: hrsTo(e.ts) <= 24 && e.impact === "HIGH" ? C.red : C.text }}>
                              {cd(e.ts)}
                            </div>
                            <div className="text-xs" style={{ color: C.faint, fontFamily: FONT_MONO }}>{jst(e.ts)}</div>
                          </div>
                          <div className="flex gap-1.5 pt-0.5">
                            <Tag color={CUR[e.cur] ?? C.muted}>{e.cur}</Tag>
                            <Tag color={IMPACT[e.impact]}>{e.impact}</Tag>
                          </div>
                          <div className="flex-1 min-w-48">
                            <div className="text-sm font-semibold" style={{ color: C.text }}>{e.name}</div>
                            <div className="text-xs mt-0.5 leading-relaxed" style={{ color: C.muted }}>
                              <span style={{ color: C.orangeBright }}>✦ </span>{e.note}
                            </div>
                          </div>
                          {(e.f || e.p) && (
                            <div className="text-xs pt-0.5" style={{ fontFamily: FONT_MONO, color: C.faint }}>
                              予想 <span style={{ color: C.text }}>{e.f ?? "—"}</span> · 前回 <span style={{ color: C.text }}>{e.p ?? "—"}</span>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </Panel>
                </div>
              </>
            );
          })()}
        </div>
      )}

      {loading && !m && (
        <div className="max-w-6xl mx-auto mt-16 text-center text-sm" style={{ color: C.muted, fontFamily: FONT_MONO }}>
          Hyperliquid からデータを取得中…
        </div>
      )}

      <div className="max-w-6xl mx-auto mt-8 pb-4 text-xs leading-relaxed" style={{ color: C.faint }}>
        データ: Hyperliquid public API (1h足 / mark price / funding / OI) · Alternative.me Fear & Greed · AI概況: Claude API。
        本画面は環境認識ツールであり、投資助言ではありません。判断と執行は常にトレーダー自身が行ってください。
      </div>
    </div>
  );
}
