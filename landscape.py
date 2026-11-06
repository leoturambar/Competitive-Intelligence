# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — landscape.py
# ─────────────────────────────────────────────
#
#  Cross-entity synthesis: the "so what" of the report.
#    - deterministic statistics (by type, stage, country, research output)
#    - one LLM call over compact entity profiles → executive summary,
#      key players, segments, trends, white spaces, watchlist.
#
#  Names in the LLM output are validated against the entity list, so the
#  synthesis can only talk about organisations that are actually mapped.
#  Cache: data/projects/<slug>/landscape.json
# ─────────────────────────────────────────────

from __future__ import annotations

from collections import Counter

import llm
from llm import call_json
from project import Project, fingerprint, load_cached, save_cached

NAMED = {"type": "object", "properties": {"name": {"type": "string"}, "why": {"type": "string"}},
         "required": ["name", "why"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "key_players": {"type": "array", "items": NAMED},
        "segments": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                           "members": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "description", "members"]}},
        "trends": {"type": "array", "items": {"type": "string"}},
        "white_spaces": {"type": "array", "items": {"type": "string"}},
        "watchlist": {"type": "array", "items": NAMED},
    },
}
SCHEMA["required"] = list(SCHEMA["properties"].keys())


def _country(c):
    from openalex import country_name
    return country_name(c) if isinstance(c, str) and len(c) == 2 and c.isupper() else c


def stats(records: list[dict]) -> dict:
    valid = [r for r in records if r.get("is_organization", True) and not r.get("_error")]
    scores = [r["relevance_score"] for r in valid if r.get("relevance_score")]
    papers = sum((r.get("research") or {}).get("topic_works", 0) or 0 for r in valid)
    return {
        "n_entities": len(valid),
        "n_flagged": len(records) - len(valid),
        "by_type": dict(Counter(r.get("entity_type") or "Other" for r in valid).most_common()),
        "by_stage": dict(Counter(r.get("development_stage") or "Unknown" for r in valid).most_common()),
        "by_position": dict(Counter(r.get("competitive_position") or "Unknown" for r in valid).most_common()),
        "by_country": dict(Counter(_country(r.get("country")) or "Unknown" for r in valid).most_common(12)),
        "avg_relevance": round(sum(scores) / len(scores), 2) if scores else None,
        "core_players": sum(1 for s in scores if s >= 4),
        "topic_papers": papers,
        "commercial": sum(1 for r in valid if r.get("development_stage") in ("Pilot", "Commercial")),
    }


def _profile(r: dict) -> str:
    res = r.get("research") or {}
    bits = [
        f"{r['entity_name']} [{r.get('entity_type')}, {r.get('country') or '?'}]",
        f"stage={r.get('development_stage')}, position={r.get('competitive_position')}, relevance={r.get('relevance_score')}",
    ]
    if r.get("technology_focus"):
        bits.append(f"tech: {r['technology_focus']}")
    if r.get("domain_application"):
        bits.append(f"application: {r['domain_application']}")
    if res.get("topic_works"):
        bits.append(f"topic papers: {res['topic_works']} (momentum {res.get('momentum')})")
    if r.get("funding_or_status"):
        bits.append(f"funding/status: {r['funding_or_status']}")
    if r.get("recent_developments"):
        bits.append("recent: " + "; ".join(f"{d.get('date') or ''} {d['event']}"
                                           for d in r["recent_developments"][:2]))
    return " | ".join(bits)


def _valid_names(items: list, names: set) -> list:
    lower = {n.lower(): n for n in names}
    out = []
    for it in items or []:
        n = (it.get("name") or "").strip()
        match = lower.get(n.lower()) or next((v for k, v in lower.items() if n.lower() in k or k in n.lower()), None)
        if match:
            out.append({**it, "name": match})
    return out


def synthesize(project: Project, records: list[dict], force: bool = False) -> dict:
    st = stats(records)
    valid = [r for r in records if r.get("is_organization", True) and not r.get("_error")]
    fp = fingerprint(project.context_fp, llm.describe(),
                     sorted((r["entity_name"], r.get("_fp")) for r in valid))
    cached = None if force else load_cached(project.landscape_path, fp)
    if cached:
        print("  [CACHE] landscape synthesis")
        cached["stats"] = st
        return cached

    result = {"stats": st}
    if len(valid) >= 2:
        valid_sorted = sorted(valid, key=lambda r: -(r.get("relevance_score") or 0))
        profiles = "\n".join(f"- {_profile(r)}" for r in valid_sorted[:60])
        prompt = f"""You are briefing an executive on the competitive landscape of "{project.topic}".
Scope: {project.description or project.topic}

Mapped organisations (from web sources and publication data):
{profiles}

Aggregate figures: {st}

Write, grounded ONLY in the profiles above:
- executive_summary: 4–6 sentences — maturity of the field, who leads, where the commercial activity is, the main gap.
- key_players: 3–6 most important organisations, each with a one-line why.
- segments: 2–5 meaningful clusters (by technology approach or application), each with members (exact names from the list).
- trends: 3–5 observable trends.
- white_spaces: 2–4 gaps or opportunities nobody in the list covers well.
- watchlist: 2–4 smaller or emerging organisations to monitor, with the signal that justifies it.
Use exact organisation names from the list."""
        try:
            data = call_json(prompt, schema=SCHEMA, max_tokens=4000)
            names = {r["entity_name"] for r in valid}
            data["key_players"] = _valid_names(data.get("key_players"), names)
            data["watchlist"] = _valid_names(data.get("watchlist"), names)
            for seg in data.get("segments") or []:
                seg["members"] = [m["name"] for m in _valid_names([{"name": x} for x in seg.get("members", [])], names)]
            result.update(data)
            return save_cached(project.landscape_path, result, fp)
        except Exception as e:
            print(f"  [WARN] landscape synthesis failed: {e}")
            result["error"] = str(e)
    return result
