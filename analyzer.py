# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — analyzer.py
# ─────────────────────────────────────────────
#
#  One LLM call per entity → structured profile, constrained by a JSON schema
#  (both backends), then normalised (enums, score range, lists) so the report
#  never has to guess. Failed analyses are NOT cached — they are retried on
#  the next run instead of being frozen as "Analysis failed".
#
#  Cache: data/projects/<slug>/analyzed/<entity>.json
#  Fingerprint: raw text + topic definition + backend/model.
# ─────────────────────────────────────────────

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import llm
from llm import call_json, enum_of, nullable, parse_json  # noqa: F401  (parse_json re-exported)
from project import Project, fingerprint, load_cached, save_cached

ENTITY_TYPES = ["Company", "Spinoff", "Research Institute", "University", "Consortium", "Other"]
STAGES       = ["Research", "Prototype", "Pilot", "Commercial", "Unknown"]
POSITIONS    = ["Leader", "Strong player", "Niche player", "Adjacent", "Early stage", "Unknown"]
EVIDENCE     = ["High", "Medium", "Low"]
IDENTITY     = ["Organisation", "Research group or lab", "Not an organisation"]
OUTPUT_TYPES = ["paper", "patent", "product", "tool", "dataset", "project", "other"]

PERSON = {"type": "object", "properties": {"name": {"type": "string"},
                                           "role": nullable("string")},
          "required": ["name", "role"]}
OUTPUT = {"type": "object", "properties": {"type": enum_of(OUTPUT_TYPES),
                                           "title": {"type": "string"},
                                           "year": nullable("string")},
          "required": ["type", "title", "year"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "identity":               enum_of(IDENTITY),
        "official_name":          nullable("string"),
        "entity_type":            enum_of(ENTITY_TYPES),
        "country":                nullable("string"),
        "city":                   nullable("string"),
        "founded_or_established": nullable("string"),
        "technology_focus":       nullable("string"),
        "domain_application":     nullable("string"),
        "development_stage":      enum_of(STAGES),
        "business_model":         nullable("string"),
        "funding_or_status":      nullable("string"),
        "key_people":             {"type": "array", "items": PERSON},
        "notable_outputs":        {"type": "array", "items": OUTPUT},
        "competitive_position":   enum_of(POSITIONS),
        "relevance_score":        {"type": "integer"},
        "relevance_rationale":    {"type": "string"},
        "strengths":              {"type": "array", "items": {"type": "string"}},
        "risks":                  {"type": "array", "items": {"type": "string"}},
        "tags":                   {"type": "array", "items": {"type": "string"}},
        "summary":                {"type": "string"},
        "evidence_quality":       enum_of(EVIDENCE),
    },
}
SCHEMA["required"] = list(SCHEMA["properties"].keys())

SYSTEM = (
    "You are a senior competitive-intelligence analyst for deep-tech and life-science markets. "
    "You extract facts strictly from the provided source text and never invent names, numbers, "
    "dates, products or publications. When the text does not support a field, use null (or an "
    "empty list). Output a single JSON object matching the requested schema."
)


def build_prompt(project: Project, entity_name: str, raw_text: str) -> str:
    return f"""Landscape being mapped: {project.topic}
Scope: {project.description or project.topic}

Entity: {entity_name}

Source text (scraped from the entity's web pages, plus analyst notes):
---
{raw_text}
---

Fill these fields:
- identity: "Organisation" (company, university, institute, consortium), "Research group or lab" (a group inside a larger organisation), or "Not an organisation" ONLY if the entity name is clearly not an organisation at all (e.g. a street address, a person's name, a paper title).
- official_name: the organisation's proper name as written in the text, or null.
- entity_type: {" | ".join(ENTITY_TYPES)}.
- country, city, founded_or_established (year or date as written).
- technology_focus: 1–2 sentences on the specific technology.
- domain_application ("{project.domain_label}"): 1–2 sentences on how the entity applies it within "{project.topic}", or null if nothing specific.
- development_stage: {" | ".join(STAGES)} — of their work relevant to the topic.
- business_model: e.g. "B2B sensors + SaaS", "academic research", "contract R&D", or null.
- funding_or_status: funding rounds, grants, investors, revenue, headcount — only if stated.
- key_people: up to 5 named people with role, only if named in the text.
- notable_outputs: up to 6 papers/patents/products/tools explicitly mentioned (title ≤ 10 words).
- competitive_position: {" | ".join(POSITIONS)}.
- relevance_score 1–5 (5 = core player working directly on the topic; 4 = substantial dedicated activity;
  3 = clear but partial involvement; 2 = adjacent/tangential; 1 = unrelated or misidentified).
- relevance_rationale: one sentence justifying the score.
- strengths, risks: 1–3 short bullet phrases each, grounded in the text (empty list if unclear).
- tags: 3–6 short lowercase technology/application keywords.
- summary: 2–3 sentence executive summary.
- evidence_quality: High (rich, specific source text) | Medium | Low (thin or generic text).
"""


def _pick(value, allowed: list[str], default: str) -> str:
    if not value:
        return default
    v = str(value).strip().lower()
    for a in allowed:
        if v == a.lower():
            return a
    for a in allowed:
        if a.lower() in v or v in a.lower():
            return a
    return default


def _clean_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in ("", "null", "none", "n/a", "unknown", "not specified") else s


def _clean_list(items, limit: int) -> list:
    out, seen = [], set()
    for x in items or []:
        if isinstance(x, dict):
            key = (x.get("name") or x.get("title") or "").strip().lower()
            if not key or key in ("null", "none") or key in seen:
                continue
            seen.add(key)
            out.append({k: _clean_str(v) if isinstance(v, str) or v is None else v for k, v in x.items()})
        else:
            s = _clean_str(x)
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
        if len(out) >= limit:
            break
    return out


def normalize(data: dict) -> dict:
    """Coerce an LLM result into the canonical record shape."""
    d = dict(data or {})
    out = {
        "identity":               _pick(str(d.get("identity") or "").replace("organiz", "organis"), IDENTITY, "Organisation"),
        "official_name":          _clean_str(d.get("official_name")),
        "entity_type":            _pick(d.get("entity_type"), ENTITY_TYPES, "Other"),
        "country":                _clean_str(d.get("country")),
        "city":                   _clean_str(d.get("city")),
        "founded_or_established": _clean_str(d.get("founded_or_established")),
        "technology_focus":       _clean_str(d.get("technology_focus")),
        "domain_application":     _clean_str(d.get("domain_application")),
        "development_stage":      _pick(d.get("development_stage"), STAGES, "Unknown"),
        "business_model":         _clean_str(d.get("business_model")),
        "funding_or_status":      _clean_str(d.get("funding_or_status")),
        "competitive_position":   _pick(d.get("competitive_position"), POSITIONS, "Unknown"),
        "relevance_rationale":    _clean_str(d.get("relevance_rationale")),
        "summary":                _clean_str(d.get("summary")),
        "evidence_quality":       _pick(d.get("evidence_quality"), EVIDENCE, "Low"),
        "strengths":              _clean_list(d.get("strengths"), 4),
        "risks":                  _clean_list(d.get("risks"), 4),
        "tags":                   [t.lower() for t in _clean_list(d.get("tags"), 8)],
    }
    try:
        out["relevance_score"] = max(1, min(5, int(round(float(d.get("relevance_score"))))))
    except (TypeError, ValueError):
        out["relevance_score"] = None

    people = d.get("key_people")
    if isinstance(people, str):        # legacy "Name (role), Name" strings
        people = [{"name": p.strip(), "role": None} for p in people.split(",") if p.strip()]
    out["key_people"] = [p if isinstance(p, dict) else {"name": str(p), "role": None}
                         for p in _clean_list(people, 6)]

    outputs = d.get("notable_outputs")
    if isinstance(outputs, str):
        outputs = [{"type": "other", "title": p.strip(), "year": None} for p in outputs.split(",") if p.strip()]
    norm_outputs = []
    for o in _clean_list(outputs, 8):
        if isinstance(o, str):
            o = {"type": "other", "title": o, "year": None}
        o["type"] = _pick(o.get("type"), OUTPUT_TYPES, "other")
        norm_outputs.append(o)
    out["notable_outputs"] = norm_outputs
    return out


def empty_record(name: str, raw: dict, error: str | None = None) -> dict:
    rec = normalize({})
    rec["is_organization"] = True
    rec.update(entity_name=name, entity_urls=raw.get("urls", []), website=raw.get("website"),
               summary=None, evidence_quality="Low")
    if error:
        rec["_error"] = error
    return rec


def analyze_entity(project: Project, name: str, raw: dict) -> dict:
    text = (raw.get("text") or "").strip()
    if not text:
        rec = empty_record(name, raw, "No source text could be collected for this entity.")
        rec["summary"] = "No public source text could be collected — add a URL or notes to the CSV."
        return rec
    data = call_json(build_prompt(project, name, text), schema=SCHEMA, system=SYSTEM, max_tokens=3000)
    rec = normalize(data)
    rec["is_organization"] = rec["identity"] != "Not an organisation"
    rec.update(entity_name=name, entity_urls=raw.get("urls", []), website=raw.get("website"),
               url_source=raw.get("url_source"), sources=raw.get("sources", []),
               discovered=raw.get("discovered", False), input_tags=raw.get("tags", []),
               notes=raw.get("notes", ""), openalex_id=raw.get("openalex_id"))
    return rec


def analyze_all(project: Project, scraped: dict, force: bool = False, on_progress=None) -> dict:
    """Run LLM analysis on all scraped entities. Returns {entity_name: record}."""
    results, todo = {}, []
    model_id = llm.describe()
    for name, raw in scraped.items():
        fp = fingerprint(raw.get("_fp"), project.context_fp, model_id)
        cached = None if force else load_cached(project.entity_path("analyzed", name), fp)
        if cached:
            print(f"  [CACHE] {name}")
            results[name] = cached
        else:
            todo.append((name, raw, fp))

    total, done = len(scraped), len(results)
    if on_progress:
        on_progress(done, total, "")

    def _work(item):
        name, raw, fp = item
        print(f"  [ANALYZE] {name}  ({model_id})")
        try:
            rec = analyze_entity(project, name, raw)
        except Exception as e:
            print(f"    [ERROR] LLM failed for {name}: {e}")
            return name, empty_record(name, raw, f"Analysis failed: {e}")
        if rec.get("_error") and not raw.get("text"):
            return name, rec               # nothing to analyse: don't cache, retry later
        return name, save_cached(project.entity_path("analyzed", name), rec, fp)

    workers = 4 if llm.backend() == "claude" else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(_work, t) for t in todo]):
            name, rec = fut.result()
            results[name] = rec
            done += 1
            if on_progress:
                on_progress(done, total, name)

    return {n: results[n] for n in scraped if n in results}
