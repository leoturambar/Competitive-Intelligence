# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — enricher.py
# ─────────────────────────────────────────────
#
#  Adds evidence beyond the entity's own website, for ANY topic
#  (prompts are built from the project's topic and domain label):
#
#    1. OpenAlex research profile
#         institution match → topic publications per year, momentum,
#         most-cited topic papers (real DOIs), most active authors.
#    2. Web intelligence pass (DDG)
#         one news/funding/partnership search + the entity's own text →
#         one LLM call extracting recent developments, funding,
#         partnerships, a sharper domain application, extra people/outputs.
#    3. Link resolution for notable outputs
#         papers → OpenAlex title lookup (accepted only on a close title
#         match), patents → Google Patents search, products → website.
#
#  Cache: data/projects/<slug>/enriched/<entity>.json
# ─────────────────────────────────────────────

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import llm
import openalex
import web
from analyzer import OUTPUT, PERSON
from llm import call_json, nullable
from project import Project, fingerprint, load_cached, save_cached

ENRICH_VERSION    = 5
MIN_CONTEXT_CHARS = 400
NEWS_PAGES        = 2

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "domain_application": nullable("string"),
        "funding_or_status":  nullable("string"),
        "recent_developments": {"type": "array", "items": {
            "type": "object",
            "properties": {"date": nullable("string"), "event": {"type": "string"},
                           "source_url": nullable("string")},
            "required": ["date", "event", "source_url"]}},
        "partners":        {"type": "array", "items": {"type": "string"}},
        "key_people":      {"type": "array", "items": PERSON},
        "notable_outputs": {"type": "array", "items": OUTPUT},
        "relevance_score": {"type": "integer"},
        "relevance_rationale": {"type": "string"},
    },
}
ENRICH_SCHEMA["required"] = list(ENRICH_SCHEMA["properties"].keys())


# ── 1. OpenAlex ───────────────────────────────

def research_profile(project: Project, name: str, website: str | None,
                     oa_id: str | None = None) -> dict | None:
    """OpenAlex profile; a known institution ID (from discovery) skips name matching."""
    inst = (openalex.institution(oa_id) if oa_id else None) or openalex.match_institution(name, website)
    if not inst:
        return None
    activity = openalex.topic_activity(inst["id"], openalex.topic_query(project.topic, project.keywords))
    return {"institution": inst, **activity}


# ── 2. Web intelligence ───────────────────────

def _news_context(name: str, website: str | None) -> tuple[str, list[dict]]:
    short = web.shorten_query(name, "", max_name_words=4)
    own = web.domain_of(website) if website else ""
    results = web.ddg_search(f"{short} funding OR partnership OR launch OR award", max_results=8)
    chunks, sources = [], []
    snippets = [f"- {r.title}: {r.snippet} ({r.url})" for r in results if r.snippet]
    if snippets:
        chunks.append("[Search result snippets]\n" + "\n".join(snippets[:8]))
    fetched = 0
    for r in results:
        if fetched >= NEWS_PAGES:
            break
        if own and web.domain_of(r.url) == own:
            continue
        page = web.fetch(r.url)
        sources.append({"url": r.url, "title": page.title or r.title, "ok": page.ok, "kind": "news"})
        if page.ok and page.text:
            chunks.append(f"[Source: {r.url} | {page.title}]\n{page.text[:3500]}")
            fetched += 1
    return "\n\n".join(chunks), sources


def _research_brief(research: dict | None) -> str:
    if not research:
        return "No publication record matched in OpenAlex."
    titles = "; ".join(w["title"] for w in research.get("top_works", [])[:4] if w.get("title"))
    return (f"OpenAlex: {research['institution']['display_name']} has {research.get('topic_works', 0)} papers "
            f"matching the topic (3-yr momentum {research.get('momentum')}). Top papers: {titles or 'none'}.")


def web_intelligence(project: Project, name: str, raw_text: str, website: str | None,
                     prior: dict | None = None, research: dict | None = None) -> tuple[dict, list]:
    """One LLM pass over own pages + news; also re-scores relevance with all evidence."""
    prior = prior or {}
    news, sources = _news_context(name, website)
    context = f"{raw_text[:7000]}\n\n{news}".strip()
    if len(context) < MIN_CONTEXT_CHARS:
        return {}, sources

    prompt = f"""Landscape: {project.topic}
Scope: {project.description or project.topic}
Entity: {name}

Using ONLY the text below (the entity's own pages, search snippets and news pages), extract:
- domain_application: 1–2 sentences on how "{name}" specifically applies its work to "{project.topic}" ({project.domain_label}). Null if not clearly supported.
- funding_or_status: funding rounds, grants, investors, acquisitions, revenue or headcount if stated; else null.
- recent_developments: up to 5 dated events (launches, funding, partnerships, awards, key papers), newest first. Use the source URL shown in the text.
- partners: named partner organisations (max 6).
- key_people: named people with roles who work on this topic at the entity (max 5).
- notable_outputs: papers/patents/products explicitly named (max 6, title ≤ 10 words).
- relevance_score (1–5) and relevance_rationale (one sentence): re-assess how central "{name}" is to
  "{project.topic}" using ALL evidence — the web text AND the publication record (papers listed there
  were selected because their title/abstract match the topic, so they ARE evidence of topic activity).
  5 = core player working directly on the topic; 4 = substantial dedicated activity; 3 = clear but partial;
  2 = adjacent; 1 = unrelated. The first-pass score from the website alone was
  {prior.get("relevance_score")} ({prior.get("relevance_rationale") or "no rationale"}).
  For a large university, topic papers may come from any of its groups — judge the named entity.

Make sure each item really concerns "{name}" and not a similarly named organisation. Omit anything you are not sure about.

Evidence:
---
[Publication record]
{_research_brief(research)}

{context[:12000]}
---"""
    try:
        return call_json(prompt, schema=ENRICH_SCHEMA, max_tokens=2500), sources
    except Exception as e:
        print(f"      [WARN] enrichment LLM failed: {e}")
        return {}, sources


# ── 3. Merging & link resolution ──────────────

def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def resolve_output(item: dict, website: str | None) -> dict:
    item = dict(item)
    if item.get("url"):
        return item
    typ, title = (item.get("type") or "").lower(), (item.get("title") or "").strip()
    if typ == "patent" and title:
        num = re.search(r"\b([A-Z]{2}\s?\d{6,}\s?[A-Z]\d?)\b", title)
        item["url"] = (f"https://patents.google.com/patent/{num.group(1).replace(' ', '')}" if num
                       else f"https://patents.google.com/?q={quote(title)}")
        item["link_kind"] = "patent" if num else "search"
    elif typ == "paper" and title:
        work = openalex.find_work(title)
        if work:
            item.update(url=work["url"], doi=work.get("doi"), cited_by=work.get("cited_by"),
                        year=item.get("year") or work.get("year"), link_kind="verified")
    elif typ in ("product", "tool") and website:
        item["url"], item["link_kind"] = website, "website"
    return item


def merge_outputs(*lists, website=None, limit=10) -> list[dict]:
    merged, seen = [], set()
    for lst in lists:
        for o in lst or []:
            if isinstance(o, str):
                o = {"type": "other", "title": o, "year": None}
            if not isinstance(o, dict):
                continue
            title = o.get("title") or o.get("label")
            key = _norm_title(title)
            if not key or key in seen:
                continue
            seen.add(key)
            o = {**o, "title": title}
            o.pop("label", None)
            merged.append(o)
    merged = [resolve_output(o, website) for o in merged[:limit]]
    return merged


def merge_people(*lists, limit=8) -> list[dict]:
    out, seen = [], set()
    for lst in lists:
        for p in lst or []:
            if isinstance(p, str):
                p = {"name": p, "role": None}
            name = (p.get("name") or "").strip()
            key = re.sub(r"[^a-z]", "", name.lower())
            # match "Magnus Berggren" with "Magnus Berggren A" / initials variants
            dup = next((q for q in out if key[:12] and (key[:12] in q["_k"] or q["_k"][:12] in key)), None)
            if not name or dup is not None:
                if dup is not None and not dup.get("role") and p.get("role"):
                    dup["role"] = p["role"]
                if dup is not None and p.get("topic_works") and not dup.get("topic_works"):
                    dup["topic_works"], dup["url"] = p["topic_works"], p.get("url")
                continue
            seen.add(key)
            out.append({**p, "name": name, "_k": key})
    for p in out:
        p.pop("_k", None)
    return out[:limit]


FLOOR_MIN_PAPERS = 5


def bibliometric_floor(name: str, research: dict | None) -> int | None:
    """Minimum relevance implied by publication evidence.

    Applies only when the OpenAlex institution IS the entity (same distinctive
    name words) — never for a lab inside a big university, whose topic papers
    may come from other groups. ≥5 topic papers → at least 3; ≥15 → at least 4.
    """
    if not research or (research.get("topic_works") or 0) < FLOOR_MIN_PAPERS:
        return None
    inst_name = (research.get("institution") or {}).get("display_name") or ""
    if openalex._tokens(inst_name) != openalex._tokens(name):
        return None
    return 4 if research["topic_works"] >= 15 else 3


def enrich_entity(project: Project, name: str, rec: dict, raw: dict, use_web: bool = True,
                  use_openalex: bool = True) -> dict:
    enriched = dict(rec)
    enriched.pop("_fp", None)
    website = rec.get("website") or (rec.get("entity_urls") or [None])[0]
    sources = list(rec.get("sources") or [])

    research = None
    if use_openalex and rec.get("is_organization", True):
        print(f"    [OPENALEX] {name}")
        try:
            research = research_profile(project, name, website,
                                        rec.get("openalex_id") or raw.get("openalex_id"))
        except Exception as e:
            print(f"      [WARN] OpenAlex failed: {e}")
    enriched["research"] = research

    intel = {}
    if use_web:
        print(f"    [WEB] {name}")
        intel, news_sources = web_intelligence(project, name, raw.get("text", ""), website,
                                               prior=rec, research=research)
        sources += news_sources

    try:
        new_score = max(1, min(5, int(intel.get("relevance_score"))))
    except (TypeError, ValueError):
        new_score = None
    if new_score and intel.get("relevance_rationale"):
        enriched["relevance_first_pass"] = rec.get("relevance_score")
        enriched["relevance_score"] = new_score
        enriched["relevance_rationale"] = intel["relevance_rationale"]

    floor = bibliometric_floor(name, research)
    if floor and (enriched.get("relevance_score") or 0) < floor:
        enriched.setdefault("relevance_first_pass", rec.get("relevance_score"))
        enriched["relevance_score"] = floor
        enriched["relevance_rationale"] = (
            f"Raised to {floor}: {research['topic_works']} topic papers in OpenAlex for this institution. "
            + (enriched.get("relevance_rationale") or ""))

    if intel.get("domain_application"):
        enriched["domain_application"] = intel["domain_application"]
    if intel.get("funding_or_status") and not rec.get("funding_or_status"):
        enriched["funding_or_status"] = intel["funding_or_status"]
    enriched["recent_developments"] = [d for d in intel.get("recent_developments") or []
                                       if isinstance(d, dict) and d.get("event")][:5]
    enriched["partners"] = [p for p in intel.get("partners") or [] if isinstance(p, str) and p.strip()][:6]

    oa_people = [{"name": a["name"], "role": f"{a['topic_works']} topic papers",
                  "topic_works": a["topic_works"], "url": a["url"]}
                 for a in (research or {}).get("top_authors", [])[:4]]
    enriched["key_people"] = merge_people(rec.get("key_people"), intel.get("key_people"), oa_people)

    oa_works = (research or {}).get("top_works", [])[:4]
    enriched["notable_outputs"] = merge_outputs(oa_works, rec.get("notable_outputs"),
                                                intel.get("notable_outputs"), website=website)

    if research and not enriched.get("country") and research["institution"].get("country_code"):
        enriched["country"] = openalex.country_name(research["institution"]["country_code"])
    enriched["sources"] = sources
    enriched["enriched"] = True
    return enriched


def enrich_all(project: Project, analyzed: dict, scraped: dict, force: bool = False,
               on_progress=None, use_web: bool = True, use_openalex: bool = True) -> dict:
    results, todo = {}, []
    for name, rec in analyzed.items():
        if rec.get("_error"):
            results[name] = rec           # nothing reliable to enrich
            continue
        fp = fingerprint(rec.get("_fp"), ENRICH_VERSION, use_web, use_openalex, llm.describe())
        cached = None if force else load_cached(project.entity_path("enriched", name), fp)
        if cached:
            print(f"  [CACHE] {name}")
            results[name] = cached
        else:
            todo.append((name, rec, fp))

    total, done = len(analyzed), len(results)
    if on_progress:
        on_progress(done, total, "")

    def _work(item):
        name, rec, fp = item
        print(f"  [ENRICH] {name}")
        try:
            out = enrich_entity(project, name, rec, scraped.get(name, {}), use_web, use_openalex)
            return name, save_cached(project.entity_path("enriched", name), out, fp)
        except Exception as e:
            print(f"    [ERROR] enrichment failed for {name}: {e}")
            return name, rec

    workers = 3 if llm.backend() == "claude" else 2
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(_work, t) for t in todo]):
            name, rec = fut.result()
            results[name] = rec
            done += 1
            if on_progress:
                on_progress(done, total, name)

    return {n: results[n] for n in analyzed if n in results}
