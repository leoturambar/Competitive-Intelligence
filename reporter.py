# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — reporter.py
# ─────────────────────────────────────────────

from datetime import date
from pathlib import Path

from config import REPORT_TITLE, REPORT_SUBTITLE, TOPIC_DESCRIPTION

REPORTS_DIR = Path("data/reports")


def _format_value(v):
    if v is None:
        return "—"
    # try to parse string-encoded lists
    if isinstance(v, str):
        stripped = v.strip()
        if stripped.startswith("["):
            try:
                import json, ast
                try:
                    v = json.loads(stripped)
                except Exception:
                    v = ast.literal_eval(stripped)
            except Exception:
                pass
    if isinstance(v, list):
        parts = []
        for x in v:
            if isinstance(x, dict):
                # formato dizionario → stringa leggibile
                title = x.get("title") or x.get("name") or str(x)
                year  = x.get("year")
                typ   = x.get("type")
                label = f"{typ.capitalize()}: {title}" if typ else title
                if year:
                    label += f" ({year})"
                parts.append(label)
            elif x and str(x).lower() not in ("null", "none", ""):
                parts.append(str(x).strip())
        return ", ".join(parts) if parts else "—"
    s = str(v).strip()
    if s.lower() in ("null", "none", ""):
        return "—"
    return s


# ── Stage / position badge colours ───────────
STAGE_COLORS = {
    "Research":   ("#e8f4fd", "#1a6fa8"),
    "Prototype":  ("#fef9e7", "#b7770d"),
    "Pilot":      ("#eafaf1", "#1e8449"),
    "Commercial": ("#f9ebea", "#922b21"),
    "Unknown":    ("#f2f3f4", "#717d7e"),
}

POSITION_COLORS = {
    "Leader":        ("#1a6fa8", "#fff"),
    "Strong player": ("#1e8449", "#fff"),
    "Niche player":  ("#7d3c98", "#fff"),
    "Adjacent":      ("#717d7e", "#fff"),
    "Early stage":   ("#b7770d", "#fff"),
    "Unknown":       ("#aab7b8", "#fff"),
}

TYPE_ICONS = {
    "Company":            "🏢",
    "Spinoff":            "🚀",
    "Research Institute": "🔬",
    "University":         "🎓",
    "Consortium":         "🤝",
    "Other":              "•",
}


def _badge(text, bg, fg="#333"):
    if not text:
        text = "—"
    return (
        f'<span style="background:{bg};color:{fg};padding:2px 9px;'
        f'border-radius:12px;font-size:0.75rem;font-weight:600;white-space:nowrap">'
        f'{text}</span>'
    )


def _stage_badge(stage):
    bg, fg = STAGE_COLORS.get(stage, ("#f2f3f4", "#333"))
    return _badge(stage, bg, fg)


def _position_badge(pos):
    bg, fg = POSITION_COLORS.get(pos, ("#aab7b8", "#fff"))
    return _badge(pos, bg, fg)


def _score_dots(score):
    if score is None:
        return "—"
    score = int(score)
    filled = "●" * score
    empty  = "○" * (5 - score)
    return f'<span style="color:#1a6fa8;letter-spacing:2px">{filled}</span><span style="color:#d5d8dc">{empty}</span>'


def _entity_card(e: dict) -> str:
    name     = e.get("entity_name", "Unknown")
    etype    = e.get("entity_type", "Other")
    icon     = TYPE_ICONS.get(etype, "•")
    score    = e.get("relevance_score")
    country  = _format_value(e.get("country"))
    founded  = _format_value(e.get("founded_or_established"))
    stage    = _format_value(e.get("development_stage")) or "Unknown"
    position = _format_value(e.get("competitive_position")) or "Unknown"
    funding  = _format_value(e.get("funding_or_status"))
    people   = _format_value(e.get("key_people"))
    outputs  = _format_value(e.get("notable_outputs"))
    summary  = _format_value(e.get("summary"))
    tech     = _format_value(e.get("technology_focus"))
    plant    = _format_value(e.get("plant_application"))

    return f"""
<div class="card">
  <div class="card-header">
    <div>
      <span class="entity-icon">{icon}</span>
      <span class="entity-name">{name}</span>
    </div>
    <div class="badges">
      {_badge(etype, "#eaf2ff", "#1a5276")}
      {_stage_badge(stage)}
      {_position_badge(position)}
    </div>
  </div>

  <div class="summary">{summary}</div>

  <div class="meta-grid">
    <div class="meta-item"><span class="meta-label">Country</span><span>{country}</span></div>
    <div class="meta-item"><span class="meta-label">Founded / Est.</span><span>{founded}</span></div>
    <div class="meta-item"><span class="meta-label">Relevance</span><span>{_score_dots(score)}</span></div>
    <div class="meta-item"><span class="meta-label">Funding / Status</span><span>{funding}</span></div>
  </div>

  <div class="detail-grid">
    <div><span class="meta-label">Technology focus</span><p>{tech}</p></div>
    <div><span class="meta-label">Plant application</span><p>{plant}</p></div>
    <div><span class="meta-label">Key people</span><p>{people}</p></div>
    <div><span class="meta-label">Notable outputs</span><p>{outputs}</p></div>
  </div>
</div>
"""


def generate_report(analyzed: dict, filename: str = None) -> Path:
    """
    Generate an HTML report from analyzed entities.
    Sorted by relevance_score descending.
    Returns the path to the saved file.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    today     = date.today().isoformat()
    filename  = filename or f"competitive_intelligence_{today}.html"
    out_path  = REPORTS_DIR / filename

    # Sort by relevance descending, nulls last
    entities = sorted(
        analyzed.values(),
        key=lambda x: -(x.get("relevance_score") or 0)
    )

    n_entities = len(entities)
    n_companies = sum(1 for e in entities if e.get("entity_type") in ("Company", "Spinoff"))
    n_academic  = sum(1 for e in entities if e.get("entity_type") in ("University", "Research Institute"))
    n_other     = n_entities - n_companies - n_academic

    cards_html = "\n".join(_entity_card(e) for e in entities)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{REPORT_TITLE} — {REPORT_SUBTITLE}</title>
<style>
  :root {{
    --blue:   #1a6fa8;
    --green:  #1e8449;
    --bg:     #f8f9fa;
    --card:   #ffffff;
    --border: #e0e4e8;
    --text:   #2c3e50;
    --muted:  #6c757d;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 14px;
    line-height: 1.6;
  }}

  .page-header {{
    background: linear-gradient(135deg, #0d4f7c 0%, #1a6fa8 100%);
    color: #fff;
    padding: 40px 48px 32px;
  }}

  .page-header .label {{
    font-size: 0.7rem;
    letter-spacing: 2px;
    text-transform: uppercase;
    opacity: 0.7;
    margin-bottom: 6px;
  }}

  .page-header h1 {{
    font-size: 1.9rem;
    font-weight: 700;
    margin-bottom: 4px;
  }}

  .page-header h2 {{
    font-size: 1.1rem;
    font-weight: 400;
    opacity: 0.85;
    margin-bottom: 20px;
  }}

  .page-header .description {{
    font-size: 0.85rem;
    opacity: 0.75;
    max-width: 720px;
    margin-bottom: 24px;
  }}

  .stats-bar {{
    display: flex;
    gap: 32px;
  }}

  .stat {{
    text-align: center;
  }}

  .stat .num {{
    font-size: 1.6rem;
    font-weight: 700;
  }}

  .stat .lbl {{
    font-size: 0.72rem;
    opacity: 0.7;
    text-transform: uppercase;
    letter-spacing: 1px;
  }}

  .content {{
    max-width: 960px;
    margin: 32px auto;
    padding: 0 24px;
  }}

  .section-label {{
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 2px;
    color: var(--muted);
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}

  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 22px 24px;
    margin-bottom: 16px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
  }}

  .card-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 12px;
  }}

  .entity-icon {{ font-size: 1.1rem; margin-right: 6px; }}

  .entity-name {{
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text);
  }}

  .badges {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }}

  .summary {{
    color: #4a5568;
    font-size: 0.88rem;
    margin-bottom: 16px;
    padding: 10px 14px;
    background: #f7fafc;
    border-left: 3px solid var(--blue);
    border-radius: 0 6px 6px 0;
  }}

  .meta-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
    margin-bottom: 14px;
  }}

  .meta-item {{
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}

  .meta-label {{
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
    font-weight: 600;
    display: block;
    margin-bottom: 2px;
  }}

  .detail-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px 20px;
    border-top: 1px solid var(--border);
    padding-top: 14px;
    font-size: 0.84rem;
  }}

  .detail-grid p {{
    color: #4a5568;
    margin-top: 3px;
  }}

  .footer {{
    text-align: center;
    padding: 24px;
    font-size: 0.75rem;
    color: var(--muted);
    border-top: 1px solid var(--border);
    margin-top: 40px;
  }}
</style>
</head>
<body>

<div class="page-header">
  <div class="label">Intelligence Report</div>
  <h1>{REPORT_TITLE}</h1>
  <h2>{REPORT_SUBTITLE}</h2>
  <p class="description">{TOPIC_DESCRIPTION}</p>
  <div class="stats-bar">
    <div class="stat"><div class="num">{n_entities}</div><div class="lbl">Entities</div></div>
    <div class="stat"><div class="num">{n_companies}</div><div class="lbl">Companies / Spinoffs</div></div>
    <div class="stat"><div class="num">{n_academic}</div><div class="lbl">Academic / Research</div></div>
    <div class="stat"><div class="num">{n_other}</div><div class="lbl">Other</div></div>
    <div class="stat"><div class="num">{today}</div><div class="lbl">Generated</div></div>
  </div>
</div>

<div class="content">
  <div class="section-label">Entities — sorted by relevance</div>
  {cards_html}
</div>

<div class="footer">
  Generated automatically · {REPORT_TITLE} · {today}
</div>

</body>
</html>
"""

    out_path.write_text(html, encoding="utf-8")
    print(f"  [REPORT] Saved → {out_path}")
    return out_path
