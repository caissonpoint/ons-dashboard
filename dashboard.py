#!/usr/bin/env python3
"""Generate a single self-contained HTML dashboard from the daily ONS store.

The payload is embedded gzipped + base64 and inflated in the browser with the
native DecompressionStream API, which keeps the file a few MB rather than tens.
"""

from __future__ import annotations

import base64
import datetime as dt
import gzip
import json
from pathlib import Path

import pandas as pd

SUBSYSTEM_ORDER = ["SIN", "SE", "S", "NE", "N"]
SUBSYSTEM_LABELS = {
    "SIN": "SIN (national)",
    "SE": "Southeast/Midwest",
    "S": "South",
    "NE": "Northeast",
    "N": "North",
}

# metric -> (label, unit, picker group, sums across subsystems, entity kind)
SERIES_META: dict[str, tuple[str, str, str, bool, str]] = {
    "load":                 ("Load",                 "MWmed", "Balance", True, ""),
    "production_total":     ("Production total",     "MWmed", "Balance", True, ""),
    "gen_hydro":            ("Hydro generation",     "MWmed", "Balance", True, ""),
    "gen_thermal":          ("Thermal generation",   "MWmed", "Balance", True, ""),
    "gen_wind":             ("Wind generation",      "MWmed", "Balance", True, ""),
    "gen_solar":            ("Solar generation",     "MWmed", "Balance", True, ""),
    "net_interchange":      ("Net interchange",      "MWmed", "Balance", True, ""),
    "thermal_gas":          ("Thermal \u2014 natural gas", "MWmed", "Thermal by fuel", True, ""),
    "thermal_coal":         ("Thermal \u2014 coal",        "MWmed", "Thermal by fuel", True, ""),
    "thermal_oil":          ("Thermal \u2014 oil/diesel",  "MWmed", "Thermal by fuel", True, ""),
    "thermal_nuclear":      ("Thermal \u2014 nuclear",     "MWmed", "Thermal by fuel", True, ""),
    "thermal_biomass":      ("Thermal \u2014 biomass",     "MWmed", "Thermal by fuel", True, ""),
    "thermal_other":        ("Thermal \u2014 other",       "MWmed", "Thermal by fuel", True, ""),
    "ena_gross_mwmes":      ("ENA gross",            "MWm\u00eas", "Hydrology", True, ""),
    "ena_storable_mwmes":   ("ENA storable",         "MWm\u00eas", "Hydrology", True, ""),
    "ear_mwmes":            ("EAR stored",           "MWm\u00eas", "Hydrology", True, ""),
    "ear_max_mwmes":        ("EAR capacity",         "MWm\u00eas", "Hydrology", True, ""),
    "ena_pct_mlt":          ("ENA gross, % MLT",     "%", "Hydrology", False, ""),
    "ena_storable_pct_mlt": ("ENA storable, % MLT",  "%", "Hydrology", False, ""),
    "ear_pct":              ("EAR, % of capacity",   "%", "Hydrology", False, ""),
    "cmo":                  ("CMO",                  "R$/MWh", "Prices", False, ""),
    # per-plant (bulletin sheet 09)
    "plant_verif":          ("Verified",             "MWmed", "Thermal plant", False, "plant"),
    "plant_prog":           ("Programmed",           "MWmed", "Thermal plant", False, "plant"),
    "plant_desvio_pct":     ("Deviation",            "%",     "Thermal plant", False, "plant"),
    # per-reservoir (bulletin sheets 23-26)
    "res_volutil_pct":      ("Usable volume",        "%",     "Reservoir", False, "reservoir"),
    "res_level_m":          ("Upstream level",       "m",     "Reservoir", False, "reservoir"),
}

UNIT_PANELS = [
    ("MWmed", "MWmed"),
    ("MWm\u00eas", "MWm\u00eas"),
    ("%", "Percent"),
    ("m", "Level \u2014 metres"),
    ("R$/MWh", "R$/MWh"),
]

PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
                "#d55181", "#008300", "#9085e9", "#e66767"]


def add_sin(df: pd.DataFrame) -> pd.DataFrame:
    """Derive national (SIN) rows for subsystem-level series only."""
    sub = df[df["entity"] == ""]
    wide = sub.pivot_table(index=["date", "subsystem"], columns="series",
                           values="value", aggfunc="mean")
    summables = [s for s, m in SERIES_META.items()
                 if m[3] and not m[4] and s in wide.columns]
    sin = wide[summables].groupby(level="date").sum(min_count=1)

    if {"ear_mwmes", "ear_max_mwmes"} <= set(sin.columns):
        sin["ear_pct"] = 100 * sin["ear_mwmes"] / sin["ear_max_mwmes"]

    for val_col, pct_col in [("ena_gross_mwmes", "ena_pct_mlt"),
                             ("ena_storable_mwmes", "ena_storable_pct_mlt")]:
        if {val_col, pct_col} <= set(wide.columns):
            mlt = 100 * wide[val_col] / wide[pct_col].replace(0, pd.NA)
            sin[pct_col] = 100 * sin[val_col] / mlt.groupby(level="date").sum(min_count=1)

    if "cmo" in wide.columns:
        sin["cmo"] = wide["cmo"].groupby(level="date").mean()

    sin = sin.reset_index().melt(id_vars="date", var_name="series", value_name="value")
    sin["subsystem"], sin["entity"] = "SIN", ""
    sin = sin.dropna(subset=["value"])
    return pd.concat([df, sin[["date", "subsystem", "entity", "series", "value"]]],
                     ignore_index=True)


def pick_defaults(df: pd.DataFrame, ent: pd.DataFrame) -> dict:
    """Opening selections: the biggest gas plants, one reservoir per subsystem.

    Rows carry subsystem as well as name -- a plant or reservoir is identified by
    the pair, never by the name alone.
    """
    out = {"plant": [], "reservoir": []}

    plants = ent[ent["kind"] == "plant"]
    gas = plants[plants["group"].str.lower().str.contains("gás|gas", regex=True,
                                                          na=False)]
    pool = gas if len(gas) else plants
    if len(pool):
        recent = df[(df["series"] == "plant_verif")
                    & (df["date"] >= df["date"].max() - pd.Timedelta(days=90))]
        recent = recent.merge(pool[["entity", "subsystem"]],
                              on=["entity", "subsystem"])
        rank = (recent.groupby(["subsystem", "entity"])["value"].mean()
                .sort_values(ascending=False).head(4).reset_index())
        out["plant"] = rank[["entity", "subsystem"]].to_dict("records")

    res = df[df["series"] == "res_volutil_pct"]
    if len(res):
        counts = res.groupby(["subsystem", "entity"]).size().reset_index(name="n")
        top = (counts.sort_values("n", ascending=False)
               .groupby("subsystem").head(1).head(4))
        out["reservoir"] = top[["entity", "subsystem"]].to_dict("records")
    return out


def build_payload(df: pd.DataFrame, ent: pd.DataFrame) -> dict:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["entity"] = df["entity"].fillna("").astype(str)
    df = add_sin(df)

    dates = sorted(df["date"].unique())
    idx = {d: i for i, d in enumerate(dates)}
    n = len(dates)

    series: dict[str, list] = {}
    for (s, sub, e), grp in df.groupby(["series", "subsystem", "entity"],
                                       observed=True):
        if s not in SERIES_META:
            continue
        dec = 2 if SERIES_META[s][1] in ("%", "R$/MWh", "m") else 1
        vals: list[float | None] = [None] * n
        for d, v in zip(grp["date"], grp["value"]):
            vals[idx[d]] = round(float(v), dec)
        series[f"{s}|{sub}|{e}"] = vals

    ents = ent.fillna("").to_dict("records") if len(ent) else []
    return {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates],
        "series": series,
        "entities": ents,
        "defaults": pick_defaults(df, ent) if len(ent) else {"plant": [],
                                                             "reservoir": []},
        "seriesMeta": {k: {"label": v[0], "unit": v[1], "group": v[2], "kind": v[4]}
                       for k, v in SERIES_META.items()},
        "seriesOrder": list(SERIES_META),
        "subsystems": SUBSYSTEM_ORDER,
        "subsystemLabels": SUBSYSTEM_LABELS,
        "unitPanels": [{"unit": u, "title": t} for u, t in UNIT_PANELS],
        "paletteLight": PALETTE_LIGHT,
        "paletteDark": PALETTE_DARK,
    }


def write_dashboard(df: pd.DataFrame, dest: Path,
                    ent: pd.DataFrame | None = None) -> Path:
    if ent is None:
        ent = pd.DataFrame(columns=["kind", "entity", "subsystem", "group"])
    payload = build_payload(df, ent)
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    packed = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")
    html = TEMPLATE.replace("__PAYLOAD__", packed)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    print(f"  payload {len(raw)/1e6:.1f} MB JSON -> {len(packed)/1e6:.1f} MB embedded")
    return dest


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ONS Balances</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%232a78d6'/%3E%3Cpath d='M3 11.5 6 7l3 2.5L13 4' stroke='white' stroke-width='1.6' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
:root{
  color-scheme: light;
  --surface-1:#fcfcfb; --plane:#f4f4f1; --text-1:#0b0b0b; --text-2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --ring:rgba(11,11,11,.10);
  --accent:#2a78d6; --wash:rgba(42,120,214,.08);
}
@media (prefers-color-scheme: dark){
 :root:not([data-theme="light"]){
  color-scheme: dark;
  --surface-1:#1a1a19; --plane:#0d0d0d; --text-1:#fff; --text-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --accent:#3987e5; --wash:rgba(57,135,229,.14);
 }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface-1:#1a1a19; --plane:#0d0d0d; --text-1:#fff; --text-2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --axis:#383835; --ring:rgba(255,255,255,.10);
  --accent:#3987e5; --wash:rgba(57,135,229,.14);
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--text-1);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;}
.wrap{max-width:1440px;margin:0 auto;padding:20px 20px 64px}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;
  justify-content:space-between;margin-bottom:14px}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
.sub{color:var(--text-2);font-size:13px}
.card{background:var(--surface-1);border:1px solid var(--ring);border-radius:10px;
  padding:14px 16px;margin-bottom:14px}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--ring);margin-bottom:14px}
.tabs button{border:0;border-bottom:2px solid transparent;background:none;
  border-radius:0;padding:9px 14px;color:var(--text-2);font-weight:600;font-size:13.5px}
.tabs button[aria-pressed=true]{color:var(--text-1);border-bottom-color:var(--accent);
  background:none}
.tabs button:hover{background:var(--wash)}
.controls{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-end}
.ctl{display:flex;flex-direction:column;gap:6px}
.ctl > label{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);font-weight:600}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
button,select,input{font:inherit;color:var(--text-1);background:var(--surface-1);
  border:1px solid var(--axis);border-radius:6px;padding:5px 10px}
button,select{cursor:pointer}
button:hover,select:hover{background:var(--wash)}
button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}
.pickers{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.pick h3{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
  color:var(--muted);margin:0 0 6px;font-weight:600}
.opt{display:flex;align-items:center;gap:8px;padding:2px 0;cursor:pointer;
  font-size:13px;line-height:1.35}
.opt input{accent-color:var(--accent);margin:0;flex:none}
.opt.disabled{opacity:.4;cursor:not-allowed}
.opt .grp{color:var(--muted);font-size:11.5px;margin-left:auto;padding-left:10px;
  white-space:nowrap}
.sw{width:9px;height:9px;border-radius:2px;flex:none;background:transparent;
  border:1px solid transparent}
.entlist{max-height:460px;overflow:auto;border:1px solid var(--ring);
  border-radius:8px;padding:0;margin-top:8px}
.entlist table.data{font-size:12px}
.entlist table.data th,.entlist table.data td{padding:5px 8px}
.entlist th.l,.entlist td.l{text-align:left}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.tile{background:var(--surface-1);border:1px solid var(--ring);border-radius:10px;
  padding:11px 13px}
.tile .nm{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--text-2);
  margin-bottom:4px}
.tile .big{font-size:22px;font-weight:600;letter-spacing:-.02em}
.tile .meta{font-size:11.5px;color:var(--muted);margin-top:3px;
  font-variant-numeric:tabular-nums}
.panel-title{font-size:13px;font-weight:600;margin:0 0 2px}
.panel-note{font-size:11.5px;color:var(--muted);margin:0 0 8px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:8px;font-size:12px;
  color:var(--text-2)}
.legend span{display:flex;align-items:center;gap:6px}
svg{display:block;width:100%;overflow:hidden}
.tt{position:fixed;pointer-events:none;background:var(--surface-1);
  border:1px solid var(--ring);border-radius:8px;padding:8px 10px;font-size:12px;
  box-shadow:0 6px 20px rgba(0,0,0,.16);z-index:50;display:none;min-width:180px}
.tt .d{font-weight:600;margin-bottom:5px}
.tt table{border-collapse:collapse;width:100%}
.tt td{padding:1px 0}
.tt td.v{text-align:right;padding-left:14px;font-variant-numeric:tabular-nums}
table.data{border-collapse:collapse;width:100%;font-size:12.5px;
  font-variant-numeric:tabular-nums}
table.data th,table.data td{padding:5px 9px;border-bottom:1px solid var(--grid);
  text-align:right;white-space:nowrap}
table.data th:first-child,table.data td:first-child{text-align:left}
table.data thead th{position:sticky;top:0;background:var(--surface-1);
  color:var(--text-2);font-weight:600}
.scroll{overflow-x:auto;max-height:460px;overflow-y:auto}
.empty{color:var(--muted);padding:26px 0;text-align:center}
.foot{color:var(--muted);font-size:11.5px;margin-top:22px;line-height:1.7}
.foot a{color:var(--accent)}
#boot{padding:60px 0;text-align:center;color:var(--muted)}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>ONS Balances</h1>
    <div class="sub" id="subtitle">Loading&hellip;</div>
  </div>
  <div class="row">
    <button id="themeBtn" title="Toggle light/dark">Theme</button>
    <button id="csvBtn">Download CSV</button>
  </div>
</header>

<div id="boot">Unpacking data&hellip;</div>
<div id="app" hidden>

<div class="tabs" id="tabs"></div>

<div class="card">
  <div class="controls">
    <div class="ctl"><label>Date range</label><div class="row" id="presets"></div></div>
    <div class="ctl"><label>From</label><input type="date" id="from"></div>
    <div class="ctl"><label>To</label><input type="date" id="to"></div>
    <div class="ctl"><label>Smoothing</label><div class="row" id="smooth"></div></div>
    <div class="ctl" id="subsCtl"><label>Subsystems</label><div class="row" id="subs"></div></div>
    <div class="ctl"><label>View</label>
      <div class="row"><button id="tableBtn" aria-pressed="false">Table</button></div>
    </div>
  </div>
</div>

<div class="card" id="pickCard"></div>

<div class="tiles" id="tiles" style="margin-bottom:14px"></div>
<div id="charts"></div>
<div class="card" id="tableCard" hidden>
  <div class="scroll"><table class="data" id="dataTable"></table></div>
</div>
</div>

<div class="foot" id="foot"></div>
</div>

<div class="tt" id="tt"></div>

<script type="application/octet-stream" id="payload">__PAYLOAD__</script>
<script>
let DATA = null;
const PALETTE_SIZE = 8;   // colour palette length -- selection itself is unlimited
const CHART_MAX = 40;     // beyond this many picks, charts/tiles defer to the table
const fmtNum = (v, d=0) => v==null||!isFinite(v) ? "–"
  : v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});

/* ---------- views ---------------------------------------------------------- */
const VIEWS = [
  {id:"subsystems", label:"Subsystems", kind:""},
  {id:"plants",     label:"Thermal plants", kind:"plant"},
  {id:"reservoirs", label:"Reservoirs", kind:"reservoir"},
];

const state = {
  view: "subsystems",
  from: null, to: null, smooth: 7,
  subs: new Set(["SIN"]),
  table: false,
  slots: {subsystems:new Map(), plants:new Map(), reservoirs:new Map()},
  picked: {subsystems:[], plants:[], reservoirs:[]},
  metrics: {plants:new Set(["plant_verif"]), reservoirs:new Set(["res_volutil_pct"])},
  ents:    {plants:[], reservoirs:[]},      // selected entity names
  filter:  {plants:{region:"", group:"", q:""}, reservoirs:{region:"", group:"", q:""}},
};
const view = () => VIEWS.find(v=>v.id===state.view);
const picked = () => state.picked[state.view];

/* ---------- colour slots (sticky: a series keeps its hue while selected) ---- */
function slotMap(v){ return state.slots[v || state.view]; }
function claimSlot(key, v){
  const M=slotMap(v);
  if (M.has(key)) return M.get(key);
  const slot = M.size % PALETTE_SIZE;
  M.set(key, slot);
  return slot;
}
const releaseSlot = (k, v) => slotMap(v).delete(k);
function isDark(){
  const t=document.documentElement.dataset.theme;
  return t==="dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches);
}
const colorOf = (k, v) =>
  (isDark()?DATA.paletteDark:DATA.paletteLight)[claimSlot(k, v)];

/* ---------- series keys: "<metric>|<subsystem>|<entity>" -------------------- */
const skey = (m,s,e="") => m+"|"+s+"|"+e;
const metricOf = k => k.split("|")[0];
const unitOf = k => DATA.seriesMeta[metricOf(k)].unit;
let AMBIG = null;
function ambiguous(name){
  if(AMBIG===null){
    const seen={}; AMBIG=new Set();
    (DATA.entities||[]).forEach(e=>{
      if(seen[e.entity]!==undefined && seen[e.entity]!==e.subsystem) AMBIG.add(e.entity);
      seen[e.entity]=e.subsystem;
    });
  }
  return AMBIG.has(name);
}
function labelOf(k){
  const [m,s,e]=k.split("|");
  if(!e) return DATA.seriesMeta[m].label + " · " + s;
  return DATA.seriesMeta[m].label + " · " + e + (ambiguous(e) ? " ("+s+")" : "");
}
function exists(k){
  const [m,s,e]=k.split("|");
  if(m==="plant_desvio_pct")
    return DATA.series[skey("plant_verif",s,e)]!==undefined
        && DATA.series[skey("plant_prog",s,e)]!==undefined;
  return DATA.series[k]!==undefined;
}

/* ---------- windowing + smoothing ------------------------------------------ */
function bounds(){
  const D=DATA.dates;
  let i0=D.indexOf(state.from), i1=D.indexOf(state.to);
  if(i0<0){ i0=D.findIndex(d=>d>=state.from); if(i0<0) i0=0; }
  if(i1<0){ i1=D.length-1; for(let i=D.length-1;i>=0;i--) if(D[i]<=state.to){i1=i;break;} }
  return [Math.min(i0,i1), Math.max(i0,i1)];
}
function smoothed(arr){
  const w=state.smooth;
  if(w<=1) return arr;
  const out=new Array(arr.length).fill(null), need=Math.ceil(w*0.6);
  let sum=0,n=0;
  for(let i=0;i<arr.length;i++){
    const v=arr[i]; if(v!=null){sum+=v;n++;}
    const drop=i-w; if(drop>=0 && arr[drop]!=null){sum-=arr[drop];n--;}
    if(n>=need) out[i]=sum/n;
  }
  return out;
}
const memo = new Map();
function fullSeries(key){
  const tag=key+"@"+state.smooth;
  if(memo.has(tag)) return memo.get(tag);
  const [m,s,e]=key.split("|");
  let out;
  if(m==="plant_desvio_pct"){
    const v=smoothed(DATA.series[skey("plant_verif",s,e)]||[]);
    const p=smoothed(DATA.series[skey("plant_prog",s,e)]||[]);
    out=v.map((x,i)=>{
      const y=p[i];
      return (x==null||y==null||Math.abs(y)<1e-6)?null:100*(x-y)/y;
    });
  } else {
    out=smoothed(DATA.series[key]||[]);
  }
  memo.set(tag,out); return out;
}
function seriesValues(key){ const [i0,i1]=bounds(); return fullSeries(key).slice(i0,i1+1); }
function windowDates(){ const [i0,i1]=bounds(); return DATA.dates.slice(i0,i1+1); }

/* ---------- shared controls ------------------------------------------------ */
function el(tag, cls, txt){
  const e=document.createElement(tag);
  if(cls) e.className=cls;
  if(txt!=null) e.textContent=txt;
  return e;
}
function buildTabs(){
  const host=document.getElementById("tabs"); host.innerHTML="";
  VIEWS.forEach(v=>{
    const b=el("button",null,v.label);
    b.setAttribute("aria-pressed",String(state.view===v.id));
    b.onclick=()=>{ state.view=v.id; buildTabs(); buildSubs(); updateSubsVisibility();
      buildPickCard(); render(); };
    host.appendChild(b);
  });
}
function buildPresets(){
  const host=document.getElementById("presets"); host.innerHTML="";
  const last=DATA.dates[DATA.dates.length-1];
  const back=n=>{const d=new Date(last+"T00:00:00Z");d.setUTCDate(d.getUTCDate()-n);
    return d.toISOString().slice(0,10);};
  [["30D",back(29)],["90D",back(89)],["6M",back(181)],["YTD",last.slice(0,4)+"-01-01"],
   ["1Y",back(364)],["3Y",back(1094)],["Max",DATA.dates[0]]].forEach(([nm,from])=>{
    const b=el("button",null,nm);
    b.onclick=()=>{ state.from = from<DATA.dates[0]?DATA.dates[0]:from;
      state.to=last; syncInputs(); render(); };
    host.appendChild(b);
  });
}
function buildSmooth(){
  const host=document.getElementById("smooth"); host.innerHTML="";
  [["Daily",1],["7d avg",7],["30d avg",30]].forEach(([nm,w])=>{
    const b=el("button",null,nm);
    b.setAttribute("aria-pressed",String(state.smooth===w));
    b.onclick=()=>{ state.smooth=w; buildSmooth(); render(); };
    host.appendChild(b);
  });
}
function buildSubs(){
  const host=document.getElementById("subs"); host.innerHTML="";
  DATA.subsystems.forEach(s=>{
    if(view().kind && s==="SIN") return;          // plants/reservoirs have no SIN
    const b=el("button",null,s); b.title=DATA.subsystemLabels[s];
    b.setAttribute("aria-pressed",String(state.subs.has(s)));
    b.onclick=()=>{
      if(state.subs.has(s)) state.subs.delete(s); else state.subs.add(s);
      if(state.subs.size===0) state.subs.add(s);
      dropOutOfScope(); buildSubs(); buildPickCard(); render();
    };
    host.appendChild(b);
  });
}
function updateSubsVisibility(){
  const host=document.getElementById("subsCtl");
  if(host) host.style.display = view().kind ? "none" : "";
}
function dropOutOfScope(){
  // the Subsystems row now only scopes the "subsystems" balance view -- plants
  // and reservoirs are scoped by their own Region dropdown instead.
  state.picked.subsystems = state.picked.subsystems.filter(k=>{
    const keep=state.subs.has(k.split("|")[1]);
    if(!keep) releaseSlot(k, "subsystems");
    return keep;
  });
}
function syncInputs(){
  document.getElementById("from").value=state.from;
  document.getElementById("to").value=state.to;
}

/* ---------- pick card: metric grid (subsystems) or entity list ------------- */
function toggleKey(k, on){
  const list=picked(), i=list.indexOf(k);
  if(on && i<0){ claimSlot(k); list.push(k); }
  else if(!on && i>=0){ list.splice(i,1); releaseSlot(k); }
}
function buildPickCard(){
  const card=document.getElementById("pickCard"); card.innerHTML="";
  if(view().kind) buildEntityPicker(card); else buildMetricPicker(card);
  const foot=el("div","row"); foot.style.marginTop="12px";
  const clear=el("button",null,"Clear all");
  clear.onclick=()=>{ picked().forEach(k=>releaseSlot(k)); state.picked[state.view]=[];
    buildPickCard(); render(); };
  const cnt=el("span","sub");
  cnt.id="count";
  foot.append(clear,cnt); card.appendChild(foot);
}

function buildMetricPicker(card){
  const grid=el("div","pickers"); card.appendChild(grid);
  const groups={};
  DATA.seriesOrder.filter(m=>!DATA.seriesMeta[m].kind).forEach(m=>{
    const g=DATA.seriesMeta[m].group; (groups[g]=groups[g]||[]).push(m);
  });
  Object.entries(groups).forEach(([g,metrics])=>{
    const box=el("div","pick"); box.appendChild(el("h3",null,g));
    metrics.forEach(m=>{
      const keys=[...state.subs].map(s=>skey(m,s)).filter(exists);
      if(!keys.length) return;
      const on=keys.every(k=>picked().includes(k));
      const lab=el("label","opt");
      const cb=document.createElement("input"); cb.type="checkbox"; cb.checked=on;
      cb.onchange=()=>{ keys.forEach(k=>toggleKey(k,cb.checked));
        buildPickCard(); render(); };
      const sw=el("span","sw");
      const first=keys.find(k=>picked().includes(k));
      if(first){ sw.style.background=colorOf(first); sw.style.borderColor=colorOf(first); }
      lab.append(cb,sw,document.createTextNode(DATA.seriesMeta[m].label));
      box.appendChild(lab);
    });
    if(box.children.length>1) grid.appendChild(box);
  });
}

function entityRows(){
  const kind=view().kind;
  const f=state.filter[state.view];
  const chosen=new Set(picked().map(k=>{const a=k.split("|");return a[1]+"|"+a[2];}));
  return DATA.entities.filter(e=>e.kind===kind)
    .filter(e=>!f.region || e.subsystem===f.region)
    .filter(e=>!f.group || e.group===f.group)
    .filter(e=>!f.q || e.entity.toLowerCase().includes(f.q.toLowerCase()))
    .sort((a,b)=>{
      const sa=chosen.has(a.subsystem+"|"+a.entity)?0:1;
      const sb=chosen.has(b.subsystem+"|"+b.entity)?0:1;
      return sa-sb || a.entity.localeCompare(b.entity);
    });
}
function buildEntityPicker(card){
  const kind=view().kind;
  const f=state.filter[state.view];
  const metrics=DATA.seriesOrder.filter(m=>DATA.seriesMeta[m].kind===kind);

  const bar=el("div","controls");
  const mBox=el("div","ctl");
  mBox.appendChild(el("label",null,"Metrics"));
  const mRow=el("div","row");
  metrics.forEach(m=>{
    const b=el("button",null,DATA.seriesMeta[m].label);
    b.setAttribute("aria-pressed",String(state.metrics[state.view].has(m)));
    b.onclick=()=>{
      const set=state.metrics[state.view];
      if(set.has(m)) set.delete(m); else set.add(m);
      if(!set.size) set.add(m);
      syncEntitySelection(); buildPickCard(); render();
    };
    mRow.appendChild(b);
  });
  mBox.appendChild(mRow); bar.appendChild(mBox);

  const regions=DATA.subsystems.filter(s=>s!=="SIN");
  const rBox=el("div","ctl");
  rBox.appendChild(el("label",null,"Region"));
  const rsel=document.createElement("select");
  rsel.appendChild(new Option("All regions",""));
  regions.forEach(s=>rsel.appendChild(new Option(DATA.subsystemLabels[s], s)));
  rsel.value=f.region;
  rsel.onchange=()=>{ f.region=rsel.value; renderEntityList(); renderCount(); };
  rBox.appendChild(rsel); bar.appendChild(rBox);

  const groups=[...new Set(DATA.entities.filter(e=>e.kind===kind)
    .map(e=>e.group).filter(Boolean))].sort();
  if(groups.length){
    const gBox=el("div","ctl");
    gBox.appendChild(el("label",null,kind==="plant"?"Fuel":"Basin"));
    const sel=document.createElement("select");
    sel.appendChild(new Option(kind==="plant"?"All fuels":"All basins",""));
    groups.forEach(g=>sel.appendChild(new Option(g,g)));
    sel.value=f.group;
    sel.onchange=()=>{ f.group=sel.value; renderEntityList(); renderCount(); };
    gBox.appendChild(sel); bar.appendChild(gBox);
  }

  const qBox=el("div","ctl");
  qBox.appendChild(el("label",null,"Search"));
  const q=document.createElement("input");
  q.type="search"; q.placeholder=kind==="plant"?"plant name":"reservoir name";
  q.value=f.q;
  q.oninput=()=>{ f.q=q.value; renderEntityList(); renderCount(); };
  qBox.appendChild(q); bar.appendChild(qBox);
  card.appendChild(bar);

  const filtered = f.region || f.group || f.q;
  const selRow=el("div","row"); selRow.style.margin="10px 0 2px";
  const allBtn=el("button",null,"Select all"+(filtered?" (filtered)":""));
  allBtn.onclick=()=>{ setFilteredSelection(true); };
  const noneBtn=el("button",null,"Deselect all"+(filtered?" (filtered)":""));
  noneBtn.onclick=()=>{ setFilteredSelection(false); };
  selRow.append(allBtn,noneBtn); card.appendChild(selRow);

  const list=el("div","entlist"); list.id="entlist"; card.appendChild(list);
  renderEntityList();
}
function setFilteredSelection(on){
  const mset=[...state.metrics[state.view]];
  entityRows().forEach(e=>{
    const keys=mset.map(m=>skey(m,e.subsystem,e.entity)).filter(exists);
    keys.forEach(k=>toggleKey(k,on));
  });
  buildPickCard(); render();
}
function renderEntityList(){
  const host=document.getElementById("entlist"); if(!host) return;
  host.innerHTML="";
  const rows=entityRows();
  if(!rows.length){ host.appendChild(el("div","empty","No matches.")); return; }
  const kind=view().kind;
  const mset=[...state.metrics[state.view]];
  const table=document.createElement("table"); table.className="data";
  const thead=document.createElement("thead");
  const htr=document.createElement("tr");
  htr.appendChild(el("th"));
  ["Name","Region",kind==="plant"?"Fuel":"Basin"].forEach(t=>htr.appendChild(el("th","l",t)));
  mset.forEach(m=>htr.appendChild(el("th",null,DATA.seriesMeta[m].label)));
  thead.appendChild(htr); table.appendChild(thead);
  const tbody=document.createElement("tbody");
  rows.forEach(e=>{
    const keys=mset.map(m=>skey(m,e.subsystem,e.entity)).filter(exists);
    if(!keys.length) return;
    const on=keys.every(k=>picked().includes(k));
    const tr=document.createElement("tr");
    const tdCb=el("td"); const cb=document.createElement("input");
    cb.type="checkbox"; cb.checked=on;
    cb.onchange=()=>{ keys.forEach(k=>toggleKey(k,cb.checked));
      renderEntityList(); renderCount(); render(); };
    tdCb.appendChild(cb); tr.appendChild(tdCb);
    const tdName=el("td","l");
    const sw=el("span","sw"); sw.style.display="inline-block"; sw.style.marginRight="6px";
    const first=keys.find(k=>picked().includes(k));
    if(first){ sw.style.background=colorOf(first); sw.style.borderColor=colorOf(first); }
    tdName.appendChild(sw); tdName.appendChild(document.createTextNode(e.entity));
    tr.appendChild(tdName);
    tr.appendChild(el("td","l",DATA.subsystemLabels[e.subsystem]||e.subsystem));
    tr.appendChild(el("td","l",e.group||"—"));
    mset.forEach(m=>{
      const k=skey(m,e.subsystem,e.entity);
      const vals=fullSeries(k);
      let last=null; for(let i=vals.length-1;i>=0;i--) if(vals[i]!=null){last=vals[i];break;}
      tr.appendChild(el("td",null,fmtNum(last,decOf(k))));
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  host.appendChild(table);
}
function syncEntitySelection(){
  // when the metric toggles change, rebuild the picked list from the same entities
  const mset=[...state.metrics[state.view]];
  const seen=new Set(), ents=[];
  picked().forEach(k=>{
    const a=k.split("|"), id=a[1]+"|"+a[2];
    if(!seen.has(id)){ seen.add(id); ents.push({subsystem:a[1], entity:a[2]}); }
  });
  picked().forEach(k=>releaseSlot(k));
  const list=[];
  ents.forEach(e=>{
    const keys=mset.map(m=>skey(m,e.subsystem,e.entity)).filter(exists);
    if(!keys.length) return;  // whole set or none
    keys.forEach(k=>{ claimSlot(k); list.push(k); });
  });
  state.picked[state.view]=list;
}

/* ---------- stat tiles ----------------------------------------------------- */
const decOf = k => ["%","R$/MWh","m"].includes(unitOf(k)) ? 1 : 0;
function renderTiles(){
  const host=document.getElementById("tiles"); host.innerHTML="";
  if(picked().length>CHART_MAX){
    host.innerHTML='<div class="card empty">'+picked().length+
      ' series selected — stat tiles are hidden above '+CHART_MAX+
      '. Switch on Table to see them all.</div>';
    renderCount();
    return;
  }
  picked().forEach(k=>{
    const v=seriesValues(k).filter(x=>x!=null);
    const t=el("div","tile"), unit=unitOf(k), dec=decOf(k);
    if(!v.length){
      t.innerHTML='<div class="nm">'+labelOf(k)+'</div><div class="big">–</div>';
      host.appendChild(t); return;
    }
    const last=v[v.length-1], first=v[0];
    const avg=v.reduce((a,b)=>a+b,0)/v.length;
    const chg = Math.abs(first)>1e-9 ? 100*(last-first)/Math.abs(first) : null;
    t.innerHTML =
      '<div class="nm"><span class="sw" style="background:'+colorOf(k)+
        ';border-color:'+colorOf(k)+'"></span>'+labelOf(k)+'</div>'+
      '<div class="big">'+fmtNum(last,dec)+' <span style="font-size:12px;'+
        'color:var(--text-2);font-weight:400">'+unit+'</span></div>'+
      '<div class="meta">avg '+fmtNum(avg,dec)+' · min '+fmtNum(Math.min(...v),dec)+
        ' · max '+fmtNum(Math.max(...v),dec)+
        (chg==null?"":' · '+(chg>=0?"+":"")+chg.toFixed(1)+'% over range')+'</div>';
    host.appendChild(t);
  });
  renderCount();
}

/* ---------- chart ---------------------------------------------------------- */
const NS="http://www.w3.org/2000/svg";
function svgEl(n,a){const e=document.createElementNS(NS,n);
  for(const k in a) e.setAttribute(k,a[k]); return e;}
function niceTicks(lo,hi,n){
  if(lo===hi){lo-=1;hi+=1;}
  const raw=(hi-lo)/n, mag=Math.pow(10,Math.floor(Math.log10(raw)));
  const norm=raw/mag, step=(norm<=1?1:norm<=2?2:norm<=5?5:10)*mag;
  const out=[]; for(let v=Math.ceil(lo/step)*step; v<=hi+step*1e-9; v+=step) out.push(v);
  return out;
}
const MON=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function axisLabel(iso,span){
  const [y,m,d]=iso.split("-");
  return span>200 ? MON[+m-1]+" '"+y.slice(2) : d+" "+MON[+m-1];
}
const shorten=(s,n)=> s.length>n ? s.slice(0,n-1)+"…" : s;

function drawPanel(unit,title,keys,W){
  const dates=windowDates();
  const cols=keys.map(k=>({key:k,vals:seriesValues(k),color:colorOf(k)}))
                 .filter(c=>c.vals.some(v=>v!=null));
  if(!cols.length) return null;

  const card=el("div","card");
  const decs = ["%","R$/MWh","m"].includes(unit) ? 1 : 0;
  card.innerHTML='<p class="panel-title">'+title+'</p><p class="panel-note">'+
    (state.smooth>1? state.smooth+"-day moving average" : "Daily values")+
    ' · '+dates[0]+' to '+dates[dates.length-1]+'</p>';

  const H=330, ML=64, MT=12, MB=30;
  const direct = cols.length<=4, MR = direct?200:16;
  const svg=svgEl("svg",{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":title});
  svg.style.width="100%"; svg.style.height=H+"px";

  let lo=Infinity,hi=-Infinity;
  cols.forEach(c=>c.vals.forEach(v=>{if(v!=null){lo=Math.min(lo,v);hi=Math.max(hi,v);}}));
  if(lo>0 && lo/hi<=0.55) lo=0;                 // baseline at zero unless far from it
  const pad=(hi-lo)*0.06||1; hi+=pad; if(lo<0) lo-=pad;

  const x=i=>ML+(W-ML-MR)*(dates.length<2?0.5:i/(dates.length-1));
  const y=v=>MT+(H-MT-MB)*(1-(v-lo)/(hi-lo));

  niceTicks(lo,hi,5).forEach(t=>{
    svg.appendChild(svgEl("line",{x1:ML,x2:W-MR,y1:y(t),y2:y(t),
      stroke:"var(--grid)","stroke-width":1}));
    const lb=svgEl("text",{x:ML-9,y:y(t)+4,"text-anchor":"end",
      fill:"var(--muted)","font-size":11.5});
    lb.textContent=fmtNum(t,decs);
    lb.style.fontVariantNumeric="tabular-nums"; svg.appendChild(lb);
  });
  if(lo<0&&hi>0) svg.appendChild(svgEl("line",{x1:ML,x2:W-MR,y1:y(0),y2:y(0),
    stroke:"var(--axis)","stroke-width":1.5}));

  const nT=Math.min(7,dates.length);
  for(let i=0;i<nT;i++){
    const di=Math.round(i*(dates.length-1)/Math.max(1,nT-1));
    const t=svgEl("text",{x:x(di),y:H-9,fill:"var(--muted)","font-size":11.5,
      "text-anchor":i===0?"start":(i===nT-1?"end":"middle")});
    t.textContent=axisLabel(dates[di],dates.length); svg.appendChild(t);
  }

  cols.forEach(c=>{
    let d="",pen=false;
    c.vals.forEach((v,i)=>{
      if(v==null){pen=false;return;}
      d+=(pen?"L":"M")+x(i).toFixed(1)+" "+y(v).toFixed(1)+" "; pen=true;
    });
    svg.appendChild(svgEl("path",{d,fill:"none",stroke:c.color,"stroke-width":2,
      "stroke-linejoin":"round","stroke-linecap":"round"}));
  });

  if(direct){
    const marks=[];
    cols.forEach(c=>{
      let li=-1; for(let i=c.vals.length-1;i>=0;i--) if(c.vals[i]!=null){li=i;break;}
      if(li>=0) marks.push({c,li,y:y(c.vals[li])});
    });
    marks.sort((a,b)=>a.y-b.y);
    for(let i=1;i<marks.length;i++)
      if(marks[i].y-marks[i-1].y<15) marks[i].y=marks[i-1].y+15;
    const over=marks.length? marks[marks.length-1].y-(H-MB) : 0;
    if(over>0) marks.forEach(m=>m.y-=over);
    marks.forEach(m=>{
      svg.appendChild(svgEl("circle",{cx:x(m.li),cy:y(m.c.vals[m.li]),r:3.5,
        fill:m.c.color,stroke:"var(--surface-1)","stroke-width":2}));
      const g=svgEl("text",{x:W-MR+9,y:m.y+4,fill:"var(--text-1)","font-size":11.5});
      g.textContent=shorten(labelOf(m.c.key),28); svg.appendChild(g);
    });
  }

  const cross=svgEl("line",{x1:0,x2:0,y1:MT,y2:H-MB,stroke:"var(--axis)",
    "stroke-width":1,opacity:0});
  svg.appendChild(cross);
  const dots=svgEl("g",{opacity:0}); svg.appendChild(dots);
  const hit=svgEl("rect",{x:ML,y:MT,width:W-ML-MR,height:H-MT-MB,fill:"transparent"});
  svg.appendChild(hit);

  const tt=document.getElementById("tt");
  hit.addEventListener("pointermove",ev=>{
    const r=svg.getBoundingClientRect();
    const px=(ev.clientX-r.left)/r.width*W;
    const i=Math.max(0,Math.min(dates.length-1,
      Math.round((px-ML)/(W-ML-MR)*(dates.length-1))));
    cross.setAttribute("x1",x(i)); cross.setAttribute("x2",x(i));
    cross.setAttribute("opacity",1);
    dots.innerHTML=""; dots.setAttribute("opacity",1);
    let rows="";
    cols.forEach(c=>{
      const v=c.vals[i]; if(v==null) return;
      dots.appendChild(svgEl("circle",{cx:x(i),cy:y(v),r:4,fill:c.color,
        stroke:"var(--surface-1)","stroke-width":2}));
      rows+='<tr><td><span class="sw" style="display:inline-block;background:'+
        c.color+';border-color:'+c.color+'"></span> '+labelOf(c.key)+
        '</td><td class="v">'+fmtNum(v,decs)+'</td></tr>';
    });
    tt.innerHTML='<div class="d">'+dates[i]+'</div><table>'+rows+'</table>';
    tt.style.display="block";
    const tw=tt.offsetWidth, th=tt.offsetHeight;
    tt.style.left=Math.min(window.innerWidth-tw-12, ev.clientX+16)+"px";
    tt.style.top=Math.min(window.innerHeight-th-12, Math.max(8,ev.clientY-th/2))+"px";
  });
  hit.addEventListener("pointerleave",()=>{
    tt.style.display="none"; cross.setAttribute("opacity",0);
    dots.setAttribute("opacity",0);
  });

  card.appendChild(svg);
  if(cols.length>=2){
    const lg=el("div","legend");
    cols.forEach(c=>{
      const s=el("span");
      s.innerHTML='<span class="sw" style="background:'+c.color+';border-color:'+
        c.color+'"></span>'+labelOf(c.key);
      lg.appendChild(s);
    });
    card.appendChild(lg);
  }
  return card;
}
function renderCharts(){
  const host=document.getElementById("charts"); host.innerHTML="";
  if(!picked().length){
    host.innerHTML='<div class="card empty">'+
      (view().kind? "Pick one or more from the list above."
                  : "Pick one or more series above.")+'</div>';
    return;
  }
  if(picked().length>CHART_MAX){
    host.innerHTML='<div class="card empty">'+picked().length+
      ' series selected — too many to chart clearly. Switch on Table below, '+
      'or narrow your Region/Fuel/search filters.</div>';
    return;
  }
  const W=Math.max(680, host.clientWidth-34);
  DATA.unitPanels.forEach(p=>{
    const keys=picked().filter(k=>unitOf(k)===p.unit);
    if(!keys.length) return;
    const c=drawPanel(p.unit,p.title,keys,W);
    if(c) host.appendChild(c);
  });
}

/* ---------- table + CSV ---------------------------------------------------- */
const tableData = () => ({dates:windowDates(),
  cols:picked().map(k=>({key:k,vals:seriesValues(k)}))});
function renderTable(){
  const card=document.getElementById("tableCard");
  card.hidden=!state.table;
  if(!state.table) return;
  const {dates,cols}=tableData();
  let h="<thead><tr><th>Date</th>"+
    cols.map(c=>"<th>"+labelOf(c.key)+"</th>").join("")+"</tr></thead><tbody>";
  for(let i=dates.length-1;i>=0;i--)
    h+="<tr><td>"+dates[i]+"</td>"+
      cols.map(c=>"<td>"+fmtNum(c.vals[i],decOf(c.key))+"</td>").join("")+"</tr>";
  document.getElementById("dataTable").innerHTML=h+"</tbody>";
}
function downloadCSV(){
  const {dates,cols}=tableData();
  if(!cols.length) return;
  let s="date,"+cols.map(c=>'"'+labelOf(c.key)+' ('+unitOf(c.key)+')"').join(",")+"\n";
  dates.forEach((d,i)=>{
    s+=d+","+cols.map(c=>c.vals[i]==null?"":c.vals[i].toFixed(2)).join(",")+"\n";
  });
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([s],{type:"text/csv"}));
  a.download="ons_"+state.view+"_"+dates[0]+"_"+dates[dates.length-1]+".csv";
  a.click(); URL.revokeObjectURL(a.href);
}

function renderCount(){
  const c=document.getElementById("count");
  if(c) c.textContent=picked().length+" series selected · "
    +windowDates().length+" days";
}
function render(){ renderTiles(); renderCharts(); renderTable(); }

/* ---------- boot ----------------------------------------------------------- */
async function unpack(){
  const b64=document.getElementById("payload").textContent.trim();
  const bin=Uint8Array.from(atob(b64), c=>c.charCodeAt(0));
  if(typeof DecompressionStream!=="function")
    throw new Error("This browser lacks DecompressionStream (needs Chrome/Edge 80+, "+
      "Firefox 113+, or Safari 16.4+).");
  const ds=new DecompressionStream("gzip");
  const txt=await new Response(new Blob([bin]).stream().pipeThrough(ds)).text();
  return JSON.parse(txt);
}

async function boot(){
  try{ DATA=await unpack(); }
  catch(err){
    document.getElementById("boot").innerHTML =
      "Could not unpack the embedded data.<br>"+err.message;
    return;
  }
  document.getElementById("boot").hidden=true;
  document.getElementById("app").hidden=false;

  const last=DATA.dates[DATA.dates.length-1];
  state.to=last;
  state.from=DATA.dates[Math.max(0,DATA.dates.length-366)];

  document.getElementById("subtitle").textContent =
    "Brazilian grid balances · "+DATA.dates[0]+" to "+last+" · daily";
  document.getElementById("foot").innerHTML =
    'Source: <a href="https://dados.ons.org.br" target="_blank" rel="noopener">ONS '+
    'Dados Abertos</a> (CC-BY). Balanço de Energia nos Subsistemas · Geração por '+
    'Usina em Base Horária · Geração Térmica por Motivo de Despacho (sheet 09) · '+
    'ENA/EAR Diário por Subsistema · Dados Hidráulicos por Reservatório (sheets '+
    '23–26) · CMO Semi-Horário. Hourly and semi-hourly sources are averaged to '+
    'daily means. SIN rows are summed for absolute series; EAR % and ENA %MLT are '+
    'rebuilt from their components, CMO is an unweighted subsystem mean. '+
    'Net interchange is positive when the subsystem is a net exporter, matching the '+
    'bulletin. ONS revises recent days after publication. Built '+DATA.generated+'.';

  ["from","to"].forEach(id=>{
    const inp=document.getElementById(id);
    inp.min=DATA.dates[0]; inp.max=last;
    inp.onchange=()=>{ state[id]=inp.value||state[id]; render(); };
  });
  document.getElementById("tableBtn").onclick=e=>{
    state.table=!state.table;
    e.target.setAttribute("aria-pressed",String(state.table)); renderTable();
  };
  document.getElementById("csvBtn").onclick=downloadCSV;
  document.getElementById("themeBtn").onclick=()=>{
    document.documentElement.dataset.theme = isDark() ? "light" : "dark";
    buildPickCard(); render();
  };
  let rz; window.addEventListener("resize",()=>{
    clearTimeout(rz); rz=setTimeout(renderCharts,140);
  });

  // opening selections
  ["load","gen_thermal","thermal_gas","ear_pct"].forEach(m=>{
    const k=skey(m,"SIN");
    if(exists(k)){
      claimSlot(k, "subsystems"); state.picked.subsystems.push(k);
    }
  });
  const seedEnts=(viewId,metric,rows)=>{
    const seen=new Set();
    (rows||[]).forEach(r=>{
      const id=r.subsystem+"|"+r.entity;
      if(seen.has(id)) return;
      seen.add(id);
      const k=skey(metric,r.subsystem,r.entity);
      if(exists(k)){
        claimSlot(k, viewId); state.picked[viewId].push(k);
      }
    });
  };
  seedEnts("plants","plant_verif",DATA.defaults.plant);
  seedEnts("reservoirs","res_volutil_pct",DATA.defaults.reservoir);

  buildTabs(); buildPresets(); buildSmooth(); buildSubs(); updateSubsVisibility();
  syncInputs(); buildPickCard(); render();
}
boot();
</script>
</body>
</html>
"""
