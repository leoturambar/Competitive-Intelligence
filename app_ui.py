# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — app_ui.py
#  Streamlit UI — configure and launch analysis
#  Run with: streamlit run app_ui.py
# ─────────────────────────────────────────────

import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Competitive Intelligence Tool",
    page_icon="🔬",
    layout="centered",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }

.ci-header {
    padding: 2rem 0 1.5rem 0;
    border-bottom: 2px solid #1a6fa8;
    margin-bottom: 2rem;
}
.ci-title {
    font-size: 1.6rem; font-weight: 600;
    color: #0d4f7c; letter-spacing: -0.02em; margin: 0;
}
.ci-subtitle {
    font-size: 0.85rem; color: #6c757d;
    font-family: 'DM Mono', monospace; margin-top: 0.3rem;
}
.section-label {
    font-size: 0.68rem; font-family: 'DM Mono', monospace;
    text-transform: uppercase; letter-spacing: 2px;
    color: #6c757d; margin-bottom: 0.8rem; margin-top: 1.5rem;
}
.milestone-panel {
    background: #f8f9fa;
    border: 1px solid #e0e4e8;
    border-radius: 8px;
    padding: 0.6rem 1.2rem;
    margin-top: 0.5rem;
}
.ms-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.45rem 0;
    font-size: 0.87rem;
    border-bottom: 1px solid #f0f0f0;
}
.ms-row:last-child { border-bottom: none; }
.ms-bullet { font-size: 0.7rem; width: 1rem; text-align: center; flex-shrink: 0; }
.ms-label  { font-weight: 600; color: #0d4f7c; min-width: 100px; }
.ms-detail { color: #6c757d; font-family: 'DM Mono', monospace; font-size: 0.76rem; }
.ms-pending .ms-bullet { color: #c8d0d8; }
.ms-running .ms-bullet { color: #1a6fa8; }
.ms-done    .ms-bullet { color: #1e8449; }
.ms-error   .ms-bullet { color: #c0392b; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="ci-header">
  <p class="ci-title">&#x1F52C; Competitive Intelligence Tool</p>
  <p class="ci-subtitle">biotech &middot; deeptech &middot; landscape analysis</p>
</div>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────
def get_csv_files():
    d = Path("data/input")
    return sorted(d.glob("*.csv")) if d.exists() else []


def find_latest_report():
    d = Path("data/reports")
    if not d.exists():
        return None
    reports = sorted(d.glob("*.html"), key=lambda p: p.stat().st_mtime)
    return reports[-1] if reports else None


def read_status() -> dict:
    p = Path("data/run_status.json")
    if not p.exists():
        return {"current": {}, "history": {}}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        # Support both old (flat) and new (nested) format
        if "current" not in data:
            return {"current": data, "history": {}}
        return data
    except Exception:
        return {"current": {}, "history": {}}


def render_milestones(steps: list, status: dict, is_running: bool = True):
    """
    steps: list of (key, label) for this run configuration.
    status: {"current": {...}, "history": {"step_key": "detail", ...}}
    is_running: False when displaying a previous (completed or aborted) run.
    """
    current  = status.get("current", {})
    history  = status.get("history", {})
    cur_step = current.get("step", "")
    is_error = current.get("error", False)
    all_done = cur_step == "done"

    rows = ""
    for key, label in steps:
        if all_done or key in history:
            # Completed step — show stored detail
            detail = history.get(key, "")
            css, bullet = "ms-done", "&#x25CF;"
        elif key == cur_step:
            detail = current.get("detail", "running...")
            if is_error:
                css, bullet = "ms-error", "&#x2717;"
            elif is_running:
                css, bullet = "ms-running", "&#x25C9;"
            else:
                # Stale "current" from a previous aborted run — show as pending
                detail, css, bullet = "", "ms-pending", "&#x25CB;"
        else:
            detail, css, bullet = "", "ms-pending", "&#x25CB;"

        rows += (
            f'<div class="ms-row {css}">'
            f'  <span class="ms-bullet">{bullet}</span>'
            f'  <span class="ms-label">{label}</span>'
            f'  <span class="ms-detail">{detail}</span>'
            f'</div>'
        )

    st.markdown(f'<div class="milestone-panel">{rows}</div>', unsafe_allow_html=True)


def get_steps(do_discover, skip_scrape, skip_enrich) -> list:
    steps = []
    if do_discover:
        steps.append(("discovery",  "Discovery"))
    if not skip_scrape:
        steps.append(("scraping",   "Scraping"))
    steps.append(("analysis",   "Analysis"))
    if not skip_enrich:
        steps.append(("enrichment", "Enrichment"))
    steps.append(("report",     "Report"))
    return steps


# ── Section 1: Input ─────────────────────────
st.markdown('<p class="section-label">Input</p>', unsafe_allow_html=True)

csv_files   = get_csv_files()
csv_options = ["— use config.py defaults —"] + [str(f) for f in csv_files]

selected_csv = st.selectbox(
    "Entity file", options=csv_options,
    help="CSV file with topic and entity list. Place files in data/input/",
)

if selected_csv != csv_options[0]:
    try:
        from csv_loader import load_csv
        preview = load_csv(selected_csv)
        st.caption(f"**Topic:** {preview['topic']}  ·  **{len(preview['entities'])} entities**")
    except Exception as e:
        st.warning(f"Could not read CSV: {e}")


# ── Section 2: LLM backend ────────────────────
st.markdown('<p class="section-label">LLM Backend</p>', unsafe_allow_html=True)

llm_backend = st.radio(
    "LLM backend", options=["ollama", "claude"], horizontal=True,
    help="Ollama = local (free, Qwen2.5:14b). Claude = API (better quality, small cost).",
    label_visibility="collapsed",
)


# ── Section 3: Pipeline steps ─────────────────
st.markdown('<p class="section-label">Pipeline steps</p>', unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    do_scrape  = st.checkbox("Scrape",  value=True, help="Fetch entity pages")
    do_analyze = st.checkbox("Analyze", value=True, help="LLM structured extraction")
with col2:
    do_enrich  = st.checkbox("Enrich",  value=True,
                              help="DDG passes for outputs, people, application")
    _do_report = st.checkbox("Report",  value=True, help="Generate HTML report")


# ── Section 4: Options ────────────────────────
st.markdown('<p class="section-label">Options</p>', unsafe_allow_html=True)

col3, col4 = st.columns(2)
with col3:
    force_all   = st.checkbox("Force full refresh", value=False,
                               help="Ignore all caches — re-run everything from scratch")
    skip_scrape = st.checkbox("Skip scraping",      value=False,
                               disabled=force_all,
                               help="Use existing raw cache (faster re-runs)")
with col4:
    do_discover    = st.checkbox("Auto-discover new entities", value=False,
                                  help="Search DDG + regex affiliations for new labs/companies")
    force_discover = st.checkbox("Force re-discover",          value=False,
                                  disabled=force_all,
                                  help="Ignore discovery cache and re-run")

# ── Enforce logic ─────────────────────────────
if force_all:
    skip_scrape    = False
    force_discover = False

if force_discover:
    do_discover = True
    skip_scrape = False

if not do_discover:
    force_discover = False

if force_discover and not force_all:
    st.info("Force re-discover: skip scraping has been disabled automatically.")


# ── Build command ─────────────────────────────
def build_command(selected_csv, csv_options, do_scrape, skip_scrape,
                  do_enrich, do_discover, force_discover, force_all) -> list:
    cmd = [sys.executable, "main.py"]

    if selected_csv != csv_options[0]:
        cmd += ["--entities", selected_csv]

    if force_all:
        cmd.append("--force")
    else:
        if not do_scrape or skip_scrape:
            cmd.append("--skip-scrape")
        if not do_enrich:
            cmd.append("--skip-enrich")

    if do_discover:
        cmd.append("--discover")
    if force_discover and not force_all:
        cmd.append("--force-discover")

    return cmd


# ── Run section ───────────────────────────────
st.markdown('<p class="section-label">Run</p>', unsafe_allow_html=True)

cmd_preview = " ".join(build_command(
    selected_csv, csv_options, do_scrape, skip_scrape,
    do_enrich, do_discover, force_discover, force_all,
))
st.code(cmd_preview, language="bash")

run_clicked = st.button("▶  Run analysis", type="primary", use_container_width=True)

# ── Session state ─────────────────────────────
for key, default in [("running", False), ("last_report", None), ("last_discovered", [])]:
    if key not in st.session_state:
        st.session_state[key] = default

status_placeholder = st.empty()


# ── Launch ────────────────────────────────────
if run_clicked and not st.session_state.running:
    p = Path("data/run_status.json")
    if p.exists():
        p.unlink()

    st.session_state.running     = True
    st.session_state.last_report = None

    cmd = build_command(
        selected_csv, csv_options, do_scrape, skip_scrape,
        do_enrich, do_discover, force_discover, force_all,
    )
    env = os.environ.copy()
    env["CI_LLM_BACKEND"]   = llm_backend
    env["PYTHONIOENCODING"] = "utf-8"

    subprocess.Popen(cmd, env=env)
    st.rerun()


# ── Poll ──────────────────────────────────────
steps = get_steps(do_discover, skip_scrape, not do_enrich)

if st.session_state.running:
    status = read_status()
    current = status.get("current", {})

    with status_placeholder.container():
        st.markdown('<p class="section-label">Progress</p>', unsafe_allow_html=True)
        render_milestones(steps, status)

    if current.get("step") == "done" and current.get("done"):
        st.session_state.running     = False
        st.session_state.last_report = find_latest_report()
        disc_path = Path("data/run_discovered.json")
        if disc_path.exists():
            try:
                with open(disc_path, encoding="utf-8") as f:
                    st.session_state.last_discovered = json.load(f)
                disc_path.unlink()
            except Exception:
                pass
        st.rerun()
    elif current.get("error"):
        st.session_state.running = False
        st.error(f"Run failed: {current.get('detail', '')}")
        st.rerun()
    else:
        time.sleep(1.5)
        st.rerun()

else:
    # Show status from last run if available
    status = read_status()
    if status.get("current") or status.get("history"):
        with status_placeholder.container():
            st.markdown('<p class="section-label">Progress</p>', unsafe_allow_html=True)
            render_milestones(steps, status, is_running=False)


# ── Open report button ────────────────────────
if st.session_state.last_report:
    report_path = st.session_state.last_report
    st.success(f"Report ready: `{report_path.name}`")
    if st.button("&#x1F4C4;  Open report in browser"):
        webbrowser.open(report_path.resolve().as_uri())


# ── Save discovered entities ──────────────────
if st.session_state.get("last_discovered"):
    st.markdown('<p class="section-label">New entities discovered</p>',
                unsafe_allow_html=True)
    discovered = st.session_state.last_discovered
    st.info(f"{len(discovered)} new entities found. Save to CSV for future runs?")
    for e in discovered:
        st.caption(f"• {e['name']}")
    if st.button("💾  Save to CSV"):
        from csv_loader import save_discovered_to_csv
        target = selected_csv if selected_csv != csv_options[0] else None
        path = save_discovered_to_csv(discovered, target)
        st.success(f"Saved -> {path}")
        st.session_state.last_discovered = []