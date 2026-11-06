# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — reporter.py
# ─────────────────────────────────────────────
#
#  Builds a single self-contained, offline HTML report (no external
#  requests) plus JSON and CSV exports next to it.
#
#  The report embeds the data as JSON and renders it with vanilla JS:
#    Overview   — KPIs, executive summary, key players, segments, trends,
#                 white spaces, watchlist
#    Landscape  — positioning map (stage × relevance), distributions
#    Entities   — searchable / filterable / sortable table + detail drawer
#    Compare    — side-by-side view of up to 4 selected entities
#    Method     — sources, data quality, flagged entities
#
#  All text is inserted with textContent / escaping — LLM output can never
#  inject markup into the report.
# ─────────────────────────────────────────────

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path

import llm
from project import Project, write_json

INDUSTRY = {"Company", "Spinoff"}
ACADEMIC = {"University", "Research Institute"}


def _group(etype: str | None) -> str:
    if etype in INDUSTRY:
        return "Industry"
    if etype in ACADEMIC:
        return "Academic"
    return "Other"


def _country(c):
    from openalex import country_name
    return country_name(c) if isinstance(c, str) and len(c) == 2 and c.isupper() else c


def _entity_payload(r: dict) -> dict:
    research = r.get("research") or None
    people = r.get("key_people") or []
    if isinstance(people, str):
        people = [{"name": p.strip(), "role": None} for p in people.split(",") if p.strip()]
    outputs = []
    for o in r.get("notable_outputs") or []:
        if isinstance(o, str):
            o = {"type": "other", "title": o}
        elif "label" in o and "title" not in o:       # v1 cache shape
            o = {"type": "other", "title": o["label"], "url": o.get("url")}
        outputs.append({k: o.get(k) for k in ("type", "title", "year", "url", "cited_by", "link_kind")})
    return {
        "name": r.get("entity_name"),
        "official_name": r.get("official_name"),
        "website": r.get("website") or (r.get("entity_urls") or [None])[0],
        "type": r.get("entity_type") or "Other",
        "group": _group(r.get("entity_type")),
        "country": _country(r.get("country")),
        "city": r.get("city"),
        "founded": r.get("founded_or_established"),
        "stage": r.get("development_stage") or "Unknown",
        "position": r.get("competitive_position") or "Unknown",
        "score": r.get("relevance_score"),
        "rationale": r.get("relevance_rationale"),
        "score_first": r.get("relevance_first_pass"),
        "summary": r.get("summary"),
        "tech": r.get("technology_focus"),
        "application": r.get("domain_application"),
        "business_model": r.get("business_model"),
        "funding": r.get("funding_or_status"),
        "people": people,
        "outputs": outputs,
        "strengths": r.get("strengths") or [],
        "risks": r.get("risks") or [],
        "tags": r.get("tags") or [],
        "partners": r.get("partners") or [],
        "developments": r.get("recent_developments") or [],
        "research": research and {
            "institution": (research.get("institution") or {}).get("display_name"),
            "institution_url": (research.get("institution") or {}).get("url"),
            "topic_works": research.get("topic_works"),
            "momentum": research.get("momentum"),
            "series": research.get("series") or [],
        },
        "evidence": r.get("evidence_quality") or "Low",
        "sources": [{"url": s.get("url"), "title": s.get("title"), "ok": s.get("ok"), "kind": s.get("kind")}
                    for s in (r.get("sources") or []) if s.get("url")],
        "discovered": bool(r.get("discovered")),
        "flag": ("not an organisation" if r.get("is_organization") is False
                 else (r.get("_error") or None)),
        "updated": r.get("_updated"),
    }


CSV_COLUMNS = ["name", "type", "country", "stage", "position", "score", "summary", "tech",
               "application", "business_model", "funding", "website", "people", "topic_papers",
               "momentum", "tags", "evidence", "discovered", "flag"]


def _csv(entities: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for e in entities:
        w.writerow([
            e["name"], e["type"], e["country"] or "", e["stage"], e["position"], e["score"] or "",
            e["summary"] or "", e["tech"] or "", e["application"] or "", e["business_model"] or "",
            e["funding"] or "", e["website"] or "",
            "; ".join(p.get("name", "") + (f" ({p['role']})" if p.get("role") else "") for p in e["people"]),
            (e["research"] or {}).get("topic_works") or "", (e["research"] or {}).get("momentum") or "",
            ", ".join(e["tags"]), e["evidence"], "yes" if e["discovered"] else "", e["flag"] or "",
        ])
    return buf.getvalue()


def build_payload(project: Project, records: list[dict], landscape: dict | None) -> dict:
    entities = [_entity_payload(r) for r in records if r.get("entity_name")]
    entities.sort(key=lambda e: (e["flag"] is not None, -(e["score"] or 0),
                                 -((e["research"] or {}).get("topic_works") or 0)))
    land = dict(landscape or {})
    land.pop("_fp", None)
    if "stats" not in land:
        from landscape import stats
        land["stats"] = stats(records)
    return {
        "project": {
            "slug": project.slug, "topic": project.topic, "description": project.description,
            "domain_label": project.domain_label,
        },
        "generated": datetime.now().isoformat(timespec="minutes"),
        "model": llm.describe(),
        "landscape": land,
        "entities": entities,
    }


def generate_report(project: Project, records: list[dict], landscape: dict | None = None,
                    filename: str | None = None) -> Path:
    project.reports_dir.mkdir(parents=True, exist_ok=True)
    filename = filename or f"report_{date.today().isoformat()}.html"
    out_path = project.reports_dir / filename

    payload = build_payload(project, records, landscape)
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    title = f"{project.topic.strip().title()} — Competitive Landscape"
    html = (TEMPLATE.replace("__TITLE__", _esc(title))
                    .replace("__DATA__", data_json))
    out_path.write_text(html, encoding="utf-8")

    write_json(out_path.with_suffix(".json"), payload)
    out_path.with_suffix(".csv").write_text(_csv(payload["entities"]), encoding="utf-8-sig")
    print(f"  [REPORT] Saved → {out_path}")
    return out_path


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title>
<style>
:root{
  color-scheme:light;
  --bg:#f6f6f4; --surface:#ffffff; --surface-2:#f1f1ee; --border:#e3e2dd; --border-strong:#cfcec8;
  --text:#0b0b0b; --text-2:#52514e; --text-3:#7a7974;
  --accent:#2a78d6; --accent-soft:#e6f0fc; --accent-ink:#1c5cab;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
  --seq:#2a78d6; --seq-soft:#cde2fb;
  --warn:#b26b00; --warn-soft:#fdf1dc; --bad:#c43c3b; --bad-soft:#fbe7e6; --good:#14804a; --good-soft:#e2f4ea;
  --shadow:0 1px 2px rgba(0,0,0,.04),0 2px 8px rgba(0,0,0,.04);
  --radius:12px;
}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --bg:#111110; --surface:#1a1a19; --surface-2:#232321; --border:#2e2e2b; --border-strong:#41413d;
    --text:#f0efec; --text-2:#c3c2b7; --text-3:#8f8e86;
    --accent:#3987e5; --accent-soft:#16263a; --accent-ink:#86b6ef;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --seq:#3987e5; --seq-soft:#184f95;
    --warn:#e0a03a; --warn-soft:#33270f; --bad:#e66767; --bad-soft:#3a1d1d; --good:#4cc38a; --good-soft:#15301f;
    --shadow:none;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --bg:#111110; --surface:#1a1a19; --surface-2:#232321; --border:#2e2e2b; --border-strong:#41413d;
  --text:#f0efec; --text-2:#c3c2b7; --text-3:#8f8e86;
  --accent:#3987e5; --accent-soft:#16263a; --accent-ink:#86b6ef;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --seq:#3987e5; --seq-soft:#184f95;
  --warn:#e0a03a; --warn-soft:#33270f; --bad:#e66767; --bad-soft:#3a1d1d; --good:#4cc38a; --good-soft:#15301f;
  --shadow:none;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased}
a{color:var(--accent-ink);text-decoration:none}
a:hover{text-decoration:underline}
button{font:inherit;color:inherit;cursor:pointer}
.wrap{max-width:1240px;margin:0 auto;padding:0 24px}
header.top{background:var(--surface);border-bottom:1px solid var(--border)}
.top-inner{padding:28px 0 0}
.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--text-3);font-weight:600}
h1{font-size:28px;line-height:1.2;letter-spacing:-.02em;margin:6px 0 6px;font-weight:700}
.desc{color:var(--text-2);max-width:820px}
.meta-line{display:flex;gap:14px;flex-wrap:wrap;color:var(--text-3);font-size:12px;margin-top:10px}
.top-actions{display:flex;gap:8px;margin-left:auto}
.btn{border:1px solid var(--border-strong);background:var(--surface);border-radius:8px;padding:6px 12px;font-size:13px}
.btn:hover{background:var(--surface-2)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
nav.tabs{display:flex;gap:4px;margin-top:20px;overflow-x:auto}
nav.tabs button{background:none;border:none;border-bottom:2px solid transparent;padding:10px 14px;color:var(--text-2);font-weight:600;white-space:nowrap}
nav.tabs button[aria-selected="true"]{color:var(--text);border-color:var(--accent)}
nav.tabs .count{font-weight:500;color:var(--text-3);margin-left:4px}
main{padding:28px 0 60px}
section.tab{display:none}
section.tab.active{display:block}
.grid{display:grid;gap:16px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow)}
.kpi .v{font-size:28px;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.kpi .l{color:var(--text-2);font-size:12px;margin-top:4px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px 22px;box-shadow:var(--shadow)}
.card h2{font-size:15px;font-weight:700;margin-bottom:12px;letter-spacing:-.005em}
.card h3{font-size:13px;font-weight:700;margin:14px 0 6px}
.two{grid-template-columns:1.6fr 1fr}
.three{grid-template-columns:repeat(3,1fr)}
@media (max-width:900px){.two,.three{grid-template-columns:1fr}}
.lead{font-size:15px;line-height:1.65;color:var(--text)}
ul.clean{list-style:none}
ul.clean li{padding:8px 0;border-top:1px solid var(--border)}
ul.clean li:first-child{border-top:none;padding-top:0}
ul.dots{padding-left:18px;color:var(--text-2)}
ul.dots li{margin:4px 0}
.muted{color:var(--text-3)}
.small{font-size:12px}
.chip{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--border);border-radius:999px;padding:1px 9px;font-size:12px;color:var(--text-2);background:var(--surface);white-space:nowrap}
.chip.solid{background:var(--surface-2);border-color:transparent}
.chip.warn{background:var(--warn-soft);color:var(--warn);border-color:transparent}
.chip.bad{background:var(--bad-soft);color:var(--bad);border-color:transparent}
.chip.good{background:var(--good-soft);color:var(--good);border-color:transparent}
.chip.new{background:var(--accent-soft);color:var(--accent-ink);border-color:transparent}
.linkish{background:none;border:none;color:var(--accent-ink);padding:0;font-weight:600;text-align:left}
.linkish:hover{text-decoration:underline}
.segment{border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-top:10px}
.segment .members{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
/* toolbar */
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:14px}
.toolbar input[type=search],.toolbar select{font:inherit;padding:7px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--text)}
.toolbar input[type=search]{min-width:240px;flex:1 1 240px}
.seg-btns{display:inline-flex;border:1px solid var(--border-strong);border-radius:8px;overflow:hidden}
.seg-btns button{border:none;background:var(--surface);padding:7px 12px;font-size:13px}
.seg-btns button[aria-pressed="true"]{background:var(--accent-soft);color:var(--accent-ink);font-weight:600}
/* table */
.table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:auto;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border);vertical-align:top}
th{position:sticky;top:0;background:var(--surface-2);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-2);font-weight:700;white-space:nowrap;cursor:pointer;user-select:none}
th.nosort{cursor:default}
th .arrow{opacity:.5;margin-left:3px}
tbody tr{cursor:pointer}
tbody tr:hover{background:var(--surface-2)}
tbody tr.flagged td{opacity:.6}
td.name{min-width:220px}
td.name .n{font-weight:650;color:var(--text)}
td.name .sub{color:var(--text-3);font-size:12px}
td.sum{color:var(--text-2);min-width:280px;max-width:420px}
.score{display:inline-flex;gap:3px;align-items:center}
.score i{width:8px;height:8px;border-radius:50%;background:var(--border-strong);display:inline-block}
.score i.on{background:var(--seq)}
input[type=checkbox]{accent-color:var(--accent);width:15px;height:15px}
/* cards view */
.cards{grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}
.ecard{cursor:pointer;display:flex;flex-direction:column;gap:8px}
.ecard:hover{border-color:var(--border-strong)}
.ecard .row{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.ecard .nm{font-weight:700;font-size:15px}
.ecard p{color:var(--text-2);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
/* drawer */
.backdrop{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;z-index:20}
.drawer{position:fixed;top:0;right:0;height:100%;width:min(720px,100%);background:var(--surface);border-left:1px solid var(--border);z-index:21;transform:translateX(100%);transition:transform .2s ease;overflow-y:auto}
.drawer.open{transform:none}
.backdrop.open{display:block}
.drawer-head{position:sticky;top:0;background:var(--surface);border-bottom:1px solid var(--border);padding:18px 24px;display:flex;gap:12px;align-items:flex-start;z-index:1}
.drawer-head h2{font-size:20px;letter-spacing:-.01em}
.drawer-body{padding:20px 24px 40px}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px 16px;margin:14px 0}
.fact .k{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-3);font-weight:700}
.fact .v{margin-top:2px}
.callout{background:var(--surface-2);border-radius:10px;padding:12px 14px;color:var(--text);margin:10px 0}
.kv{display:grid;grid-template-columns:150px 1fr;gap:6px 14px;margin-top:8px}
.kv .k{color:var(--text-3);font-size:12px;font-weight:600;padding-top:1px}
.timeline{border-left:2px solid var(--border);padding-left:14px;margin-top:6px}
.timeline div{margin:8px 0;position:relative}
.timeline div::before{content:"";position:absolute;left:-19px;top:7px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
/* charts */
.chart-title{font-size:13px;font-weight:700;margin-bottom:2px}
.chart-sub{font-size:12px;color:var(--text-3);margin-bottom:10px}
.hbar{display:grid;grid-template-columns:minmax(90px,140px) 1fr 36px;gap:8px;align-items:center;font-size:12px;margin:5px 0}
.hbar .lab{color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hbar .track{height:14px;position:relative}
.hbar .fill{height:100%;background:var(--seq);border-radius:0 4px 4px 0;min-width:2px}
.hbar .num{color:var(--text);font-variant-numeric:tabular-nums;text-align:right}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--text-2);margin-top:8px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{width:10px;height:10px;border-radius:50%;display:inline-block}
svg text{fill:var(--text-3);font-size:11px}
.tip{position:fixed;pointer-events:none;background:var(--text);color:var(--surface);padding:7px 10px;border-radius:8px;font-size:12px;max-width:280px;z-index:40;display:none;line-height:1.4}
.empty{padding:40px;text-align:center;color:var(--text-3)}
.compare-table th{cursor:default;position:static;text-transform:none;letter-spacing:0;font-size:12px}
.compare-table td{min-width:180px}
.note{font-size:12px;color:var(--text-3);margin-top:10px}
@media print{
  header.top nav,.top-actions,.toolbar,.drawer,.backdrop{display:none!important}
  section.tab{display:block!important;page-break-before:always}
  body{background:#fff}
  .card,.kpi,.table-wrap{box-shadow:none}
}
</style>
</head>
<body>
<header class="top">
  <div class="wrap top-inner">
    <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
      <div style="flex:1 1 520px">
        <div class="eyebrow">Competitive landscape report</div>
        <h1 id="title"></h1>
        <p class="desc" id="desc"></p>
        <div class="meta-line" id="metaLine"></div>
      </div>
      <div class="top-actions">
        <button class="btn" id="themeBtn" title="Toggle light/dark">◐ Theme</button>
        <button class="btn" id="csvBtn">Export CSV</button>
        <button class="btn" id="jsonBtn">Export JSON</button>
        <button class="btn" onclick="window.print()">Print</button>
      </div>
    </div>
    <nav class="tabs" role="tablist" id="tabs"></nav>
  </div>
</header>

<main class="wrap">
  <section class="tab" id="tab-overview"></section>
  <section class="tab" id="tab-landscape"></section>
  <section class="tab" id="tab-entities">
    <div class="toolbar">
      <input type="search" id="q" placeholder="Search name, technology, people, tags…" aria-label="Search entities">
      <select id="fType" aria-label="Filter by type"></select>
      <select id="fStage" aria-label="Filter by stage"></select>
      <select id="fCountry" aria-label="Filter by country"></select>
      <select id="fScore" aria-label="Minimum relevance">
        <option value="0">Any relevance</option><option value="3">Relevance ≥ 3</option>
        <option value="4">Relevance ≥ 4</option><option value="5">Relevance 5</option>
      </select>
      <label class="small muted" style="display:inline-flex;gap:6px;align-items:center"><input type="checkbox" id="fFlagged"> show flagged</label>
      <span class="seg-btns" role="group" aria-label="View">
        <button id="vTable" aria-pressed="true">Table</button><button id="vCards" aria-pressed="false">Cards</button>
      </span>
    </div>
    <div class="small muted" id="resultCount" style="margin-bottom:8px"></div>
    <div id="entityView"></div>
  </section>
  <section class="tab" id="tab-compare"></section>
  <section class="tab" id="tab-method"></section>
</main>

<div class="backdrop" id="backdrop"></div>
<aside class="drawer" id="drawer" aria-hidden="true"></aside>
<div class="tip" id="tip"></div>

<script id="ci-data" type="application/json">__DATA__</script>
<script>
(function(){
"use strict";
const D = JSON.parse(document.getElementById("ci-data").textContent);
const E = D.entities;
const L = D.landscape || {};
const S = L.stats || {};
const byName = new Map(E.map(e => [e.name, e]));
const STAGES = ["Research","Prototype","Pilot","Commercial","Unknown"];
const GROUPS = [["Industry","var(--s1)"],["Academic","var(--s2)"],["Other","var(--s3)"]];
const groupColor = g => (GROUPS.find(x => x[0] === g) || GROUPS[2])[1];
const state = {q:"", type:"", stage:"", country:"", minScore:0, flagged:false, sort:"score", dir:-1, view:"table", compare:new Set()};

/* ── tiny DOM helpers (all text goes through textContent) ── */
function h(tag, attrs, ...kids){
  const el = document.createElement(tag);
  if (attrs) for (const [k,v] of Object.entries(attrs)){
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()){
    if (kid == null || kid === false) continue;
    el.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
  }
  return el;
}
function svg(tag, attrs, ...kids){
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k,v] of Object.entries(attrs||{})) if (v != null) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid) el.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
  return el;
}
const safeUrl = u => (typeof u === "string" && /^https?:\/\//i.test(u)) ? u : null;
function ext(url, label){ const u = safeUrl(url); return u ? h("a",{href:u,target:"_blank",rel:"noopener noreferrer",text:label||u}) : h("span",{text:label||"—"}); }
const dash = v => (v == null || v === "" ? "—" : String(v));
function scoreDots(s){ const w = h("span",{class:"score",title:s?`Relevance ${s}/5`:"Not scored","aria-label":s?`Relevance ${s} of 5`:"Not scored"}); for (let i=1;i<=5;i++) w.appendChild(h("i",{class:s&&i<=s?"on":""})); return w; }
function host(u){ try { return new URL(u).hostname.replace(/^www\./,""); } catch(e){ return ""; } }

/* ── tooltip ── */
const tip = document.getElementById("tip");
function showTip(ev, lines){ tip.replaceChildren(...lines.map((l,i) => h("div",{style:i===0?"font-weight:700":"",text:l}))); tip.style.display="block"; moveTip(ev); }
function moveTip(ev){ const pad=14, r=tip.getBoundingClientRect(); let x=ev.clientX+pad, y=ev.clientY+pad; if (x+r.width>innerWidth-8) x=ev.clientX-r.width-pad; if (y+r.height>innerHeight-8) y=ev.clientY-r.height-pad; tip.style.left=x+"px"; tip.style.top=y+"px"; }
function hideTip(){ tip.style.display="none"; }
function hover(el, lines){ el.addEventListener("mouseenter", ev => showTip(ev, typeof lines==="function"?lines():lines)); el.addEventListener("mousemove", moveTip); el.addEventListener("mouseleave", hideTip); return el; }

/* ── header ── */
const topic = D.project.topic || "";
document.getElementById("title").textContent = topic.replace(/\b\w/g, c => c.toUpperCase());
document.getElementById("desc").textContent = D.project.description || "";
document.getElementById("metaLine").replaceChildren(
  h("span",{text:`Generated ${D.generated.replace("T"," ")}`}), h("span",{text:`Model: ${D.model}`}),
  h("span",{text:`${E.length} entities`}));

/* ── tabs ── */
const TABS = [["overview","Overview"],["landscape","Landscape"],["entities","Entities",E.length],["compare","Compare"],["method","Sources & method"]];
const tabsEl = document.getElementById("tabs");
function selectTab(id){
  for (const b of tabsEl.children) b.setAttribute("aria-selected", b.dataset.id === id);
  for (const s of document.querySelectorAll("section.tab")) s.classList.toggle("active", s.id === "tab-"+id);
  if (id === "compare") renderCompare();
  try { history.replaceState(null, "", "#"+id); } catch(e){}
}
for (const [id,label,count] of TABS){
  const b = h("button",{role:"tab","data-id":id,onclick:()=>selectTab(id)}, label, count!=null ? h("span",{class:"count",text:count}) : null);
  if (id === "compare") b.id = "compareTab";
  tabsEl.appendChild(b);
}
function updateCompareTab(){ const b=document.getElementById("compareTab"); b.replaceChildren("Compare", state.compare.size ? h("span",{class:"count",text:state.compare.size}) : ""); }

/* ── Overview ── */
function nameLink(n){ return byName.has(n) ? h("button",{class:"linkish",text:n,onclick:()=>openDrawer(byName.get(n))}) : h("strong",{text:n}); }
function renderOverview(){
  const sec = document.getElementById("tab-overview");
  const valid = E.filter(e => !e.flag);
  const industry = valid.filter(e => e.group==="Industry").length;
  const academic = valid.filter(e => e.group==="Academic").length;
  const kpis = [
    [valid.length, "Organisations mapped"],
    [S.core_players ?? valid.filter(e=>(e.score||0)>=4).length, "Core players (relevance ≥ 4)"],
    [industry, "Companies & spinoffs"],
    [academic, "Universities & institutes"],
    [S.commercial ?? valid.filter(e=>["Pilot","Commercial"].includes(e.stage)).length, "At pilot / commercial stage"],
    [(S.topic_papers||0).toLocaleString(), "Topic papers (OpenAlex)"],
  ];
  sec.appendChild(h("div",{class:"grid kpis"}, kpis.map(([v,l]) => h("div",{class:"kpi"}, h("div",{class:"v",text:v}), h("div",{class:"l",text:l})))));

  const left = h("div",{class:"grid",style:"gap:16px"});
  const right = h("div",{class:"grid",style:"gap:16px;align-content:start"});
  if (L.executive_summary){
    left.appendChild(h("div",{class:"card"}, h("h2",{text:"Executive summary"}), h("p",{class:"lead",text:L.executive_summary})));
  } else {
    left.appendChild(h("div",{class:"card"}, h("h2",{text:"Executive summary"}), h("p",{class:"muted",text:L.error ? "Landscape synthesis failed: "+L.error : "Landscape synthesis was not run for this report."})));
  }
  if ((L.segments||[]).length){
    left.appendChild(h("div",{class:"card"}, h("h2",{text:"Segments"}),
      L.segments.map(s => h("div",{class:"segment"}, h("strong",{text:s.name}), h("div",{class:"muted small",text:s.description}),
        h("div",{class:"members"}, (s.members||[]).map(m => h("button",{class:"chip",onclick:()=>byName.has(m)&&openDrawer(byName.get(m)),text:m})))))));
  }
  const two = h("div",{class:"grid three"});
  if ((L.trends||[]).length) two.appendChild(h("div",{class:"card"}, h("h2",{text:"Trends"}), h("ul",{class:"dots"}, L.trends.map(t => h("li",{text:t})))));
  if ((L.white_spaces||[]).length) two.appendChild(h("div",{class:"card"}, h("h2",{text:"White spaces"}), h("ul",{class:"dots"}, L.white_spaces.map(t => h("li",{text:t})))));
  if ((L.watchlist||[]).length) two.appendChild(h("div",{class:"card"}, h("h2",{text:"Watchlist"}), h("ul",{class:"clean"}, L.watchlist.map(w => h("li",{}, nameLink(w.name), h("div",{class:"small muted",text:w.why}))))));
  if (two.children.length) left.appendChild(two);

  const kp = (L.key_players||[]);
  right.appendChild(h("div",{class:"card"}, h("h2",{text:kp.length ? "Key players" : "Top by relevance"}),
    h("ul",{class:"clean"}, (kp.length ? kp : valid.slice(0,6).map(e => ({name:e.name, why:e.rationale||e.summary||""})))
      .map(k => { const e = byName.get(k.name); return h("li",{}, h("div",{style:"display:flex;justify-content:space-between;gap:8px;align-items:center"}, nameLink(k.name), e ? scoreDots(e.score) : null), h("div",{class:"small muted",text:k.why})); }))));
  right.appendChild(h("div",{class:"card"}, h("div",{class:"chart-title",text:"Research momentum"}),
    h("div",{class:"chart-sub",text:"Topic papers, most active organisations (OpenAlex)"}), momentumBars()));
  sec.appendChild(h("div",{class:"grid two",style:"margin-top:16px"}, left, right));
}

function hbars(rows, opts){
  opts = opts || {};
  const max = Math.max(1, ...rows.map(r => r[1]));
  return h("div",{}, rows.map(([label, v, extra]) => {
    const row = h("div",{class:"hbar"}, h("div",{class:"lab",title:label,text:label}),
      h("div",{class:"track"}, h("div",{class:"fill",style:`width:${(v/max*100).toFixed(1)}%`})), h("div",{class:"num",text:v}));
    if (opts.onclick) { row.style.cursor="pointer"; row.addEventListener("click", () => opts.onclick(label)); }
    return hover(row, [label, `${v} ${opts.unit||""}`.trim()].concat(extra?[extra]:[]));
  }));
}
function momentumBars(){
  const rows = E.filter(e => e.research && e.research.topic_works).sort((a,b)=>b.research.topic_works-a.research.topic_works).slice(0,8)
    .map(e => [e.name, e.research.topic_works, e.research.momentum!=null ? `3-yr momentum ${e.research.momentum>0?"+":""}${Math.round(e.research.momentum*100)}%` : null]);
  return rows.length ? hbars(rows,{unit:"papers",onclick:n=>openDrawer(byName.get(n))}) : h("p",{class:"muted small",text:"No research data (run enrichment with OpenAlex enabled)."});
}

/* ── Landscape ── */
function renderLandscape(){
  const sec = document.getElementById("tab-landscape");
  const valid = E.filter(e => !e.flag);
  const card = h("div",{class:"card"}, h("div",{class:"chart-title",text:"Positioning map"}),
    h("div",{class:"chart-sub",text:"Development stage × relevance to the topic. Click a dot to open the profile."}), positioningMap(valid),
    h("div",{class:"legend"}, GROUPS.map(([g,c]) => h("span",{}, h("i",{style:`background:${c}`}), g))));
  sec.appendChild(card);
  const count = (key, order) => { const m = new Map(); for (const e of valid){ const k = e[key] || "Unknown"; m.set(k,(m.get(k)||0)+1); } let rows=[...m.entries()]; if (order) rows.sort((a,b)=>order.indexOf(a[0])-order.indexOf(b[0])); else rows.sort((a,b)=>b[1]-a[1]); return rows; };
  const filt = (key) => (label) => { state.type=""; state.stage=""; state.country=""; state[key]=label; syncFilters(); selectTab("entities"); renderEntities(); };
  sec.appendChild(h("div",{class:"grid three",style:"margin-top:16px"},
    h("div",{class:"card"}, h("div",{class:"chart-title",text:"By type"}), h("div",{class:"chart-sub",text:"Click a bar to filter"}), hbars(count("type"),{unit:"entities",onclick:filt("type")})),
    h("div",{class:"card"}, h("div",{class:"chart-title",text:"By development stage"}), h("div",{class:"chart-sub",text:"Click a bar to filter"}), hbars(count("stage", STAGES),{unit:"entities",onclick:filt("stage")})),
    h("div",{class:"card"}, h("div",{class:"chart-title",text:"By country"}), h("div",{class:"chart-sub",text:"Top 10 · click a bar to filter"}), hbars(count("country").slice(0,10),{unit:"entities",onclick:filt("country")}))));
}
function positioningMap(list){
  const W=900, H=360, m={l:120,r:20,t:16,b:36};
  const iw=W-m.l-m.r, ih=H-m.t-m.b;
  const stages = STAGES;
  const y = s => m.t + (stages.length-1-stages.indexOf(s)) * ih/(stages.length-1);
  const x = v => m.l + (v-0.5)/5*iw;
  const root = svg("svg",{viewBox:`0 0 ${W} ${H}`,width:"100%",role:"img","aria-label":"Positioning map of entities by development stage and relevance"});
  stages.forEach(s => { root.appendChild(svg("line",{x1:m.l,x2:W-m.r,y1:y(s),y2:y(s),stroke:"var(--border)","stroke-width":1})); root.appendChild(svg("text",{x:m.l-10,y:y(s)+4,"text-anchor":"end"}, s)); });
  for (let v=1; v<=5; v++) root.appendChild(svg("text",{x:x(v),y:H-12,"text-anchor":"middle"}, String(v)));
  root.appendChild(svg("text",{x:m.l+iw/2,y:H-0,"text-anchor":"middle"}, "Relevance →"));
  // jitter identical cells deterministically so dots never hide each other
  const cells = new Map();
  list.filter(e => e.score).forEach(e => { const k=e.stage+"|"+e.score; if(!cells.has(k)) cells.set(k,[]); cells.get(k).push(e); });
  for (const [k, items] of cells){
    items.forEach((e,i) => {
      const n=items.length, cols=Math.ceil(Math.sqrt(n)), row=Math.floor(i/cols), col=i%cols;
      const dx=(col-(cols-1)/2)*16, dy=(row-(Math.ceil(n/cols)-1)/2)*16;
      const papers = e.research && e.research.topic_works || 0;
      const r = 6 + Math.min(6, Math.sqrt(papers));
      const g = svg("g",{style:"cursor:pointer",tabindex:"0","aria-label":e.name});
      g.appendChild(svg("circle",{cx:x(e.score)+dx,cy:y(e.stage)+dy,r:r+8,fill:"transparent"}));
      g.appendChild(svg("circle",{cx:x(e.score)+dx,cy:y(e.stage)+dy,r,fill:groupColor(e.group),stroke:"var(--surface)","stroke-width":2,opacity:.92}));
      g.addEventListener("click",()=>openDrawer(e)); g.addEventListener("keydown",ev=>{ if(ev.key==="Enter") openDrawer(e); });
      hover(g, [e.name, `${e.type} · ${e.country||"?"}`, `Stage ${e.stage} · relevance ${e.score}/5`, papers?`${papers} topic papers`:""].filter(Boolean));
      root.appendChild(g);
    });
  }
  const unscored = list.filter(e=>!e.score).length;
  const wrap = h("div",{}, root);
  if (unscored) wrap.appendChild(h("div",{class:"note",text:`${unscored} entities without a relevance score are not shown.`}));
  return wrap;
}

/* ── Entities ── */
function opts(sel, label, values){ sel.replaceChildren(h("option",{value:"",text:label}), ...values.map(v => h("option",{value:v,text:v}))); }
const uniq = (key) => [...new Set(E.map(e => e[key]).filter(Boolean))].sort();
opts(document.getElementById("fType"), "All types", uniq("type"));
opts(document.getElementById("fStage"), "All stages", STAGES.filter(s => E.some(e=>e.stage===s)));
opts(document.getElementById("fCountry"), "All countries", uniq("country"));
function syncFilters(){
  document.getElementById("q").value = state.q; document.getElementById("fType").value = state.type;
  document.getElementById("fStage").value = state.stage; document.getElementById("fCountry").value = state.country;
  document.getElementById("fScore").value = String(state.minScore); document.getElementById("fFlagged").checked = state.flagged;
}
[["q","input","q"],["fType","change","type"],["fStage","change","stage"],["fCountry","change","country"]].forEach(([id,ev,key]) =>
  document.getElementById(id).addEventListener(ev, e => { state[key] = e.target.value; renderEntities(); }));
document.getElementById("fScore").addEventListener("change", e => { state.minScore = +e.target.value; renderEntities(); });
document.getElementById("fFlagged").addEventListener("change", e => { state.flagged = e.target.checked; renderEntities(); });
document.getElementById("vTable").addEventListener("click", () => setView("table"));
document.getElementById("vCards").addEventListener("click", () => setView("cards"));
function setView(v){ state.view=v; document.getElementById("vTable").setAttribute("aria-pressed", v==="table"); document.getElementById("vCards").setAttribute("aria-pressed", v==="cards"); renderEntities(); }

function haystack(e){ return [e.name,e.official_name,e.type,e.country,e.city,e.tech,e.application,e.summary,e.business_model,e.funding,
  (e.tags||[]).join(" "),(e.people||[]).map(p=>p.name+" "+(p.role||"")).join(" "),(e.partners||[]).join(" ")].join(" ").toLowerCase(); }
function filtered(){
  const q = state.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let list = E.filter(e => (state.flagged || !e.flag) && (!state.type || e.type===state.type) && (!state.stage || e.stage===state.stage)
    && (!state.country || e.country===state.country) && (e.score||0) >= state.minScore && q.every(t => haystack(e).includes(t)));
  const key = { name:e=>e.name.toLowerCase(), score:e=>e.score||0, stage:e=>STAGES.indexOf(e.stage), type:e=>e.type, country:e=>e.country||"~",
                papers:e=>(e.research&&e.research.topic_works)||0, position:e=>e.position }[state.sort] || (e=>e.score||0);
  list.sort((a,b) => { const A=key(a), B=key(b); return (A<B?-1:A>B?1:0)*state.dir || (b.score||0)-(a.score||0); });
  return list;
}
function sparkline(series){
  if (!series || !series.length) return h("span",{class:"muted",text:"—"});
  const W=88, H=24, max=Math.max(1,...series.map(s=>s.count)), bw=W/series.length;
  const root = svg("svg",{width:W,height:H,viewBox:`0 0 ${W} ${H}`,"aria-hidden":"true"});
  series.forEach((s,i) => { const bh = s.count ? Math.max(2, s.count/max*(H-2)) : 0; const r = svg("rect",{x:i*bw+1,y:H-bh,width:Math.max(1,bw-2),height:bh,rx:1.5,fill:"var(--seq)"}); root.appendChild(r); });
  root.appendChild(svg("line",{x1:0,x2:W,y1:H-.5,y2:H-.5,stroke:"var(--border-strong)"}));
  return hover(h("span",{style:"display:inline-block"}, root), () => ["Topic papers per year", ...series.filter(s=>s.count).map(s=>`${s.year}: ${s.count}`)]);
}
function compareBox(e){
  const cb = h("input",{type:"checkbox","aria-label":"Select for comparison",title:"Compare"});
  cb.checked = state.compare.has(e.name);
  cb.addEventListener("click", ev => { ev.stopPropagation(); toggleCompare(e.name, cb.checked); });
  return cb;
}
function toggleCompare(name, on){
  if (on){ if (state.compare.size >= 4){ alert("You can compare up to 4 entities."); renderEntities(); return; } state.compare.add(name); }
  else state.compare.delete(name);
  updateCompareTab();
}
function badges(e){
  return [h("span",{class:"chip solid",text:e.type}), h("span",{class:"chip",text:e.stage}),
    e.position && e.position!=="Unknown" ? h("span",{class:"chip",text:e.position}) : null,
    e.discovered ? h("span",{class:"chip new",text:"discovered"}) : null,
    e.flag ? h("span",{class:"chip bad",text:"⚠ "+e.flag.slice(0,40)}) : null,
    e.evidence==="Low" && !e.flag ? h("span",{class:"chip warn",text:"thin evidence"}) : null];
}
function renderEntities(){
  const list = filtered();
  document.getElementById("resultCount").textContent = `${list.length} of ${E.length} entities` + (state.compare.size?` · ${state.compare.size} selected for comparison`:"");
  const view = document.getElementById("entityView");
  if (!list.length){ view.replaceChildren(h("div",{class:"card empty",text:"No entities match these filters."})); return; }
  if (state.view === "cards"){
    view.replaceChildren(h("div",{class:"grid cards"}, list.map(e => h("div",{class:"card ecard",onclick:()=>openDrawer(e)},
      h("div",{class:"row"}, h("div",{}, h("div",{class:"nm",text:e.name}), h("div",{class:"small muted",text:[e.city,e.country].filter(Boolean).join(", ")||"—"})), h("div",{style:"display:flex;gap:8px;align-items:center"}, scoreDots(e.score), compareBox(e))),
      h("div",{class:"chips"}, badges(e)), h("p",{text:e.summary||"No summary."}),
      e.research && e.research.topic_works ? h("div",{class:"small muted"}, sparkline(e.research.series), ` ${e.research.topic_works} topic papers`) : null))));
    return;
  }
  const cols = [["","",true],["name","Organisation"],["type","Type"],["country","Country"],["stage","Stage"],["position","Position"],["score","Relevance"],["papers","Research"],["","Summary",true]];
  const thead = h("thead",{}, h("tr",{}, cols.map(([key,label,nosort]) => {
    const th = h("th",{class:nosort?"nosort":"",scope:"col"}, label, key && state.sort===key ? h("span",{class:"arrow",text:state.dir<0?"↓":"↑"}) : null);
    if (!nosort) th.addEventListener("click", () => { if (state.sort===key) state.dir*=-1; else { state.sort=key; state.dir = ["name","country","type"].includes(key)?1:-1; } renderEntities(); });
    return th; })));
  const tbody = h("tbody",{}, list.map(e => h("tr",{class:e.flag?"flagged":"",onclick:()=>openDrawer(e)},
    h("td",{}, compareBox(e)),
    h("td",{class:"name"}, h("div",{class:"n",text:e.name}), h("div",{class:"sub",text:host(e.website)||""}), e.discovered?h("span",{class:"chip new small",text:"discovered"}):null, e.flag?h("span",{class:"chip bad small",text:"⚠ flagged"}):null),
    h("td",{text:e.type}), h("td",{text:dash(e.country)}), h("td",{text:e.stage}), h("td",{text:e.position}),
    h("td",{}, scoreDots(e.score)),
    h("td",{}, e.research ? h("div",{}, sparkline(e.research.series), h("div",{class:"small muted",text:`${e.research.topic_works||0} papers`})) : h("span",{class:"muted",text:"—"})),
    h("td",{class:"sum",text:e.summary||"—"}))));
  view.replaceChildren(h("div",{class:"table-wrap"}, h("table",{}, thead, tbody)));
}

/* ── Drawer ── */
const drawer = document.getElementById("drawer"), backdrop = document.getElementById("backdrop");
let lastFocus = null;
function closeDrawer(){ drawer.classList.remove("open"); backdrop.classList.remove("open"); drawer.setAttribute("aria-hidden","true"); if (lastFocus) lastFocus.focus(); }
backdrop.addEventListener("click", closeDrawer);
document.addEventListener("keydown", ev => { if (ev.key === "Escape") closeDrawer(); });
function section(title, ...kids){ const content = kids.flat().filter(Boolean); return content.length ? h("div",{}, h("h3",{text:title}), content) : null; }
function openDrawer(e){
  if (!e) return;
  hideTip();
  lastFocus = document.activeElement;
  const r = e.research;
  const closeBtn = h("button",{class:"btn",onclick:closeDrawer,"aria-label":"Close",text:"✕"});
  drawer.replaceChildren(
    h("div",{class:"drawer-head"}, h("div",{style:"flex:1"}, h("h2",{text:e.name}),
      e.official_name && e.official_name!==e.name ? h("div",{class:"small muted",text:e.official_name}) : null,
      h("div",{class:"chips",style:"margin-top:8px"}, badges(e))), closeBtn),
    h("div",{class:"drawer-body"},
      h("div",{class:"facts"},
        fact("Relevance", h("span",{}, scoreDots(e.score), " ", e.score?`${e.score}/5`:"")),
        fact("Location", [e.city,e.country].filter(Boolean).join(", ")||"—"),
        fact("Founded", dash(e.founded)),
        fact("Website", e.website ? ext(e.website, host(e.website)) : "—"),
        fact("Evidence", e.evidence)),
      e.summary ? h("div",{class:"callout",text:e.summary}) : null,
      e.rationale ? h("div",{class:"small muted",text:"Relevance rationale: "+e.rationale + (e.score_first && e.score_first!==e.score ? ` (website-only first pass: ${e.score_first}/5, revised with publication and news evidence)` : "")}) : null,
      e.flag ? h("div",{class:"callout",style:"background:var(--bad-soft);color:var(--bad)",text:"⚠ "+e.flag}) : null,
      h("div",{class:"kv"},
        kv("Technology", e.tech), kv(D.project.domain_label || "Application", e.application),
        kv("Business model", e.business_model), kv("Funding / status", e.funding),
        kv("Partners", (e.partners||[]).join(", ")), kv("Tags", (e.tags||[]).join(", "))),
      section("Strengths & risks", (e.strengths.length||e.risks.length) ? h("div",{class:"grid",style:"grid-template-columns:1fr 1fr;gap:12px"},
        h("div",{}, h("div",{class:"small muted",text:"Strengths"}), h("ul",{class:"dots"}, e.strengths.map(s=>h("li",{text:s})))),
        h("div",{}, h("div",{class:"small muted",text:"Risks"}), h("ul",{class:"dots"}, e.risks.map(s=>h("li",{text:s}))))) : null),
      section("Recent developments", (e.developments||[]).length ? h("div",{class:"timeline"}, e.developments.map(d =>
        h("div",{}, h("span",{class:"small muted",text:(d.date||"undated")+" · "}), d.event, safeUrl(d.source_url)?h("span",{}," ",ext(d.source_url,"source")):null))) : null),
      section("Research profile", r ? h("div",{},
        h("div",{class:"small"}, "Matched to ", r.institution_url ? ext(r.institution_url, r.institution) : r.institution, ` · ${r.topic_works||0} topic papers`,
          r.momentum!=null ? ` · 3-yr momentum ${r.momentum>0?"+":""}${Math.round(r.momentum*100)}%` : ""),
        h("div",{style:"margin-top:8px"}, bigSeries(r.series)),
        h("div",{class:"note",text:"Institution-level match: for a lab inside a large university, counts cover the whole university's topic papers."})) : null),
      section("Key people", (e.people||[]).length ? h("ul",{class:"clean"}, e.people.map(p => h("li",{}, p.url ? ext(p.url, p.name) : h("strong",{text:p.name}), p.role ? h("span",{class:"muted",text:" — "+p.role}) : null))) : null),
      section("Notable outputs", (e.outputs||[]).length ? h("ul",{class:"clean"}, e.outputs.map(o => h("li",{},
        h("span",{class:"chip solid",style:"margin-right:6px",text:o.type||"other"}), safeUrl(o.url) ? ext(o.url, o.title) : h("span",{text:o.title}),
        o.year ? h("span",{class:"muted",text:` (${o.year})`}) : null, o.cited_by ? h("span",{class:"muted small",text:` · cited ${o.cited_by}×`}) : null,
        o.link_kind==="search" ? h("span",{class:"muted small",text:" · search link"}) : null))) : null),
      section("Sources", (e.sources||[]).length ? h("ul",{class:"dots small"}, e.sources.map(s => h("li",{}, ext(s.url, s.title || host(s.url)), s.kind ? h("span",{class:"muted",text:` (${s.kind})`}) : null, s.ok===false ? h("span",{class:"muted",text:" — fetch failed"}) : null))) : null),
      h("div",{style:"margin-top:18px;display:flex;gap:8px"},
        h("button",{class:"btn",text:state.compare.has(e.name)?"Remove from comparison":"Add to comparison",onclick:ev=>{ toggleCompare(e.name, !state.compare.has(e.name)); ev.target.textContent = state.compare.has(e.name)?"Remove from comparison":"Add to comparison"; renderEntities(); }})),
      e.updated ? h("div",{class:"note",text:"Data updated "+e.updated.replace("T"," ")}) : null));
  drawer.classList.add("open"); backdrop.classList.add("open"); drawer.setAttribute("aria-hidden","false"); closeBtn.focus();
}
function fact(k, v){ return h("div",{class:"fact"}, h("div",{class:"k",text:k}), h("div",{class:"v"}, typeof v === "string" ? h("span",{text:v}) : v)); }
function kv(k, v){ return v ? [h("div",{class:"k",text:k}), h("div",{text:v})] : null; }
function bigSeries(series){
  if (!series || !series.length) return null;
  const W=640, H=140, m={l:28,r:8,t:10,b:22}, iw=W-m.l-m.r, ih=H-m.t-m.b;
  const max = Math.max(1, ...series.map(s=>s.count)), bw = iw/series.length;
  const root = svg("svg",{viewBox:`0 0 ${W} ${H}`,width:"100%",role:"img","aria-label":"Topic papers per year"});
  [0, max].forEach(v => { const yy = m.t+ih-(v/max)*ih; root.appendChild(svg("line",{x1:m.l,x2:W-m.r,y1:yy,y2:yy,stroke:"var(--border)"})); root.appendChild(svg("text",{x:m.l-6,y:yy+4,"text-anchor":"end"}, String(v))); });
  series.forEach((s,i) => {
    const bh = s.count ? Math.max(2,(s.count/max)*ih) : 0, xx = m.l+i*bw+2;
    const g = svg("g",{});
    g.appendChild(svg("rect",{x:m.l+i*bw,y:m.t,width:bw,height:ih,fill:"transparent"}));
    g.appendChild(svg("rect",{x:xx,y:m.t+ih-bh,width:Math.max(2,bw-4),height:bh,rx:4,fill:"var(--seq)"}));
    if (i%2===0 || series.length<=8) root.appendChild(svg("text",{x:xx+(bw-4)/2,y:H-6,"text-anchor":"middle"}, String(s.year)));
    hover(g, [String(s.year), `${s.count} topic papers`]);
    root.appendChild(g);
  });
  return root;
}

/* ── Compare ── */
function renderCompare(){
  const sec = document.getElementById("tab-compare");
  const items = [...state.compare].map(n => byName.get(n)).filter(Boolean);
  if (!items.length){ sec.replaceChildren(h("div",{class:"card empty"}, "Select up to 4 entities with the checkboxes in the Entities tab (or from a profile) to compare them side by side.")); return; }
  const rows = [
    ["Type", e=>e.type], ["Country", e=>dash(e.country)], ["Stage", e=>e.stage], ["Position", e=>e.position],
    ["Relevance", e=>scoreDots(e.score)], ["Summary", e=>dash(e.summary)], ["Technology", e=>dash(e.tech)],
    [D.project.domain_label||"Application", e=>dash(e.application)], ["Business model", e=>dash(e.business_model)],
    ["Funding / status", e=>dash(e.funding)], ["Topic papers", e=>e.research ? h("div",{}, sparkline(e.research.series), " "+(e.research.topic_works||0)) : "—"],
    ["Strengths", e=>e.strengths.length ? h("ul",{class:"dots"}, e.strengths.map(s=>h("li",{text:s}))) : "—"],
    ["Risks", e=>e.risks.length ? h("ul",{class:"dots"}, e.risks.map(s=>h("li",{text:s}))) : "—"],
    ["Key people", e=>(e.people||[]).map(p=>p.name).join(", ")||"—"],
  ];
  sec.replaceChildren(h("div",{class:"table-wrap"}, h("table",{class:"compare-table"},
    h("thead",{}, h("tr",{}, h("th",{}), items.map(e => h("th",{}, h("button",{class:"linkish",text:e.name,onclick:()=>openDrawer(e)}), " ",
      h("button",{class:"btn small",style:"padding:0 6px;margin-left:4px",title:"Remove",text:"✕",onclick:()=>{ state.compare.delete(e.name); updateCompareTab(); renderCompare(); renderEntities(); }}))))),
    h("tbody",{}, rows.map(([label, fn]) => h("tr",{style:"cursor:default"}, h("th",{scope:"row",style:"position:static",text:label}), items.map(e => { const v = fn(e); return h("td",{}, typeof v === "string" ? h("span",{text:v}) : v); })))))));
}

/* ── Method ── */
function renderMethod(){
  const sec = document.getElementById("tab-method");
  const flagged = E.filter(e => e.flag);
  const thin = E.filter(e => !e.flag && e.evidence === "Low");
  const nSources = E.reduce((a,e)=>a+(e.sources||[]).length,0);
  sec.appendChild(h("div",{class:"grid two"},
    h("div",{class:"card"}, h("h2",{text:"How this report was built"}),
      h("ul",{class:"dots"},
        h("li",{text:"Scraping: each organisation's website (homepage plus about / team / research / product pages); homepages are searched when no URL is given."}),
        h("li",{text:`Analysis: one structured extraction per organisation by ${D.model}, constrained to a JSON schema and instructed to use only source text.`}),
        h("li",{text:"Enrichment: OpenAlex publication data (topic-filtered paper counts, top-cited papers with DOIs, most active authors) and a news / funding web pass."}),
        h("li",{text:"Synthesis: one cross-entity pass producing the executive summary, segments, trends and white spaces; organisation names are validated against the mapped list."}),
        h("li",{text:"Discovery (when enabled): bibliometric institution ranking plus web affiliation extraction, verified and relevance-checked."})),
      h("p",{class:"note",text:`${nSources} sources consulted. LLM output can still contain errors — use the Sources list in each profile to verify claims before relying on them.`})),
    h("div",{class:"card"}, h("h2",{text:"Data quality"}),
      h("div",{class:"kv"}, kv("Flagged entities", String(flagged.length)), kv("Thin evidence", String(thin.length)), kv("Discovered", String(E.filter(e=>e.discovered).length))),
      flagged.length ? section("Flagged", h("ul",{class:"clean small"}, flagged.map(e => h("li",{}, h("button",{class:"linkish",text:e.name,onclick:()=>openDrawer(e)}), h("div",{class:"muted",text:e.flag}))))) : null,
      thin.length ? section("Thin evidence — consider adding URLs or notes", h("div",{class:"chips"}, thin.map(e => h("button",{class:"chip",text:e.name,onclick:()=>openDrawer(e)})))) : null)));
}

/* ── Export & theme ── */
function download(name, mime, text){ const a = h("a",{href:URL.createObjectURL(new Blob([text],{type:mime})),download:name}); document.body.appendChild(a); a.click(); setTimeout(()=>{URL.revokeObjectURL(a.href); a.remove();},0); }
const csvCell = v => { const s = v==null ? "" : String(v); return /[",\n]/.test(s) ? '"'+s.replace(/"/g,'""')+'"' : s; };
document.getElementById("csvBtn").addEventListener("click", () => {
  const list = filtered();
  const cols = [["name","Name"],["type","Type"],["country","Country"],["stage","Stage"],["position","Position"],["score","Relevance"],["summary","Summary"],["tech","Technology"],["application","Application"],["funding","Funding"],["website","Website"]];
  const lines = [cols.map(c=>c[1]).join(",")].concat(list.map(e => cols.map(c=>csvCell(e[c[0]])).concat([]).join(",")));
  download(`${D.project.slug}_entities.csv`, "text/csv", "﻿"+lines.join("\n"));
});
document.getElementById("jsonBtn").addEventListener("click", () => download(`${D.project.slug}_report.json`, "application/json", JSON.stringify(D, null, 2)));
const themeBtn = document.getElementById("themeBtn");
function applyTheme(t){ if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme"); }
try { applyTheme(localStorage.getItem("ci-theme")); } catch(e){}
themeBtn.addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark"; applyTheme(next); try { localStorage.setItem("ci-theme", next); } catch(e){}
});

/* ── boot ── */
renderOverview(); renderLandscape(); renderEntities(); renderMethod();
const initial = (location.hash||"").slice(1);
selectTab(TABS.some(t=>t[0]===initial) ? initial : "overview");
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import sys
    proj = Project.from_csv(sys.argv[1]) if len(sys.argv) > 1 else Project.from_config()
    from project import read_json
    recs = proj.load_results()
    print(generate_report(proj, recs, landscape=read_json(proj.landscape_path)))
