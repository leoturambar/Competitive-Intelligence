# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — app_ui.py
#  Streamlit UI — set up projects, run the pipeline, explore results
#  Run with: streamlit run app_ui.py
# ─────────────────────────────────────────────

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# All data paths are relative to the project root.
os.chdir(Path(__file__).resolve().parent)


import config
from csv_loader import load_csv, write_csv
from reporter import _country as reporter_country
from project import INPUT_DIR, Project, list_projects, read_json, slugify

st.set_page_config(page_title="Competitive Intelligence", page_icon="🔭", layout="wide")

st.markdown("""
<style>
.block-container{padding-top:2rem;max-width:1400px}
.ci-eyebrow{font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:#7a7974;font-weight:600;margin-bottom:-.4rem}
.ci-step{display:flex;gap:.6rem;align-items:center;padding:.35rem 0;font-size:.92rem}
.ci-step .dot{width:.7rem;height:.7rem;border-radius:50%;display:inline-block;flex-shrink:0}
.ci-step .lbl{font-weight:600;min-width:6.5rem}
.ci-step .det{color:#7a7974;font-size:.82rem}
div[data-testid="stMetricValue"]{font-size:1.7rem}
</style>
""", unsafe_allow_html=True)

STEP_LABELS = {"discovery": "Discovery", "scraping": "Scraping", "analysis": "Analysis",
               "enrichment": "Enrichment", "landscape": "Synthesis", "report": "Report"}
STATE_COLORS = {"pending": "#c8c7c1", "running": "#2a78d6", "done": "#14804a", "error": "#c43c3b"}

for key, default in [("proc", None), ("proc_project", None)]:
    st.session_state.setdefault(key, default)


# ── Helpers ───────────────────────────────────

def load_project(path: str | None) -> Project:
    return Project.from_csv(path) if path else Project.from_config()


def run_active(project: Project) -> bool:
    """True if this session's subprocess is running for the project."""
    proc = st.session_state.proc
    return bool(proc and proc.poll() is None and st.session_state.proc_project == project.slug)


def external_run(project: Project) -> bool:
    """A run started elsewhere (CLI, another tab) that is still updating its status file."""
    status = read_json(project.status_path) or {}
    if status.get("state") != "running" or st.session_state.proc_project == project.slug:
        return False
    try:
        age = (datetime.now() - datetime.fromisoformat(status.get("ts") or status["started"])).total_seconds()
    except (KeyError, ValueError):
        return False
    return age < 900


def latest_report(project: Project) -> Path | None:
    d = project.reports_dir
    if not d.exists():
        return None
    reports = sorted(d.glob("*.html"), key=lambda p: p.stat().st_mtime)
    return reports[-1] if reports else None


def records_frame(records: list[dict]) -> pd.DataFrame:
    rows = []
    for r in records:
        res = r.get("research") or {}
        rows.append({
            "Organisation": r.get("entity_name"),
            "Type": r.get("entity_type"),
            "Country": reporter_country(r.get("country")),
            "Stage": r.get("development_stage"),
            "Position": r.get("competitive_position"),
            "Relevance": r.get("relevance_score") or 0,
            "Topic papers": res.get("topic_works") or 0,
            "Papers / year": [s["count"] for s in res.get("series", [])] or None,
            "Evidence": r.get("evidence_quality"),
            "Website": r.get("website") or (r.get("entity_urls") or [None])[0],
            "Summary": r.get("summary"),
            "Flag": ("not an organisation" if r.get("is_organization") is False else r.get("_error")) or "",
            "Discovered": bool(r.get("discovered")),
        })
    df = pd.DataFrame(rows)
    for col in ("Type", "Country", "Stage", "Position", "Evidence", "Summary"):
        df[col] = df[col].fillna("—")
    return df


# ── Sidebar: project + backend ────────────────

with st.sidebar:
    st.markdown("### 🔭 Competitive Intelligence")
    csv_files = list_projects()
    options = [str(p) for p in csv_files]
    labels = {str(p): load_csv(p)["topic"] or p.stem for p in csv_files}
    default_idx = 0
    if st.session_state.get("select_project") in options:
        default_idx = options.index(st.session_state.pop("select_project"))
    selected = st.selectbox("Project", options, index=default_idx if options else None,
                            format_func=lambda p: f"{labels.get(p, p).strip().title()}",
                            placeholder="No projects yet — create one below")

    with st.expander("➕ New project"):
        with st.form("new_project", clear_on_submit=True):
            n_topic = st.text_input("Topic", placeholder="e.g. solid-state batteries for EVs")
            n_desc = st.text_area("Scope description", placeholder="Which organisations count as part of this landscape?")
            n_label = st.text_input("Domain-application label", value="Domain application")
            n_kw = st.text_input("Topic keywords (| separated)", help="Used to verify discovered candidates")
            n_q = st.text_input("Discovery queries (| separated)", help="Optional — defaults are generated from the topic")
            n_seed = st.text_area("Seed entities (one per line: Name, URL)", height=100)
            if st.form_submit_button("Create project", type="primary") and n_topic.strip():
                ents = []
                for line in n_seed.splitlines():
                    if not line.strip():
                        continue
                    name, _, url = line.partition(",")
                    ents.append({"name": name.strip(), "urls": [url.strip()] if url.strip() else [], "notes": ""})
                path = INPUT_DIR / f"entities_{slugify(n_topic, 40)}.csv"
                if path.exists():
                    st.error(f"{path.name} already exists.")
                else:
                    write_csv({"topic": n_topic.strip(), "description": n_desc.strip(), "domain_label": n_label.strip(),
                               "topic_keywords": [k.strip() for k in n_kw.split("|") if k.strip()],
                               "discovery_queries": [q.strip() for q in n_q.split("|") if q.strip()],
                               "entities": ents}, path)
                    st.session_state.select_project = str(path)
                    st.rerun()

    st.divider()
    st.markdown("**LLM backend**")
    backend = st.radio("Backend", ["ollama", "claude"], horizontal=True, label_visibility="collapsed",
                       index=0 if config.LLM_BACKEND == "ollama" else 1,
                       help="Ollama = local and free. Claude = Anthropic API (better judgment on messy sources, small cost).")
    model = st.text_input("Model", value=config.OLLAMA_MODEL if backend == "ollama" else config.CLAUDE_MODEL)
    os.environ["CI_LLM_BACKEND"] = backend
    os.environ["CI_OLLAMA_MODEL" if backend == "ollama" else "CI_CLAUDE_MODEL"] = model
    import llm
    ok, msg = llm.check_backend()
    (st.success if ok else st.error)(msg, icon="✅" if ok else "⚠️")
    st.caption(("🔎 Brave Search API configured" if os.environ.get("BRAVE_SEARCH_API_KEY")
                else "🔎 Web search: DuckDuckGo (set BRAVE_SEARCH_API_KEY for a more reliable engine)"))

if not selected:
    st.title("Competitive Intelligence")
    st.info("Create a project in the sidebar to get started — a topic, a scope description and a few seed organisations are enough.")
    st.stop()

project = load_project(selected)
records = project.load_results()
landscape = read_json(project.landscape_path, {}) or {}
report = latest_report(project)

st.markdown(f'<p class="ci-eyebrow">Project · {project.slug}</p>', unsafe_allow_html=True)
st.title(project.topic.strip().title())
if project.description:
    st.caption(project.description)

tab_results, tab_run, tab_setup, tab_disc = st.tabs(["📊 Results", "▶️ Run", "🧩 Entities & setup", "🛰️ Discovery"])


# ── Results ───────────────────────────────────

with tab_results:
    if not records:
        st.info("No results yet for this project. Go to **Run** to start the pipeline.")
    else:
        df = records_frame(records)
        valid = df[df["Flag"] == ""]
        stats = landscape.get("stats") or {}
        c = st.columns(6)
        c[0].metric("Organisations", len(valid))
        c[1].metric("Core players", int((valid["Relevance"] >= 4).sum()), help="Relevance ≥ 4")
        c[2].metric("Companies", int(valid["Type"].isin(["Company", "Spinoff"]).sum()))
        c[3].metric("Academic", int(valid["Type"].isin(["University", "Research Institute"]).sum()))
        c[4].metric("Pilot / commercial", int(valid["Stage"].isin(["Pilot", "Commercial"]).sum()))
        c[5].metric("Topic papers", f"{int(valid['Topic papers'].sum()):,}")

        left, right = st.columns([3, 2], gap="large")
        with left:
            st.subheader("Executive summary")
            st.write(landscape.get("executive_summary") or "_Synthesis not available — run the pipeline with the landscape step._")
            if landscape.get("white_spaces"):
                st.markdown("**White spaces**")
                st.markdown("\n".join(f"- {w}" for w in landscape["white_spaces"]))
        with right:
            if landscape.get("key_players"):
                st.subheader("Key players")
                for kp in landscape["key_players"]:
                    st.markdown(f"**{kp['name']}** — {kp['why']}")
            if landscape.get("trends"):
                st.markdown("**Trends**")
                st.markdown("\n".join(f"- {t}" for t in landscape["trends"]))

        st.subheader("Entities")
        f1, f2, f3, f4 = st.columns([2, 1, 1, 1])
        q = f1.text_input("Search", placeholder="name, summary…", label_visibility="collapsed")
        types = f2.multiselect("Type", sorted(df["Type"].dropna().unique()), placeholder="All types", label_visibility="collapsed")
        stages = f3.multiselect("Stage", sorted(df["Stage"].dropna().unique()), placeholder="All stages", label_visibility="collapsed")
        show_flagged = f4.toggle("Show flagged", value=False)
        view = df.copy()
        if q:
            mask = view["Organisation"].str.contains(q, case=False, na=False) | view["Summary"].str.contains(q, case=False, na=False)
            view = view[mask]
        if types:
            view = view[view["Type"].isin(types)]
        if stages:
            view = view[view["Stage"].isin(stages)]
        if not show_flagged:
            view = view[view["Flag"] == ""]
        view = view.sort_values(["Relevance", "Topic papers"], ascending=False).reset_index(drop=True)

        event = st.dataframe(
            view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
            column_order=["Organisation", "Type", "Country", "Stage", "Position", "Relevance", "Topic papers",
                          "Papers / year", "Evidence", "Website", "Flag"],
            column_config={
                "Relevance": st.column_config.ProgressColumn("Relevance", min_value=0, max_value=5, format="%d / 5"),
                "Papers / year": st.column_config.BarChartColumn("Papers / year", y_min=0),
                "Website": st.column_config.LinkColumn("Website", display_text=r"https?://(?:www\.)?([^/]+)"),
                "Topic papers": st.column_config.NumberColumn("Topic papers", format="%d"),
            })
        rows = event.selection.rows if event and event.selection else []
        if rows:
            name = view.iloc[rows[0]]["Organisation"]
            rec = next(r for r in records if r.get("entity_name") == name)
            with st.container(border=True):
                st.markdown(f"### {name}")
                st.caption(" · ".join(filter(None, [rec.get("entity_type"), rec.get("country"), rec.get("development_stage"),
                                                    rec.get("competitive_position"), f"evidence: {rec.get('evidence_quality')}"])))
                if rec.get("summary"):
                    st.info(rec["summary"])
                d1, d2 = st.columns(2)
                with d1:
                    for label, key in [("Technology", "technology_focus"), (project.domain_label, "domain_application"),
                                       ("Business model", "business_model"), ("Funding / status", "funding_or_status")]:
                        if rec.get(key):
                            st.markdown(f"**{label}** — {rec[key]}")
                    if rec.get("strengths") or rec.get("risks"):
                        st.markdown("**Strengths** — " + "; ".join(rec.get("strengths") or ["—"]))
                        st.markdown("**Risks** — " + "; ".join(rec.get("risks") or ["—"]))
                with d2:
                    people = rec.get("key_people") or []
                    if people:
                        st.markdown("**Key people**")
                        st.markdown("\n".join(f"- {p['name']}" + (f" — {p['role']}" if p.get("role") else "")
                                              for p in people if isinstance(p, dict)))
                    outs = rec.get("notable_outputs") or []
                    if outs:
                        st.markdown("**Notable outputs**")
                        st.markdown("\n".join(
                            f"- {o.get('type', '')}: " + (f"[{o['title']}]({o['url']})" if o.get("url") else o.get("title", ""))
                            + (f" ({o['year']})" if o.get("year") else "") for o in outs if isinstance(o, dict)))
                for dev in rec.get("recent_developments") or []:
                    st.markdown(f"🗓️ **{dev.get('date') or 'undated'}** — {dev['event']}")
                res = rec.get("research") or {}
                if res.get("series"):
                    st.markdown(f"**Topic papers per year** · matched to {res['institution']['display_name']} (OpenAlex)")
                    st.bar_chart(pd.DataFrame(res["series"]).set_index("year"), height=180, color="#2a78d6")
                if rec.get("sources"):
                    with st.expander(f"Sources ({len(rec['sources'])})"):
                        for s in rec["sources"]:
                            if s.get("url"):
                                st.markdown(f"- [{s.get('title') or s['url']}]({s['url']})" + ("" if s.get("ok", True) else " — _fetch failed_"))

        st.divider()
        if report:
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("📄 Open report in browser", type="primary", width="stretch"):
                webbrowser.open(report.resolve().as_uri())
            b2.download_button("⬇️ HTML report", report.read_bytes(), file_name=f"{project.slug}_{report.name}",
                               mime="text/html", width="stretch")
            for col, ext, mime in [(b3, ".csv", "text/csv"), (b4, ".json", "application/json")]:
                p = report.with_suffix(ext)
                if p.exists():
                    col.download_button(f"⬇️ {ext[1:].upper()} export", p.read_bytes(), file_name=f"{project.slug}{ext}",
                                        mime=mime, width="stretch")
            st.caption(f"Latest report: {report.name} · {datetime.fromtimestamp(report.stat().st_mtime):%Y-%m-%d %H:%M}")
            with st.expander("Preview the interactive report here"):
                st.components.v1.html(report.read_text(encoding="utf-8"), height=900, scrolling=True)


# ── Run ───────────────────────────────────────

def build_command(opts: dict) -> list[str]:
    cmd = [sys.executable, "main.py", "--entities", selected, "--backend", backend, "--model", model]
    if opts["discover"]:
        cmd.append("--discover")
    for step in opts["force"]:
        cmd.append(f"--force-{step}")
    if opts["skip_scrape"]:
        cmd.append("--skip-scrape")
    if not opts["enrich"]:
        cmd.append("--skip-enrich")
    if not opts["landscape"]:
        cmd.append("--skip-landscape")
    if not opts["openalex"]:
        cmd.append("--no-openalex")
    if not opts["web_enrich"]:
        cmd.append("--no-web-enrich")
    for name in opts["only"]:
        cmd += ["--only", name]
    if opts["report_only"]:
        cmd.append("--report-only")
    return cmd


with tab_run:
    active = run_active(project) or external_run(project)
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**What to run**")
            report_only = st.toggle("Only rebuild the report", help="Uses cached results; no network, no LLM except synthesis")
            discover = st.toggle("Discover new entities", disabled=report_only,
                                 help="OpenAlex bibliometrics + web affiliation mining")
            enrich = st.toggle("Enrichment", value=True, disabled=report_only)
            land = st.toggle("Landscape synthesis", value=True)
        with c2:
            st.markdown("**Sources**")
            openalex_on = st.toggle("OpenAlex research data", value=True, disabled=report_only)
            web_enrich = st.toggle("News / funding web pass", value=True, disabled=report_only or not enrich)
            skip_scrape = st.toggle("Offline (cached pages only)", disabled=report_only)
        with c3:
            st.markdown("**Scope & cache**")
            all_names = [e["name"] for e in project.all_entities(include_discovered=True)]
            only = st.multiselect("Only these entities", all_names, placeholder="All entities", disabled=report_only)
            force = st.multiselect("Ignore cache for", ["discover", "scrape", "analyze", "enrich", "landscape"],
                                   placeholder="Nothing — reuse caches",
                                   help="Caches are invalidated automatically when inputs change; force only to refresh stale web data.")
        opts = dict(discover=discover, force=force, skip_scrape=skip_scrape, enrich=enrich, landscape=land,
                    openalex=openalex_on, web_enrich=web_enrich, only=only, report_only=report_only)
        cmd = build_command(opts)
        with st.expander("Command line"):
            st.code(" ".join(f'"{c}"' if " " in c else c for c in cmd), language="bash")

        r1, r2 = st.columns([3, 1])
        if r1.button("▶  Run analysis", type="primary", width="stretch", disabled=active or not ok):
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            st.session_state.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                     stdin=subprocess.DEVNULL, creationflags=flags)
            st.session_state.proc_project = project.slug
            st.rerun()
        if active and not run_active(project):
            st.info("A run for this project is in progress elsewhere (CLI or another tab) — progress is shown below.")
        if r2.button("■  Cancel", width="stretch", disabled=not run_active(project)):
            st.session_state.proc.terminate()
            from project import write_json
            status = read_json(project.status_path, {}) or {}
            status.update(state="error", message="Cancelled by user")
            write_json(project.status_path, status)
            st.rerun()
        if not ok:
            st.warning(f"Backend not ready: {msg}")

    @st.fragment(run_every="2s" if active else None)
    def progress_panel():
        status = read_json(project.status_path)
        if not status:
            st.caption("No run yet for this project.")
            return
        state = status.get("state")
        live = run_active(project) or external_run(project)
        if state == "running" and not live:
            age = (datetime.now() - datetime.fromisoformat(status.get("ts", status["started"]))).total_seconds()
            state = "stale" if age > 900 or st.session_state.proc_project == project.slug else "running"
        head = {"running": "⏳ Running…", "done": "✅ Completed", "error": "❌ Failed", "stale": "⚠️ Interrupted"}.get(state, state)
        st.markdown(f"#### {head}")
        st.caption(f"Started {status.get('started', '').replace('T', ' ')}" + (f" · {status['message']}" if status.get("message") else ""))
        for key, s in status.get("steps", {}).items():
            col = STATE_COLORS.get(s.get("state"), "#c8c7c1")
            total, done = s.get("total") or 0, s.get("done") or 0
            detail = s.get("detail") or ""
            count = f"{done}/{total}" if total and s.get("state") == "running" else ""
            st.markdown(f'<div class="ci-step"><span class="dot" style="background:{col}"></span>'
                        f'<span class="lbl">{STEP_LABELS.get(key, key)}</span>'
                        f'<span class="det">{count} {detail}</span></div>', unsafe_allow_html=True)
            if s.get("state") == "running" and total:
                st.progress(min(1.0, done / total))
        if project.log_path.exists():
            lines = project.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            with st.expander("Live log", expanded=state in ("running", "error", "stale")):
                st.code("\n".join(lines[-40:]) or "…", language=None)
        if state != "running" and st.session_state.get("_was_running"):
            st.session_state._was_running = False
            st.rerun(scope="app")
        if state == "running":
            st.session_state._was_running = True

    progress_panel()


# ── Entities & setup ──────────────────────────

with tab_setup:
    st.markdown("Edit the topic definition and the entity list. Saving rewrites the CSV; changed URLs or notes "
                "automatically invalidate only the affected caches.")
    with st.form("setup"):
        m1, m2 = st.columns(2)
        topic = m1.text_input("Topic", value=project.topic)
        label = m2.text_input("Domain-application label", value=project.domain_label)
        desc = st.text_area("Scope description", value=project.description, height=80)
        k1, k2 = st.columns(2)
        kws = k1.text_area("Topic keywords (one per line)", value="\n".join(project.topic_keywords), height=120)
        qs = k2.text_area("Discovery queries (one per line)", value="\n".join(project.discovery_queries), height=120)

        def cache_state(name: str) -> str:
            marks = []
            for layer, sym in (("raw", "S"), ("analyzed", "A"), ("enriched", "E")):
                rec = read_json(project.entity_path(layer, name))
                marks.append(sym if rec and not rec.get("_error") else "·")
            return " ".join(marks)

        ent_df = pd.DataFrame([{"name": e["name"], "url": " | ".join(e.get("urls") or []), "notes": e.get("notes", ""),
                                "cached": cache_state(e["name"])} for e in project.entities]
                              or [{"name": "", "url": "", "notes": "", "cached": ""}])
        edited = st.data_editor(ent_df, num_rows="dynamic", width="stretch", hide_index=True,
                                column_config={
                                    "name": st.column_config.TextColumn("Name", required=True, width="medium"),
                                    "url": st.column_config.TextColumn("URL(s)", help="Separate several with |. Leave empty to auto-search the homepage.", width="medium"),
                                    "notes": st.column_config.TextColumn("Analyst notes", width="large"),
                                    "cached": st.column_config.TextColumn("Cache", disabled=True, help="S=scraped A=analysed E=enriched"),
                                })
        if st.form_submit_button("💾 Save project", type="primary"):
            project.topic, project.domain_label, project.description = topic.strip(), label.strip(), desc.strip()
            project.topic_keywords = [k.strip() for k in kws.splitlines() if k.strip()]
            project.discovery_queries = [q.strip() for q in qs.splitlines() if q.strip()]
            ents = []
            for _, row in edited.iterrows():
                name = str(row.get("name") or "").strip()
                if name:
                    urls = [u.strip() for u in str(row.get("url") or "").split("|") if u.strip()]
                    ents.append({"name": name, "urls": urls, "notes": str(row.get("notes") or "").strip()})
            project.entities = ents
            project.save_csv()
            st.success(f"Saved {len(ents)} entities to {project.csv_path}")
            st.rerun()


# ── Discovery ─────────────────────────────────

with tab_disc:
    disc = read_json(project.discovery_path, {}) or {}
    found = disc.get("entities", [])
    if not disc:
        st.info("Discovery hasn't run for this project. Enable **Discover new entities** in the Run tab.")
    else:
        st.caption(f"Last discovery: {disc.get('ts', '').replace('T', ' ')} · {len(found)} entities proposed · "
                   f"{len(disc.get('candidates', []))} candidates examined")
        pending = [e for e in found if e.get("status", "new") == "new"]
        if pending:
            st.markdown("**Review proposed entities** — accepted ones are added to the project CSV; "
                        "dismissed ones are excluded from future runs.")
            ddf = pd.DataFrame([{"keep": True, "name": e["name"], "source": e.get("source"), "evidence": e.get("evidence"),
                                 "why": e.get("reason"), "url": (e.get("urls") or [""])[0], "notes": e.get("notes")}
                                for e in pending])
            choice = st.data_editor(ddf, hide_index=True, width="stretch", disabled=["name", "source", "evidence", "why", "url", "notes"],
                                    column_config={"keep": st.column_config.CheckboxColumn("Keep"),
                                                   "url": st.column_config.LinkColumn("URL")})
            if st.button("Apply decisions", type="primary"):
                from csv_loader import save_discovered_to_csv
                from discoverer import set_status
                keep = set(choice[choice["keep"]]["name"])
                save_discovered_to_csv([e for e in pending if e["name"] in keep], selected)
                for e in pending:
                    set_status(project, e["name"], "accepted" if e["name"] in keep else "dismissed")
                st.success(f"Added {len(keep)} entities to the project; dismissed {len(pending) - len(keep)}.")
                st.rerun()
        else:
            st.success("No proposals waiting for review.")
        reviewed = [e for e in found if e.get("status") in ("accepted", "dismissed")]
        if reviewed:
            st.markdown("**Already reviewed**")
            st.dataframe(pd.DataFrame([{"name": e["name"], "status": e["status"], "evidence": e.get("evidence")} for e in reviewed]),
                         hide_index=True, width="stretch")
        cands = disc.get("candidates", [])
        if cands:
            with st.expander(f"All candidates and decisions ({len(cands)})"):
                cdf = pd.DataFrame([{"candidate": c.get("name"), "source": c.get("source"),
                                     "decision": c.get("decision", "—"), "reason": c.get("reason"),
                                     "evidence": c.get("evidence"), "url": c.get("url")} for c in cands])
                st.dataframe(cdf, hide_index=True, width="stretch",
                             column_config={"url": st.column_config.LinkColumn("URL")})
