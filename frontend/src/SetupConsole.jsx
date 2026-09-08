import { useState, useEffect, useCallback } from "react";
import { C, FONT_MONO, FONT_UI, jget, jpost, Tag, Panel } from "./App.jsx";

// ============================================================
// セットアップコンソール (handoff/SETUP_CONSOLE_SPEC.md)
// トレードを実行しない。判断もしない。
//   1. 人間が決めたルールで算術を検算し、違反を赤く表示する
//   2. POI到達時に吸収コンファームを機械判定し、「置いてある指値を残してよいか／引くべきか」だけを出す
//   3. 到達・判定・結果を台帳に記録する
// 設計思想は「拒否権」。緑のランプは「入れ」ではなく「消さなくてよい」。緑を「入れ」の意味で使わない。
// ============================================================

// 判定色 (SPEC §10)。ダッシュボードのセマンティック色とは別体系
const K = {
  fire: "#2DD4BF",     // 成立 = 青緑
  reject: C.orange,    // (c)棄却 = 橙
  accept: "#A78BFA",   // 受け入れ警告 = 紫
  violation: C.red,    // 違反 = 赤
  silent: C.faint,     // 沈黙 = 灰
};
const VERDICT_COLOR = { fire: K.fire, rejected_c: K.reject, silent: K.silent };
const VERDICT_LABEL = { fire: "成立", rejected_c: "(c)棄却", silent: "沈黙" };
const FLOW_STATES = ["IDLE", "PLACED", "AT_POI", "CONFIRMED", "FILLED_UNCONFIRMED", "MANAGE", "EXIT_PLAN", "CLOSED"];
const FLOW_SIDE = ["PULLED_EVENT", "PULLED_WEEKEND", "REJECTED"];

const fmt = (x, nd = 1) => (x == null || isNaN(x) ? "—" : Number(x).toLocaleString("ja-JP", { minimumFractionDigits: nd, maximumFractionDigits: nd }));
const signed = (x, nd = 0) => (x == null ? "—" : (x > 0 ? "+" : "") + fmt(x, nd));

const Small = ({ children, color = C.muted }) => (
  <span className="text-xs" style={{ color, fontFamily: FONT_MONO }}>{children}</span>
);

const Field = ({ label, children }) => (
  <label className="flex flex-col gap-1 text-xs" style={{ color: C.muted }}>
    <span>{label}</span>
    {children}
  </label>
);

const inputStyle = { background: C.bg, color: C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 8px", fontFamily: FONT_MONO, fontSize: 13 };
const Input = (props) => <input {...props} style={{ ...inputStyle, ...(props.style || {}) }} />;
const Select = ({ children, ...props }) => <select {...props} style={{ ...inputStyle, ...(props.style || {}) }}>{children}</select>;
const Btn = ({ children, color = C.orangeBright, dim = C.orangeDim, ...props }) => (
  <button {...props} className="px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-40"
    style={{ background: `${color}1e`, color, border: `1px solid ${dim}`, ...(props.style || {}) }}>
    {children}
  </button>
);

// 台帳テーブルのセル (折り返さない・余白)
const Td = ({ children, center, style = {}, ...rest }) => (
  <td className={`py-1 px-1.5 whitespace-nowrap ${center ? "text-center" : ""}`} style={style} {...rest}>{children}</td>
);
const Th = ({ children, left }) => <th className={`px-1.5 whitespace-nowrap ${left ? "text-left" : ""}`}>{children}</th>;

const Pending = ({ fields }) =>
  fields?.length ? <span className="text-xs font-bold" style={{ color: K.violation }}>{fields.join("/")} 要記入</span> : null;

// ---------------------------------------------------------------- ① 台帳＆帯
const LedgerPane = ({ s, refresh }) => {
  const [edit, setEdit] = useState(null);   // {type:'level'|'band', obj}
  const [busy, setBusy] = useState(false);
  const price = s?.data?.price;
  const save = async () => {
    if (!edit) return;
    setBusy(true);
    const path = edit.type === "level" ? "/api/setup/ledger/level" : "/api/setup/ledger/band";
    const body = { ...edit.obj };
    ["price", "lo", "hi", "ref_level", "sl_price"].forEach((k) => { if (k in body) body[k] = body[k] === "" || body[k] == null ? null : Number(body[k]); });
    if (edit.confirmed) body.needs_confirm = [];
    const r = await jpost(path, body);
    setBusy(false);
    if (r?.ok) { setEdit(null); refresh(); }
  };
  const distText = (d) => d?.usd == null ? "—" : `${signed(d.usd)} / ${d.atr == null ? "—" : signed(d.atr, 1)}ATR`;
  return (
    <Panel title="① 台帳 ＆ 帯" right={<Small>現在値 {fmt(price, 0)}</Small>}>
      <div className="text-xs mb-2" style={{ color: C.faint }}>帯 (暫定・編集可)。要記入/要確認は赤字、人間が埋めるまで inactive</div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ fontFamily: FONT_MONO }}>
          <thead><tr style={{ color: C.faint }}><Th left>帯</Th><Th>側</Th><Th>lo–hi</Th><Th>ref (c)</Th><Th>SL</Th><Th>有効</Th><Th>距離</Th><Th></Th></tr></thead>
          <tbody>
            {(s?.ledger?.bands || []).map((b) => (
              <tr key={b.id} style={{ borderTop: `1px solid ${C.borderSoft}`, color: b.active ? C.text : C.faint }}>
                <Td style={{ fontWeight: 700 }}>{b.id}</Td>
                <Td center>{b.side}</Td>
                <Td center>{fmt(b.lo, 0)}–{fmt(b.hi, 0)}</Td>
                <Td center>{b.ref_level == null ? <span style={{ color: K.violation }}>要記入</span> : fmt(b.ref_level, 1)}</Td>
                <Td center>{b.sl_price == null ? <span style={{ color: K.violation }}>要記入</span> : fmt(b.sl_price, 0)}</Td>
                <Td center>{b.active ? b.valid_from : <span style={{ color: b.pending.length ? K.violation : C.yellow }}>{b.pending.length ? "inactive" : b.valid_from ? "未誕生" : "構成未確定"}</span>}</Td>
                <Td center>{distText(b.distance)}</Td>
                <Td><button className="underline" style={{ color: C.muted }} onClick={() => setEdit({ type: "band", obj: { ...b } })}>編集</button></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs mt-3 mb-1" style={{ color: C.faint }}>水準 (2026-08-17 以降、75,000〜83,000、日足フレーム)</div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ fontFamily: FONT_MONO }}>
          <thead><tr style={{ color: C.faint }}><Th left>id</Th><Th>価格</Th><Th left>名称</Th><Th>層</Th><Th>born</Th><Th>valid_from</Th><Th>距離</Th><Th></Th></tr></thead>
          <tbody>
            {(s?.ledger?.levels || []).map((l) => (
              <tr key={l.id} style={{ borderTop: `1px solid ${C.borderSoft}`, color: l.active ? C.text : C.faint }}>
                <Td style={{ fontWeight: 700 }}>{l.id}</Td>
                <Td center>{fmt(l.price, 1)}</Td>
                <Td>{l.name} {l.status !== "active" && <Tag color={C.faint}>{l.status}</Tag>}</Td>
                <Td center>{l.tier}</Td>
                <Td center>{l.born_on ?? <span style={{ color: K.violation }}>要記入</span>}</Td>
                <Td center>
                  {l.valid_from ?? <span style={{ color: K.violation }}>要記入</span>}
                  {l.needs_confirm?.includes("valid_from") && <span style={{ color: K.violation }}> 要確認</span>}
                </Td>
                <Td center>{distText(l.distance)}</Td>
                <Td><button className="underline" style={{ color: C.muted }} onClick={() => setEdit({ type: "level", obj: { ...l } })}>編集</button></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {edit && (
        <div className="mt-3 p-3 rounded-lg" style={{ background: C.panelSoft, border: `1px solid ${C.border}` }}>
          <div className="text-xs mb-2" style={{ color: C.muted }}>{edit.type === "level" ? "水準" : "帯"} {edit.obj.id} を編集 <Pending fields={edit.obj.pending} /></div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {edit.type === "level" ? (
              <>
                <Field label="price"><Input value={edit.obj.price ?? ""} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, price: e.target.value } })} /></Field>
                <Field label="born_on (YYYY-MM-DD)"><Input value={edit.obj.born_on ?? ""} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, born_on: e.target.value || null } })} /></Field>
                <Field label={`valid_from${edit.obj.suggested_valid_from ? ` (提案 ${edit.obj.suggested_valid_from})` : ""}`}><Input value={edit.obj.valid_from ?? ""} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, valid_from: e.target.value || null } })} /></Field>
                <Field label="status"><Select value={edit.obj.status} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, status: e.target.value } })}>{["active", "broken", "retired"].map((x) => <option key={x}>{x}</option>)}</Select></Field>
              </>
            ) : (
              <>
                <Field label="lo"><Input value={edit.obj.lo} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, lo: e.target.value } })} /></Field>
                <Field label="hi"><Input value={edit.obj.hi} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, hi: e.target.value } })} /></Field>
                <Field label="ref_level (c の参照水準)"><Input value={edit.obj.ref_level ?? ""} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, ref_level: e.target.value } })} /></Field>
                <Field label="sl_price"><Input value={edit.obj.sl_price ?? ""} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, sl_price: e.target.value } })} /></Field>
                <Field label="sl_basis"><Input value={edit.obj.sl_basis ?? ""} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, sl_basis: e.target.value } })} /></Field>
                <Field label="構成水準 (カンマ区切り)"><Input value={(edit.obj.component_level_ids || []).join(",")} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, component_level_ids: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })} /></Field>
              </>
            )}
            <Field label="note"><Input value={edit.obj.note ?? ""} onChange={(e) => setEdit({ ...edit, obj: { ...edit.obj, note: e.target.value } })} /></Field>
            {edit.obj.needs_confirm?.length > 0 && (
              <label className="flex items-center gap-2 text-xs" style={{ color: C.text }}>
                <input type="checkbox" checked={!!edit.confirmed} onChange={(e) => setEdit({ ...edit, confirmed: e.target.checked })} />
                要確認 ({edit.obj.needs_confirm.join("/")}) を確認済みにする
              </label>
            )}
          </div>
          <div className="flex gap-2 mt-3">
            <Btn onClick={save} disabled={busy}>{busy ? "保存中…" : "保存"}</Btn>
            <Btn color={C.muted} dim={C.borderSoft} onClick={() => setEdit(null)}>取消</Btn>
          </div>
        </div>
      )}
    </Panel>
  );
};

// ---------------------------------------------------------------- ② 診断表
const DiagPane = ({ s, refresh }) => {
  const [form, setForm] = useState(null);
  const [showProv, setShowProv] = useState(false);
  useEffect(() => { if (s?.inputs && !form) setForm({ ...s.inputs }); }, [s, form]);
  const apply = async () => {
    const r = await jpost("/api/setup/inputs", { ...form, ref_s: Number(form.ref_s) || 0, ref_l: Number(form.ref_l) || 0, zone_lo: Number(form.zone_lo) || 0, zone_hi: Number(form.zone_hi) || 0 });
    if (r?.ok) refresh();
  };
  const d = showProv ? s?.provisional : s?.latest;
  const v = s?.latest;
  const vc = v ? VERDICT_COLOR[v.verdict] : K.silent;
  return (
    <Panel title="② 診断表 (Pine v0.3.3 同構成)" right={<Small color={C.faint}>校正値 {s?.version ?? "—"}</Small>}>
      {form && (
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-3">
          <Field label="mode"><Select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}><option value="S">S</option><option value="L">L</option><option value="both">both</option></Select></Field>
          <Field label="ref_s (0=無効)"><Input value={form.ref_s} onChange={(e) => setForm({ ...form, ref_s: e.target.value })} /></Field>
          <Field label="ref_l (0=無効)"><Input value={form.ref_l} onChange={(e) => setForm({ ...form, ref_l: e.target.value })} /></Field>
          <Field label="zone_lo"><Input value={form.zone_lo} onChange={(e) => setForm({ ...form, zone_lo: e.target.value })} /></Field>
          <Field label="zone_hi"><Input value={form.zone_hi} onChange={(e) => setForm({ ...form, zone_hi: e.target.value })} /></Field>
          <div className="flex items-end gap-2">
            <label className="text-xs flex items-center gap-1" style={{ color: C.muted }}><input type="checkbox" checked={!!form.use_zone} onChange={(e) => setForm({ ...form, use_zone: e.target.checked })} />zone</label>
            <Btn onClick={apply}>適用</Btn>
          </div>
        </div>
      )}
      {v ? (
        <div className="rounded-lg px-3 py-2 mb-3" style={{ background: `${vc}14`, border: `1px solid ${vc}55` }}>
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <Tag color={vc}>{VERDICT_LABEL[v.verdict]}{v.verdict_side ? ` (${v.verdict_side}側)` : ""}</Tag>
              <span className="ml-2 text-sm font-semibold" style={{ color: vc, fontFamily: FONT_UI }}>{v.verdict_text}</span>
            </div>
            <Small>確定 {v.close_local} (足 {v.open_local}) · 15m ATR {fmt(v.atr, 1)}</Small>
          </div>
          {v.accept_event && <div className="mt-1 text-sm font-semibold" style={{ color: K.accept }}>{v.accept_text}</div>}
        </div>
      ) : (
        <div className="text-sm mb-3" style={{ color: C.muted }}>{s?.data?.waiting ? "データ待ち (Binance BTCUSDT 15m)…" : "確定足が揃うまで判定なし"}</div>
      )}
      <div className="flex items-center gap-3 mb-1">
        <Small color={C.faint}>{showProv ? "暫定 (形成中の足。判定は出さない)" : "最新確定足"}</Small>
        {s?.provisional && <button className="text-xs underline" style={{ color: C.muted }} onClick={() => setShowProv(!showProv)}>{showProv ? "確定足を見る" : "暫定を見る"}</button>}
      </div>
      {d && (
        <table className="w-full text-xs" style={{ fontFamily: FONT_MONO }}>
          <thead><tr style={{ color: C.faint }}><th className="text-left py-1">行</th><th className="text-right">S側</th><th className="text-right">L側</th></tr></thead>
          <tbody>
            {d.rows.map((r) => (
              <tr key={r.row} style={{ borderTop: `1px solid ${C.borderSoft}`, opacity: showProv ? 0.7 : 1 }}>
                <td className="py-1" style={{ color: C.muted }}>{r.row}{showProv && r.row === "判定" ? " (暫定・無効)" : ""}</td>
                <td className="text-right" style={{ color: r.row === "判定" ? VERDICT_COLOR[d.S.verdict] : C.text }}>{showProv && r.row === "判定" ? "—" : r.S}</td>
                <td className="text-right" style={{ color: r.row === "判定" ? VERDICT_COLOR[d.L.verdict] : C.text }}>{showProv && r.row === "判定" ? "—" : r.L}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-3 text-xs leading-relaxed" style={{ color: C.faint }}>
        <div className="font-semibold mb-0.5" style={{ color: C.muted }}>既知の限界</div>
        {(s?.limits || []).map((t) => <div key={t}>· {t}</div>)}
      </div>
    </Panel>
  );
};

// ---------------------------------------------------------------- ③ 算術
const emptyLeg = () => ({ price: "", ratio: "1", lot: "" });
const ArithPane = ({ s, onResult }) => {
  const [f, setF] = useState({ balance_usd: "10000", risk_pct: "2", side: "S", legs: [emptyLeg()], sl: "", tp: "", atr_1h: "", atr_15m: "", liq_upper: "", liq_lower: "", band_lo: "", band_hi: "", atr_source: "today", band_id: "" });
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    setF((p) => ({ ...p, atr_1h: p.atr_1h || (s?.atr_1h ? s.atr_1h.toFixed(1) : ""), atr_15m: p.atr_15m || (s?.atr15 ? s.atr15.toFixed(1) : "") }));
  }, [s]);
  const num = (x) => (x === "" || x == null ? null : Number(x));
  const pickBand = (id) => {
    const b = (s?.ledger?.bands || []).find((x) => x.id === id);
    if (!b) { setF({ ...f, band_id: id }); return; }
    setF({ ...f, band_id: id, side: b.side, band_lo: String(b.lo), band_hi: String(b.hi), sl: b.sl_price != null ? String(b.sl_price) : f.sl });
  };
  const run = async () => {
    setErr(null);
    const body = {
      balance_usd: num(f.balance_usd), risk_pct: num(f.risk_pct), side: f.side,
      entries: f.legs.filter((l) => l.price !== "").map((l) => ({ price: Number(l.price), ratio: Number(l.ratio) || 1, lot: num(l.lot) })),
      sl: num(f.sl), tp: num(f.tp), atr_1h: num(f.atr_1h), atr_15m: num(f.atr_15m),
      liq_upper: num(f.liq_upper), liq_lower: num(f.liq_lower), band_lo: num(f.band_lo), band_hi: num(f.band_hi), atr_source: f.atr_source,
    };
    const r = await jpost("/api/setup/arith", body);
    if (!r) { setErr("検算できない (入力不足かバックエンド不達)"); setRes(null); onResult(null); return; }
    setRes(r);
    onResult({ ok: !r.placement_blocked, band_id: f.band_id, side: f.side, sl: num(f.sl), avg_entry: r.avg_entry });
  };
  const wk = s?.weekend_or_holiday;
  return (
    <Panel title="③ 算術 ＋ 違反フラグ" right={res && <Tag color={res.placement_blocked ? K.violation : K.fire}>{res.placement_blocked ? "敷設不可" : "違反なし"}</Tag>}>
      {wk && (
        <div className="text-xs mb-2 px-2 py-1 rounded" style={{ background: `${K.violation}14`, color: K.violation }}>
          土日・休場日: 直近フル流動性セッションのATRを使用 (当日ATRは拒否)。参考: {s?.last_session_bar_local} 時点の 1H ATR {fmt(s?.atr_1h_last_session, 1)}
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Field label="帯 (選ぶと側/lo-hi/SL を転記)"><Select value={f.band_id} onChange={(e) => pickBand(e.target.value)}><option value="">—</option>{(s?.ledger?.bands || []).map((b) => <option key={b.id} value={b.id}>{b.id}</option>)}</Select></Field>
        <Field label="残高 USD"><Input value={f.balance_usd} onChange={(e) => setF({ ...f, balance_usd: e.target.value })} /></Field>
        <Field label="リスク % (既定2、勝ち後1)"><Input value={f.risk_pct} onChange={(e) => setF({ ...f, risk_pct: e.target.value })} /></Field>
        <Field label="方向"><Select value={f.side} onChange={(e) => setF({ ...f, side: e.target.value })}><option value="S">S</option><option value="L">L</option></Select></Field>
        <Field label="SL"><Input value={f.sl} onChange={(e) => setF({ ...f, sl: e.target.value })} /></Field>
        <Field label="TP"><Input value={f.tp} onChange={(e) => setF({ ...f, tp: e.target.value })} /></Field>
        <Field label="1H ATR (SL設計用)"><Input value={f.atr_1h} onChange={(e) => setF({ ...f, atr_1h: e.target.value })} /></Field>
        <Field label="ATR の出所"><Select value={f.atr_source} onChange={(e) => setF({ ...f, atr_source: e.target.value })}><option value="today">当日</option><option value="last_full_session">直近フル流動性セッション</option></Select></Field>
        <Field label="15m ATR (分散判定用)"><Input value={f.atr_15m} onChange={(e) => setF({ ...f, atr_15m: e.target.value })} /></Field>
        <Field label="清算帯 上側"><Input value={f.liq_upper} onChange={(e) => setF({ ...f, liq_upper: e.target.value })} /></Field>
        <Field label="清算帯 下側"><Input value={f.liq_lower} onChange={(e) => setF({ ...f, liq_lower: e.target.value })} /></Field>
        <Field label="帯 lo / hi"><div className="flex gap-1"><Input value={f.band_lo} onChange={(e) => setF({ ...f, band_lo: e.target.value })} style={{ width: "50%" }} /><Input value={f.band_hi} onChange={(e) => setF({ ...f, band_hi: e.target.value })} style={{ width: "50%" }} /></div></Field>
      </div>
      <div className="mt-2 text-xs" style={{ color: C.muted }}>エントリー (単発 or 2〜3分割: 価格 / 比率 / ロット任意)</div>
      {f.legs.map((l, i) => (
        <div key={i} className="flex gap-1 mt-1">
          <Input placeholder="価格" value={l.price} onChange={(e) => setF({ ...f, legs: f.legs.map((x, j) => (j === i ? { ...x, price: e.target.value } : x)) })} style={{ width: "40%" }} />
          <Input placeholder="比率" value={l.ratio} onChange={(e) => setF({ ...f, legs: f.legs.map((x, j) => (j === i ? { ...x, ratio: e.target.value } : x)) })} style={{ width: "25%" }} />
          <Input placeholder="ロット (任意)" value={l.lot} onChange={(e) => setF({ ...f, legs: f.legs.map((x, j) => (j === i ? { ...x, lot: e.target.value } : x)) })} style={{ width: "35%" }} />
        </div>
      ))}
      <div className="flex gap-2 mt-2">
        <Btn color={C.muted} dim={C.borderSoft} onClick={() => f.legs.length < 3 && setF({ ...f, legs: [...f.legs, emptyLeg()] })} disabled={f.legs.length >= 3}>＋枠</Btn>
        <Btn color={C.muted} dim={C.borderSoft} onClick={() => f.legs.length > 1 && setF({ ...f, legs: f.legs.slice(0, -1) })} disabled={f.legs.length <= 1}>－枠</Btn>
        <Btn onClick={run}>検算</Btn>
      </div>
      {err && <div className="text-xs mt-2" style={{ color: K.violation }}>{err}</div>}
      {res && (
        <div className="mt-3">
          <div className="text-xs mb-1" style={{ color: C.muted, fontFamily: FONT_MONO }}>
            平均エントリー {fmt(res.avg_entry, 1)} · SL幅 {fmt(res.sl_width, 1)} · 最大ロット {res.max_lot.toFixed(4)} BTC · 枠ロット {res.lots.map((x) => x.toFixed(4)).join(" / ")}
          </div>
          <table className="w-full text-xs" style={{ fontFamily: FONT_MONO }}>
            <tbody>
              {res.rows.map((r) => (
                <tr key={r.key} style={{ borderTop: `1px solid ${C.borderSoft}`, color: r.violation ? K.violation : C.text }}>
                  <td className="py-1 w-28" style={{ color: r.violation ? K.violation : C.muted }}>{r.violation ? "● " : ""}{r.label}</td>
                  <td className="py-1 font-bold">{r.value}</td>
                  <td className="py-1" style={{ color: r.violation ? K.violation : C.faint }}>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-xs mt-1" style={{ color: C.faint }}>設計値は丸めない・補正しない。赤は人間が直す。</div>
        </div>
      )}
    </Panel>
  );
};

// ---------------------------------------------------------------- ④ フロー
const FlowPane = ({ s, arith, refresh }) => {
  const fl = s?.flow;
  const [note, setNote] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const advance = async (to) => {
    setErr(null); setBusy(true);
    const body = { to, note };
    if (fl.state === "IDLE" && to === "PLACED") {
      Object.assign(body, { arith_ok: !!arith?.ok, band_id: arith?.band_id || null, side: arith?.side || null, sl: arith?.sl ?? null, avg_entry: arith?.avg_entry ?? null });
    }
    const r = await jpost("/api/setup/flow/advance", body);
    setBusy(false);
    if (!r) { setErr("遷移できない (ガード条件を満たしていないかバックエンド不達)"); return; }
    setNote(""); refresh();
  };
  if (!fl) return <Panel title="④ フロー状態" />;
  const cur = fl.state;
  const stCol = (st) => (st === cur ? (["REJECTED", "PULLED_EVENT", "PULLED_WEEKEND"].includes(st) ? K.reject : st === "EXIT_PLAN" ? K.accept : K.fire) : C.faint);
  return (
    <Panel title="④ フロー状態 ＋ いまやること" right={<Small>{fl.since_local ? `since ${fl.since_local}` : ""}</Small>}>
      <div className="flex flex-wrap gap-1 mb-2">
        {FLOW_STATES.map((st) => <Tag key={st} color={stCol(st)}>{st}</Tag>)}
      </div>
      <div className="flex flex-wrap gap-1 mb-3">
        {FLOW_SIDE.map((st) => <Tag key={st} color={stCol(st)}>{st}</Tag>)}
      </div>
      <div className="rounded-lg px-3 py-2 mb-3" style={{ background: C.panelSoft, border: `1px solid ${C.border}` }}>
        <div className="text-xs" style={{ color: C.faint }}>いま人間がやること</div>
        <div className="text-sm font-semibold" style={{ color: C.text, fontFamily: FONT_UI }}>{fl.todo}</div>
        {fl.band_id && <div className="text-xs mt-1" style={{ color: C.muted, fontFamily: FONT_MONO }}>帯 {fl.band_id} · {fl.side} · SL <span style={{ color: C.text }}>{fmt(fl.sl, 0)}</span> (固定・変更不可) · 建値 {fmt(fl.avg_entry, 1)}</div>}
        {fl.state === "FILLED_UNCONFIRMED" && <div className="text-xs mt-1" style={{ color: C.muted }}>約定後の確定窓 {fl.windows_since_fill} / 4 (4窓で成立も棄却もなければ時間スクラッチ)</div>}
        {fl.exit_plan && (
          <div className="text-xs mt-1" style={{ color: K.accept, fontFamily: FONT_MONO }}>
            撤退指値 建値±0.25×ATR: {fmt(fl.exit_plan.price_lower, 1)} / {fmt(fl.exit_plan.price_upper, 1)} · 1H タイマー終了 {fl.exit_plan.timer_end_local}
          </div>
        )}
        {fl.suggested && <div className="text-xs mt-1 font-semibold" style={{ color: fl.suggested.to === "CONFIRMED" || fl.suggested.to === "MANAGE" ? K.fire : K.reject }}>判定からの提案: → {fl.suggested.to} ({fl.suggested.reason})。人間がチェックして進める</div>}
      </div>
      {cur === "IDLE" && (
        <div className="text-xs mb-2" style={{ color: arith?.ok && s?.gate?.placement_allowed ? C.muted : K.violation }}>
          敷設条件: 算術 {arith ? (arith.ok ? "OK" : "赤あり") : "未検算"} · カレンダーゲート {s?.gate?.placement_allowed ? "開" : "閉"} · 帯 {arith?.band_id || "未選択"}
        </div>
      )}
      <div className="flex flex-wrap gap-2 items-end">
        <Field label="メモ"><Input value={note} onChange={(e) => setNote(e.target.value)} style={{ width: 220 }} /></Field>
        {fl.allowed.map((a) => (
          <Btn key={a.to} onClick={() => advance(a.to)} disabled={busy} title={a.label}
            color={a.to === "REJECTED" || a.to.startsWith("PULLED") ? K.reject : a.to === "IDLE" ? C.muted : C.orangeBright}
            dim={a.to === "IDLE" ? C.borderSoft : C.orangeDim}>
            ☐ {a.to}
          </Btn>
        ))}
      </div>
      {err && <div className="text-xs mt-2" style={{ color: K.violation }}>{err}</div>}
      <div className="text-xs mt-2" style={{ color: C.faint }}>各遷移は人間のチェック操作で進む。自動は「価格が帯に到達」「窓のカウント」「タイマー」のみ。</div>
    </Panel>
  );
};

// ---------------------------------------------------------------- 下段
const GateStrip = ({ s }) => {
  const g = s?.gate;
  if (!g) return null;
  const col = { no_placement: K.violation, no_trade: K.violation, no_trade_notice: C.yellow, thin: C.yellow, minor: C.faint };
  return (
    <Panel title="カレンダーゲート (今日〜72h · Europe/Paris)" right={<Tag color={g.placement_allowed ? K.fire : K.violation}>{g.placement_allowed ? "敷設可" : "敷設不可"}</Tag>}>
      <div className="flex flex-wrap gap-2 mb-2">
        {g.badges.length ? g.badges.map((b, i) => <Tag key={i} color={col[b.kind]}>{b.text}{b.until_local ? ` (〜${b.until_local})` : ""}</Tag>) : <Small>バッジなし</Small>}
      </div>
      <div className="flex flex-col gap-1">
        {g.windows.map((w, i) => (
          <div key={i} className="flex gap-3 text-xs" style={{ fontFamily: FONT_MONO, color: col[w.kind] || C.text }}>
            <span className="w-52">{w.start_local}{w.kind !== "minor" ? ` → ${w.end_local}` : ""}</span>
            <span>{w.kind === "no_placement" ? "敷設不可" : w.kind === "no_trade" ? "ノートレード" : "minor"}</span>
            <span style={{ color: C.text }}>{w.name}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
};

const HistoryStrip = ({ s }) => {
  const h = (s?.history || []).slice(-48).reverse();
  return (
    <Panel title="判定履歴 (確定足ごと · 沈黙も保存)">
      <div className="flex flex-wrap gap-1 mb-2">
        {[...h].reverse().map((x) => (
          <span key={x.open_utc_ms} title={`${x.close_local} ${VERDICT_LABEL[x.verdict]}${x.accept_event ? " / 受け入れ" : ""}`}
            style={{ width: 10, height: 10, borderRadius: 2, background: x.accept_event ? K.accept : VERDICT_COLOR[x.verdict], display: "inline-block" }} />
        ))}
      </div>
      <div className="max-h-48 overflow-y-auto">
        {h.map((x) => (
          <div key={x.open_utc_ms} className="flex gap-3 text-xs py-0.5" style={{ fontFamily: FONT_MONO, borderTop: `1px solid ${C.borderSoft}` }}>
            <span style={{ color: C.faint }}>{x.close_local}</span>
            <span style={{ color: VERDICT_COLOR[x.verdict], width: 60 }}>{VERDICT_LABEL[x.verdict]}{x.verdict_side ? `(${x.verdict_side})` : ""}</span>
            <span style={{ color: C.muted }}>{x.verdict !== "silent" ? x.verdict_text : ""}</span>
            {x.accept_event && <span style={{ color: K.accept }}>{x.accept_text}</span>}
          </div>
        ))}
      </div>
    </Panel>
  );
};

const TouchStrip = ({ s, refresh }) => {
  const t = (s?.ledger?.touches || []).slice(-20).reverse();
  const upd = async (id, k, v) => { const r = await jpost("/api/setup/ledger/touch", { id, [k]: v || null }); if (r?.ok) refresh(); };
  return (
    <Panel title="到達記録 (台帳 Touch · 結果は人間が記入)">
      {!t.length && <Small>到達なし</Small>}
      {t.map((x) => (
        <div key={x.id} className="flex flex-wrap gap-2 items-center text-xs py-1" style={{ fontFamily: FONT_MONO, borderTop: `1px solid ${C.borderSoft}` }}>
          <span style={{ color: C.faint }}>{x.id}</span>
          <span>{x.ts_local}</span>
          <span className="font-bold">{x.target_id}</span>
          <span>{x.side}</span>
          <Tag color={x.verdict === "confirmed" ? K.fire : x.verdict === "rejected_c" ? K.reject : K.silent}>{x.verdict}</Tag>
          {x.first_fire_ts && <span style={{ color: K.fire }}>初点灯 {x.first_fire_ts}</span>}
          {x.accept_warning_ts && <span style={{ color: K.accept }}>受け入れ {x.accept_warning_ts}</span>}
          <span style={{ color: C.faint }}>{x.bar_count}本{x.end_ts_utc ? "" : " (継続中)"}</span>
          <Select value={x.outcome_4h || ""} onChange={(e) => upd(x.id, "outcome_4h", e.target.value)}><option value="">4h後 —</option><option value="progress">progress</option><option value="flat">flat</option><option value="adverse">adverse</option></Select>
          <Select value={x.outcome_structure || ""} onChange={(e) => upd(x.id, "outcome_structure", e.target.value)}><option value="">構造 —</option><option value="held">held</option><option value="broken">broken</option></Select>
        </div>
      ))}
    </Panel>
  );
};


// ---------------------------------------------------------------- リプレイ (§9)
const ReplayPane = () => {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [floor, setFloor] = useState(true);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const run = async () => {
    setErr(null); setBusy(true);
    const r = await jpost("/api/setup/replay", { from_local: from, to_local: to, floor_enabled: floor }, 60000);
    setBusy(false);
    if (!r) { setErr("リプレイできない (期間の形式 YYYY-MM-DD HH:MM、0<期間≤60日、Binance 到達性を確認)"); return; }
    setRes(r);
  };
  const vc = { confirmed: K.fire, rejected_c: K.reject, silent: K.silent };
  return (
    <Panel title="リプレイ (台帳データ収集 · 先読み防止を強制)" right={res && <Small>{res.bars} 本 / {res.rows.length} 到達</Small>}>
      <div className="flex flex-wrap gap-2 items-end">
        <Field label="開始 (Europe/Paris)"><Input placeholder="2026-08-17 00:00" value={from} onChange={(e) => setFrom(e.target.value)} style={{ width: 170 }} /></Field>
        <Field label="終了"><Input placeholder="2026-09-07 00:00" value={to} onChange={(e) => setTo(e.target.value)} style={{ width: 170 }} /></Field>
        <label className="text-xs flex items-center gap-1 pb-2" style={{ color: C.muted }}><input type="checkbox" checked={floor} onChange={(e) => setFloor(e.target.checked)} />出来高床</label>
        <Btn onClick={run} disabled={busy || !from || !to}>{busy ? "実行中…" : "リプレイ"}</Btn>
      </div>
      {err && <div className="text-xs mt-2" style={{ color: K.violation }}>{err}</div>}
      {res && (
        <>
          <div className="mt-3 max-h-64 overflow-auto">
            <table className="text-xs" style={{ fontFamily: FONT_MONO }}>
              <thead><tr style={{ color: C.faint }}><Th left>id</Th><Th>到達</Th><Th>対象</Th><Th>側</Th><Th>判定</Th><Th>初点灯</Th><Th>受け入れ</Th><Th>本数</Th><Th left>+16本 終値</Th></tr></thead>
              <tbody>
                {res.rows.map((r) => (
                  <tr key={r.id} style={{ borderTop: `1px solid ${C.borderSoft}` }}>
                    <Td style={{ color: C.faint }}>{r.id}</Td><Td center>{r.ts_local}</Td><Td center style={{ fontWeight: 700 }}>{r.target_id}</Td><Td center>{r.side}</Td>
                    <Td center style={{ color: vc[r.verdict] }}>{r.verdict}</Td><Td center style={{ color: K.fire }}>{r.first_fire_ts ?? "—"}</Td>
                    <Td center style={{ color: K.accept }}>{r.accept_warning_ts ?? "—"}</Td><Td center>{r.bar_count}</Td>
                    <Td style={{ color: C.muted }}>{r.path_16_close.map((x) => fmt(x, 0)).join(" ")}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-xs mt-2" style={{ color: C.faint }}>CSV (Google Sheet「POI水準台帳 v3」へ手動貼り付け。列順は Touch モデル順 — C表の列順は未回答)</div>
          <textarea readOnly value={res.csv} rows={5} className="w-full mt-1" style={{ ...inputStyle, fontSize: 11 }} onFocus={(e) => e.target.select()} />
        </>
      )}
    </Panel>
  );
};

// ---------------------------------------------------------------- 画面
export default function SetupConsole() {
  const [s, setS] = useState(null);
  const [down, setDown] = useState(false);
  const [arith, setArith] = useState(null);
  const refresh = useCallback(async () => {
    const j = await jget("/api/setup/state", 4000);
    if (j) { setS(j); setDown(false); } else setDown(true);
  }, []);
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <div className="min-h-screen px-4 py-6" style={{ background: C.bg, color: C.text, fontFamily: FONT_UI }}>
      <div className="max-w-6xl mx-auto flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: FONT_MONO }}>セットアップコンソール</h1>
          <div className="text-sm" style={{ color: C.muted }}>BTCUSDT (Binance USDⓈ-M 先物で計測) · 表示は Europe/Paris · トレードは実行しない</div>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <Tag color={C.orange}>校正値 {s?.version ?? "—"}</Tag>
          {down ? <Tag color={K.violation}>バックエンド不達</Tag>
            : s?.data?.waiting ? <Tag color={C.yellow}>データ待ち</Tag>
            : s?.data?.stale ? <Tag color={C.yellow}>STALE</Tag> : <Tag color={K.fire}>● LIVE</Tag>}
          <Small color={C.faint}>{s?.now_local ?? ""}</Small>
          <a href="#/" className="px-3 py-1.5 rounded-lg text-sm" style={{ color: C.muted, border: `1px solid ${C.borderSoft}`, textDecoration: "none" }}>⇠ ダッシュボード</a>
        </div>
      </div>
      <div className="max-w-6xl mx-auto mt-3 text-xs px-3 py-2 rounded-lg" style={{ background: `${C.orange}12`, color: C.orangeBright, border: `1px solid ${C.orangeDim}` }}>
        拒否権の道具。青緑は「入れ」ではなく「置いてある指値を消さなくてよい」。橙は「引く」。紫は「整理準備」。赤は「敷設不可」。最終判断は常に人間。
      </div>
      {s?.data?.error && <div className="max-w-6xl mx-auto mt-2 text-xs" style={{ color: K.violation }}>データ取得エラー: {s.data.error}</div>}

      <div className="max-w-6xl mx-auto mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <LedgerPane s={s} refresh={refresh} />
        <DiagPane s={s} refresh={refresh} />
        <ArithPane s={s} onResult={setArith} />
        <FlowPane s={s} arith={arith} refresh={refresh} />
      </div>
      <div className="max-w-6xl mx-auto mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
        <GateStrip s={s} />
        <HistoryStrip s={s} />
      </div>
      <div className="max-w-6xl mx-auto mt-4">
        <TouchStrip s={s} refresh={refresh} />
      </div>
      <div className="max-w-6xl mx-auto mt-4">
        <ReplayPane />
      </div>
      <div className="max-w-6xl mx-auto mt-6 pb-4 text-xs leading-relaxed" style={{ color: C.faint, fontFamily: FONT_MONO }}>
        校正値 {s?.version ?? "—"} (config/confirm_{s?.version ?? "…"}.json · 上書き禁止) · δ方式 {s?.config?.delta_method ?? "—"} · 窓 {s?.config?.window_bars ?? "—"}本 · x_ratio {s?.config?.x_ratio ?? "—"} · atr_coef {s?.config?.atr_coef ?? "—"} · 床 ×{s?.config?.vol_floor_mult ?? "—"} / median {s?.config?.vol_median_len ?? "—"} · 受け入れ {s?.config?.accept_consecutive ?? "—"}本
        <br />本画面は検算・判定表示・記録の道具であり、投資助言ではありません。発注機能はありません。判断と執行は常にトレーダー自身が行ってください。
      </div>
    </div>
  );
}
