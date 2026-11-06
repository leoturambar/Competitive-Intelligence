# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — discoverer.py
# ─────────────────────────────────────────────
#
#  Finds organisations active on the topic that are not yet in the CSV.
#  Two independent candidate sources feed one verification funnel:
#
#   A. Bibliometric (OpenAlex)
#        institutions with the most topic papers (title/abstract match),
#        recent years weighted. Evidence = publication count + homepage.
#
#   B. Web affiliations (DDG + regex)          ← original pipeline, kept
#        1. DDG search → raw text from papers + lab pages
#        2. _regex_affiliations → density-window affiliation strings
#        3. _verify_affiliation → DDG each candidate + topic keywords;
#           the accepted page must NOT be an aggregator (journal, PubMed,
#           ResearchGate…) and must mention the candidate's own name
#
#   Funnel (both sources):
#        dedupe against known entities (name tokens + domain) →
#        LLM relevance check (with evidence) → LLM naming/formatting
#        (index-based: the model cannot invent names or URLs).
#
#  Every candidate and every decision is logged to
#  data/projects/<slug>/discovery.json so the UI can show *why* something
#  was kept or dropped and let the user accept / dismiss each one.
# ─────────────────────────────────────────────

from __future__ import annotations

import re
from datetime import date, datetime

import openalex
import web
from analyzer import ENTITY_TYPES
from llm import call_json, enum_of, nullable
from project import Project, fingerprint, read_json, write_json

DISCOVERY_N_NEW        = 12   # max new entities to keep
DISCOVERY_N_QUERIES    = 5    # DDG queries to run
DISCOVERY_PAGES_EACH   = 6    # pages fetched per query
MAX_REGEX_TO_VERIFY    = 25   # bound the slow DDG verification loop
OPENALEX_CANDIDATES    = 30
OPENALEX_MIN_WORKS     = 2
OPENALEX_SKIP_TYPES    = {"funder"}


# ── Affiliation extraction (source B) ─────────

def _regex_affiliations(text: str) -> list:
    """
    Extract candidate affiliation strings from raw paper/page text.
    Uses density-based windowing to find the most affiliation-rich region,
    then extracts individual affiliation lines from it.
    """
    markers = [
        "university", "université", "universit",
        "institute", "laboratory", "lab ", "center", "centre",
        "department", "school of", "college of",
    ]

    lines = text.split(" ")
    window = 400
    best_start, best_score = 0, 0

    for i in range(0, max(1, len(lines) - window), 50):
        chunk = " ".join(lines[i:i + window]).lower()
        score = sum(chunk.count(m) for m in markers)
        if score > best_score:
            best_score = score
            best_start = i

    if best_score < 2:
        return []

    start = max(0, best_start - 30)
    end   = min(len(lines), best_start + window + 30)
    region = " ".join(lines[start:end])

    patterns = [
        r'\d+[\.\s]+([A-Z][^,\n]{5,80},\s*[A-Z][^,\n]{3,60})',
        r'[a-z][\.\s]+([A-Z][^,\n]{5,80},\s*[A-Z][^,\n]{3,60})',
        r'(?:Department|Laboratory|Lab\b|Institute|Center|Centre|School|College)\s+of\s+[^,\n]{3,60},\s*[A-Z][^,\n]{3,60}',
        r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+University[^,\n]{0,40}',
    ]

    found, seen = [], set()
    for pattern in patterns:
        for m in re.finditer(pattern, region):
            s = m.group(0).strip() if m.lastindex is None else m.group(1).strip()
            s = re.sub(r"\s+", " ", s).strip()
            if len(s) > 15 and s.lower() not in seen:
                seen.add(s.lower())
                found.append(s)
    return found[:30]


NOISE_PATTERNS = [
    r"\d{3,}",                                   # postal codes, phone numbers
    r"\b(Street|Avenue|Road|Boulevard|Lane|Drive|Way|Rd\.|Ave\.|Str\.|Strasse|Straße|Via|Rue)\b",
    r"Find articles by", r"doi:", r"https?://", r"^\s*\d", r"@", r"©|copyright",
    r"\b(Received|Accepted|Published|Abstract|Keywords|Correspondence)\b",
]
ORG_INDICATORS = [
    "university", "universit", "institute", "laboratory", "lab ", "center", "centre",
    "department", "college", "school", "hospital", "foundation", "agency", "gmbh",
    "inc.", "corp", "ltd", "s.a.", "s.r.l", "polytechnic", "politecnico", "academy",
]


def prefilter(affiliation: str) -> bool:
    if any(re.search(p, affiliation, re.IGNORECASE) for p in NOISE_PATTERNS):
        return False
    return any(ind in affiliation.lower() for ind in ORG_INDICATORS)


def _verify_affiliation(affiliation: str, project: Project) -> dict | None:
    """DDG the candidate; accept the first non-aggregator page that mentions the
    candidate's distinctive name words AND ≥2 topic keywords."""
    print(f"    [VERIFY] {affiliation[:60]}...")
    tokens = web.name_tokens(affiliation)
    for r in web.ddg_search(f"{web.shorten_query(affiliation, '', 5)} {project.topic}", max_results=5):
        if web.is_aggregator(r.url):
            continue
        page = web.fetch(r.url)
        if not page.ok or not page.text:
            continue
        hay = f"{page.title} {page.site_name} {page.text[:4000]}".lower()
        name_hits = sum(1 for t in tokens if t in hay)
        kw_hits = sum(1 for kw in project.keywords if kw.lower() in hay)
        if tokens and name_hits >= max(1, (len(tokens) + 1) // 2) and kw_hits >= 2:
            print(f"             OK  ({kw_hits} keyword hits, {name_hits}/{len(tokens)} name words) -> {r.url}")
            return {"url": r.url, "page_title": page.name_hint[:100],
                    "page_snippet": (page.description or page.text)[:300],
                    "evidence": f"{kw_hits} topic keywords on {web.domain_of(r.url)}"}
    return None


# ── Candidate sources ─────────────────────────

def _web_candidates(project: Project, known_urls: set, log) -> list[dict]:
    page_texts, seen_urls = {}, set()
    queries = project.queries[:DISCOVERY_N_QUERIES]
    for query in queries:
        log(f"DDG: {query}")
        print(f"    Query: {query}")
        for url in web.ddg_links(query, DISCOVERY_PAGES_EACH):
            if url in seen_urls or url in known_urls:
                continue
            seen_urls.add(url)
            text = web.fetch_text(url, max_chars=6000)
            if text:
                page_texts[url] = text
                print(f"    -> {url}  ({len(text)} chars)")

    if not page_texts:
        print("  [WARN] Initial queries returned nothing — trying fallback queries...")
        for query in _fallback_queries(project, queries):
            for url in web.ddg_links(query, DISCOVERY_PAGES_EACH):
                if url not in seen_urls:
                    seen_urls.add(url)
                    text = web.fetch_text(url, max_chars=6000)
                    if text:
                        page_texts[url] = text

    raw, seen = [], set()
    for url, text in page_texts.items():
        for aff in _regex_affiliations(text):
            if aff.lower() not in seen:
                seen.add(aff.lower())
                raw.append({"name": aff, "source": "web", "found_on": url})
    print(f"  [DISCOVER] {len(raw)} affiliation strings extracted")
    return raw


def _fallback_queries(project: Project, used: list) -> list:
    prompt = (f'You are helping search for organisations working on "{project.topic}".\n'
              f"These search queries returned no results:\n" + "\n".join(f"- {q}" for q in used) +
              "\nGenerate 3 alternative search queries (2-4 words each, different terminology).")
    try:
        data = call_json(prompt, schema={"type": "object", "properties": {
            "queries": {"type": "array", "items": {"type": "string"}}}, "required": ["queries"]})
        return [q for q in data.get("queries", []) if isinstance(q, str)][:3]
    except Exception:
        return []


def _openalex_candidates(project: Project, log) -> list[dict]:
    query = openalex.topic_query(project.topic, project.keywords)
    log(f"OpenAlex: {query}")
    print(f"  [DISCOVER] OpenAlex institutions publishing on '{query}'")
    since = date.today().year - 8
    out = []
    for inst in openalex.top_institutions(query, n=OPENALEX_CANDIDATES, since_year=since):
        if inst["topic_works"] < OPENALEX_MIN_WORKS or inst.get("type") in OPENALEX_SKIP_TYPES:
            continue
        out.append({
            "name": inst["name"], "source": "openalex", "url": inst.get("homepage_url"),
            "openalex_id": inst["openalex_id"], "country": openalex.country_name(inst.get("country_code")),
            "oa_type": inst.get("type"), "topic_works": inst["topic_works"],
            "evidence": f"{inst['topic_works']} topic papers since {since} (OpenAlex)",
        })
    print(f"  [DISCOVER] {len(out)} OpenAlex candidates")
    return out


# ── Known-entity matching ─────────────────────

def _matches_known(cand: dict, known: list[dict]) -> str | None:
    ctoks = openalex._tokens(cand["name"])
    cdom = web.domain_of(cand.get("url") or "")
    for k in known:
        ktoks = openalex._tokens(k["name"])
        if ctoks and ktoks and (ctoks <= ktoks or ktoks <= ctoks):
            return k["name"]
        for u in k.get("urls") or []:
            kd = web.domain_of(u)
            if cdom and kd and cdom == kd and not web.is_aggregator(u):
                return k["name"]
    return None


# ── LLM funnel ────────────────────────────────

RELEVANCE_SCHEMA = {"type": "object", "properties": {"checks": {"type": "array", "items": {
    "type": "object",
    "properties": {"index": {"type": "integer"}, "relevant": {"type": "boolean"},
                   "is_organization": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["index", "relevant", "is_organization", "reason"]}}}, "required": ["checks"]}

FORMAT_SCHEMA = {"type": "object", "properties": {"entities": {"type": "array", "items": {
    "type": "object",
    "properties": {"index": {"type": "integer"}, "name": {"type": "string"},
                   "entity_type": enum_of(ENTITY_TYPES), "notes": {"type": "string"},
                   "country": nullable("string")},
    "required": ["index", "name", "entity_type", "notes", "country"]}}}, "required": ["entities"]}


def _describe(c: dict) -> str:
    bits = [c["name"]]
    if c.get("url"):
        bits.append(f"URL: {c['url']}")
    if c.get("page_title"):
        bits.append(f'page title: "{c["page_title"]}"')
    if c.get("evidence"):
        bits.append(f"evidence: {c['evidence']}")
    if c.get("page_snippet"):
        bits.append(f'text: "{c["page_snippet"][:200]}"')
    return " | ".join(bits)


def _relevance_check(project: Project, cands: list[dict]) -> None:
    block = "\n".join(f"{i + 1}. {_describe(c)}" for i, c in enumerate(cands))
    prompt = f"""You are an expert analyst in {project.topic}.
Scope: {project.description or project.topic}

For each candidate below decide:
- is_organization: is it a real organisation or research group (not an address, person, journal, funder or web page)?
- relevant: does it work directly on "{project.topic}" (not merely adjacent)? Publication counts are strong evidence.
- reason: one short line.

Candidates:
{block}

Return one entry per candidate with its index."""
    try:
        checks = call_json(prompt, schema=RELEVANCE_SCHEMA, max_tokens=4000).get("checks", [])
    except Exception as e:
        print(f"  [ERROR] LLM relevance check failed: {e}")
        checks = []
    reviewed = set()
    for chk in checks:
        idx = chk.get("index", 0)
        if 1 <= idx <= len(cands):
            reviewed.add(idx)
            c = cands[idx - 1]
            keep = bool(chk.get("relevant")) and chk.get("is_organization", True) is not False
            c["decision"] = "kept" if keep else "dropped: not relevant"
            c["reason"] = chk.get("reason", "")
            print(f"  [{'KEEP' if keep else 'DROP'}] {c['name'][:60]}: {c['reason']}")
    for i, c in enumerate(cands, start=1):
        if i not in reviewed:      # silently skipped by the model → keep, flagged
            c["decision"], c["reason"] = "kept", "not reviewed by the model — included by default"


def _format(project: Project, cands: list[dict], known: list[dict]) -> None:
    block = "\n".join(f"{i + 1}. {_describe(c)}" for i, c in enumerate(cands))
    already = "\n".join(f"- {e['name']}" for e in known) or "(none)"
    prompt = f"""You are an expert analyst in {project.topic}.

Give each organisation below a clean official name (e.g. "Uppsala University — Plant Bioelectronics Group"
when a specific group is evident, otherwise the institution name), its type, country if evident,
and one sentence of notes on what it does on {project.topic}.
Use only the information given. Do not include an entry that clearly duplicates an already-known entity.

Organisations:
{block}

Already known:
{already}"""
    try:
        rows = call_json(prompt, schema=FORMAT_SCHEMA, max_tokens=4000).get("entities", [])
    except Exception as e:
        print(f"  [ERROR] LLM formatting failed: {e}")
        rows = []
    formatted = set()
    for r in rows:
        idx = r.get("index", 0)
        if 1 <= idx <= len(cands):
            c = cands[idx - 1]
            formatted.add(idx)
            c["clean_name"] = (r.get("name") or c["name"]).strip()
            c["entity_type"] = r.get("entity_type")
            c["notes"] = r.get("notes", "")
            c["country"] = c.get("country") or r.get("country")
    for i, c in enumerate(cands, start=1):
        if i not in formatted:
            c["decision"], c["reason"] = "dropped: duplicate of known entity", "removed during formatting"


# ── Main ──────────────────────────────────────

def discover(project: Project, force: bool = False, on_status=None,
             use_web: bool = True, use_openalex: bool = True) -> list[dict]:
    """Return newly discovered entity dicts (status 'new'), cached per project."""
    log = (lambda msg: on_status(msg)) if on_status else (lambda msg: None)
    fp = fingerprint(project.topic, project.description, project.queries, project.keywords,
                     use_web, use_openalex)
    cached = read_json(project.discovery_path)

    if cached and cached.get("_fp") == fp and not force:
        print("  [CACHE] discovery results")
        known = project.entities
        fresh = [e for e in cached.get("entities", [])
                 if e.get("status") == "new" and not _matches_known(e, known)]
        return fresh

    known = project.entities
    known_urls = {u for e in known for u in e.get("urls", [])}
    candidates: list[dict] = []

    if use_openalex:
        candidates += _openalex_candidates(project, log)

    if use_web:
        raw = _web_candidates(project, known_urls, log)
        raw = [c for c in raw if prefilter(c["name"])]
        log(f"verifying {min(len(raw), MAX_REGEX_TO_VERIFY)} web candidates")
        for i, c in enumerate(raw[:MAX_REGEX_TO_VERIFY]):
            if _matches_known(c, known) or _matches_known(c, candidates):
                continue
            if on_status and i % 3 == 0:
                on_status(f"verifying {i + 1}/{min(len(raw), MAX_REGEX_TO_VERIFY)}")
            v = _verify_affiliation(c["name"], project)
            if v:
                c.update(v)
                candidates.append(c)
            else:
                c["decision"], c["reason"] = "dropped: not verified", "no matching non-aggregator page"
                candidates.append(c)

    # Known-entity and URL dedupe
    seen_urls: dict[str, str] = {}
    pool = []
    for c in candidates:
        if c.get("decision"):
            continue
        hit = _matches_known(c, known)
        if hit:
            c["decision"], c["reason"] = "dropped: already known", f"matches '{hit}'"
            continue
        u = (c.get("url") or "").rstrip("/")
        if u and u in seen_urls:
            c["decision"], c["reason"] = "dropped: duplicate", f"same URL as '{seen_urls[u]}'"
            continue
        if u:
            seen_urls[u] = c["name"]
        pool.append(c)
    # Most evidence first, so the cap keeps the strongest candidates
    pool.sort(key=lambda c: -(c.get("topic_works") or 0))
    pool = pool[:DISCOVERY_N_NEW * 3]
    print(f"  [DISCOVER] {len(pool)} candidates for LLM review")

    if pool:
        log(f"LLM relevance check on {len(pool)} candidates")
        _relevance_check(project, pool)
        kept = [c for c in pool if c.get("decision") == "kept"][:DISCOVERY_N_NEW * 2]
        if kept:
            log(f"naming {len(kept)} candidates")
            _format(project, kept, known)

    new_entities, taken = [], set()
    for c in pool:
        if c.get("decision") != "kept" or not c.get("clean_name"):
            continue
        name = c["clean_name"]
        if name.lower() in taken or _matches_known({"name": name, "url": c.get("url")}, known):
            c["decision"] = "dropped: already known"
            continue
        if len(new_entities) >= DISCOVERY_N_NEW:
            c["decision"] = "dropped: over limit"
            continue
        taken.add(name.lower())
        new_entities.append({
            "name": name, "urls": [c["url"]] if c.get("url") else [],
            "notes": c.get("notes", ""), "entity_type": c.get("entity_type"),
            "country": c.get("country"), "source": c["source"], "evidence": c.get("evidence"),
            "openalex_id": c.get("openalex_id"),
            "reason": c.get("reason"), "status": "new", "_discovered": True,
            "discovered_on": date.today().isoformat(),
        })
        print(f"  [DISCOVER] Found: {name}  ({c['source']})")

    write_json(project.discovery_path, {
        "_fp": fp, "ts": datetime.now().isoformat(timespec="seconds"),
        "entities": new_entities,
        "candidates": [{k: v for k, v in c.items() if k != "page_snippet"} for c in candidates],
    })
    print(f"  [DISCOVER] {len(new_entities)} new entities identified")
    return new_entities


def set_status(project: Project, name: str, status: str) -> None:
    """Mark a discovered entity as new / accepted / dismissed (used by the UI)."""
    data = read_json(project.discovery_path, {}) or {}
    for e in data.get("entities", []):
        if e["name"] == name:
            e["status"] = status
    write_json(project.discovery_path, data)


if __name__ == "__main__":
    import sys
    proj = Project.from_csv(sys.argv[1]) if len(sys.argv) > 1 else Project.from_config()
    for e in discover(proj, force=True):
        print(f"  {e['name']}  {e['urls'][:1]}  — {e.get('evidence')}")
