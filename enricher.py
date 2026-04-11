# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — enricher.py
# ─────────────────────────────────────────────
#
#  Enriches analyzed entity data with three targeted passes:
#    1. plant_application  — DDG + LLM → concise description
#    2. notable_outputs    — DDG + LLM → list of dicts with label + url
#    3. key_people         — DDG + LLM → list of names with roles
#
#  Link resolution for notable_outputs (hybrid approach):
#    - If DOI found in text   → https://doi.org/{doi}
#    - If paper, no DOI       → Semantic Scholar API search
#    - If patent number found → https://patents.google.com/patent/{number}
#    - Otherwise              → no link (label only)
#
#  Reads from  : data/analyzed/<slug>.json
#  Writes to   : data/enriched/<slug>.json
# ─────────────────────────────────────────────

import json
import re
import random
import time
from pathlib import Path
from urllib.parse import unquote, quote

import requests
from bs4 import BeautifulSoup

import config
from analyzer import _call_llm, _parse_json

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

ENRICHED_DIR       = Path("data/enriched")
DDG_ENRICH_RESULTS = 2
MIN_CONTEXT_CHARS  = 500   # skip LLM call if context is too sparse


# ── DDG helpers ───────────────────────────────

def _ddg_links(query: str, max_results: int = DDG_ENRICH_RESULTS) -> list:
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if "uddg=" in href:
                try:
                    encoded = href.split("uddg=")[1].split("&")[0]
                    href = unquote(encoded)
                except Exception:
                    continue
            if href.startswith("http"):
                links.append(href)
            if len(links) >= max_results:
                break
        time.sleep(config.DDG_DELAY)
        return links
    except Exception as e:
        print(f"      [WARN] DDG query failed: {e}")
        return []


def _fetch_text(url: str) -> str:
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "noscript", "iframe", "svg"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        return re.sub(r"\s+", " ", text).strip()
    except Exception as e:
        print(f"      [WARN] Fetch failed {url}: {e}")
        return ""


def _ddg_text(query: str) -> str:
    links = _ddg_links(query)
    combined = ""
    for url in links:
        print(f"      [DDG] {url}")
        text = _fetch_text(url)
        if text:
            combined += f"\n\n[Source: {url}]\n{text}"
        time.sleep(config.REQUEST_DELAY)
    return combined[:config.MAX_TEXT_CHARS]


def _shorten_query(name: str, topic: str) -> str:
    """Shorten a long entity name into a compact DDG-friendly query."""
    stopwords = {
        "college", "department", "of", "and", "the", "for",
        "science", "technology", "institute", "division", "faculty",
        "school", "center", "centre",
    }
    name_words = [
        w for w in re.sub(r"[,\-—–]", " ", name).lower().split()
        if w not in stopwords and len(w) > 3
    ]
    name_part  = " ".join(name_words[:3])
    topic_words = [
        w for w in topic.lower().split()
        if w not in stopwords and len(w) > 3
    ][:3]
    topic_part = " ".join(topic_words)
    return f"{name_part} {topic_part}".strip()


# ── Link resolution ───────────────────────────

def _resolve_doi(doi: str) -> str:
    """Return DOI URL."""
    doi = doi.strip().lstrip("https://doi.org/").lstrip("doi.org/")
    return f"https://doi.org/{doi}"


def _semantic_scholar(title: str) -> str | None:
    """Search Semantic Scholar API for a paper by title. Returns URL or None."""
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "limit": 1, "fields": "title,url,externalIds"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        papers = data.get("data", [])
        if not papers:
            return None
        paper = papers[0]
        # Prefer DOI if available
        ext = paper.get("externalIds", {})
        if ext.get("DOI"):
            return f"https://doi.org/{ext['DOI']}"
        return paper.get("url")
    except Exception as e:
        print(f"      [WARN] Semantic Scholar failed: {e}")
        return None


def _google_patents_url(patent_number: str) -> str:
    """Build Google Patents URL from patent number."""
    # Normalise: remove spaces, keep alphanumeric
    num = re.sub(r"\s+", "", patent_number).upper()
    return f"https://patents.google.com/patent/{num}"


def _resolve_output_url(item: dict) -> str | None:
    """
    Hybrid link resolution for a single output item.
    item keys: type, title, year, doi, patent_number
    """
    typ   = (item.get("type") or "").lower()
    title = (item.get("title") or "").strip()

    # Patent number → Google Patents
    if "patent" in typ:
        return f"https://patents.google.com/?q={quote(title)}" if title else None

    # Paper/publication → Semantic Scholar
    if title and typ in ("paper", "publication", "article", "review", "preprint", ""):
        return _semantic_scholar(title)

    return None


# ── Enrichment passes ─────────────────────────

def _enrich_plant_application(name: str, existing: str, raw_text: str) -> str:
    print(f"    [ENRICH] domain_application")
    short = _shorten_query(name, config.TOPIC)
    ddg   = _ddg_text(f"{short} plant application")
    context = f"{raw_text}\n\n{ddg}" if ddg else raw_text

    if len(context.strip()) < MIN_CONTEXT_CHARS:
        return existing

    prompt = f"""You are an expert in {config.TOPIC}.

Given the text below about "{name}", extract a concise 1-2 sentence description
of how this entity applies organic electronics or bioelectronics specifically
to living plants.

Focus only on plant-specific applications (sensing, actuation, nutrient delivery,
plant-machine interfaces).

Text:
---
{context[:6000]}
---

Return ONLY a JSON object:
{{"domain_application": "..." or null}}

If no plant-specific application is clearly supported by the text, return null.
If you are not confident the answer is explicitly supported by the text, return null rather than guessing.
Do NOT invent or guess. No preamble, no markdown fences.
"""
    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        val  = data.get("domain_application")
        if val and str(val).lower() not in ("null", "none", ""):
            return str(val).strip()
    except Exception as e:
        print(f"      [WARN] LLM failed: {e}")
    return existing


def _enrich_notable_outputs(name: str, existing: str, raw_text: str) -> list:
    """
    Returns a list of dicts: {label, url}
    url may be None if no link could be resolved.
    """
    print(f"    [ENRICH] notable_outputs")

    short = _shorten_query(name, config.TOPIC)
    q1  = f"{short} paper publication"
    q2  = f"{short} patent product"
    ddg = _ddg_text(q1) + "\n\n" + _ddg_text(q2)
    context = f"{raw_text}\n\n{ddg}" if ddg else raw_text

    if len(context.strip()) < MIN_CONTEXT_CHARS:
        # Return existing as plain list if already structured, else wrap
        return _existing_to_links(existing)

    prompt = f"""You are an expert in {config.TOPIC}.

Given the text below about "{name}", extract notable outputs:
papers, patents, products, datasets, or tools they have produced.

For each item return a JSON object with:
- "type": paper | patent | product | tool | dataset
- "title": short label, max 8 words
- "year": year as string if available, else null

Return ONLY:
{{"notable_outputs": [
  {{"type": "...", "title": "...", "year": "..."}},
  ...
] or null}}

Maximum 6 items. Most relevant first.
Only include outputs explicitly mentioned in the text.
If you are not confident an output is real and in the text, omit it.
Do NOT invent titles, DOIs, or patent numbers. No preamble, no markdown fences.

Text:
---
{context[:6000]}
---
"""
    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        items = data.get("notable_outputs")

        if not items or not isinstance(items, list):
            return _existing_to_links(existing)

        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            year  = (item.get("year")  or "").strip()
            typ   = (item.get("type")  or "").strip()
            if not title or title.lower() in ("null", "none"):
                continue

            label = f"{typ.capitalize()}: {title}" if typ else title
            if year and year.lower() not in ("null", "none"):
                label += f" ({year})"

            print(f"      [LINK] resolving: {label}")
            url = _resolve_output_url(item)
            if url:
                print(f"             -> {url}")

            result.append({"label": label, "url": url})

        return result if result else _existing_to_links(existing)

    except Exception as e:
        print(f"      [WARN] LLM failed: {e}")
        return _existing_to_links(existing)


def _existing_to_links(existing: str) -> list:
    """Convert a legacy plain-text notable_outputs string to link list format."""
    if not existing or (isinstance(existing, str) and existing.strip().lower() in ("null", "none", "")):
        return []
    # If already a list of dicts (re-loaded from cache), return as-is
    if isinstance(existing, list):
        return existing
    # Split comma-separated string into label-only items
    parts = [p.strip() for p in str(existing).split(",") if p.strip()]
    return [{"label": p, "url": None} for p in parts]


def _enrich_key_people(name: str, existing: str, raw_text: str, topic: str) -> str:
    print(f"    [ENRICH] key_people")

    short = _shorten_query(name, topic)
    q1  = f"{short} researcher principal investigator"
    q2  = f"{short} team scientist lab plant bioelectronics"
    ddg = _ddg_text(q1) + "\n\n" + _ddg_text(q2)
    context = f"{raw_text}\n\n{ddg}" if ddg else raw_text

    if len(context.strip()) < MIN_CONTEXT_CHARS:
        return existing

    prompt = f"""You are an expert in {topic}.

Given the text below about "{name}", extract names of key researchers,
principal investigators, or team members working specifically on {topic}.

Prefer researchers directly involved in "{topic}" over general administrators.

Return ONLY:
{{"key_people": ["Name 1 (role if known)", ...] or null}}

Maximum 4 people.
Only include people explicitly named in the text.
If you are not confident a name is correct and present in the text, omit it.
Do NOT invent or guess names. No preamble, no markdown fences.

Text:
---
{context[:6000]}
---
"""
    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        val  = data.get("key_people")
        if val and isinstance(val, list) and len(val) > 0:
            clean = [str(x).strip() for x in val
                     if x and str(x).lower() not in ("null", "none", "")]
            if clean:
                return ", ".join(clean)
        elif val and isinstance(val, str) and val.lower() not in ("null", "none", ""):
            return val.strip()
    except Exception as e:
        print(f"      [WARN] LLM failed: {e}")
    return existing


# ── Main function ─────────────────────────────

def enrich_all(analyzed: dict, force: bool = False) -> dict:
    ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    for name, data in analyzed.items():
        slug  = re.sub(r"[^a-z0-9]+", "_", name.lower())[:60]
        cache = ENRICHED_DIR / f"{slug}.json"

        if cache.exists() and not force:
            print(f"  [CACHE] {name}")
            with open(cache) as f:
                results[name] = json.load(f)
            continue

        print(f"  [ENRICH] {name}")

        raw_path = Path("data/raw") / f"{slug}.json"
        raw_text = ""
        if raw_path.exists():
            with open(raw_path) as f:
                raw_text = json.load(f).get("text", "")

        enriched = dict(data)

        enriched["domain_application"] = _enrich_plant_application(
            name,
            existing = str(data.get("domain_application") or ""),
            raw_text = raw_text,
        )

        # notable_outputs is now a list of {label, url} dicts
        enriched["notable_outputs"] = _enrich_notable_outputs(
            name,
            existing = data.get("notable_outputs") or "",
            raw_text = raw_text,
        )

        enriched["key_people"] = _enrich_key_people(
            name,
            existing = str(data.get("key_people") or ""),
            raw_text = raw_text,
            topic    = config.TOPIC,
        )

        with open(cache, "w") as f:
            json.dump(enriched, f, indent=2)

        results[name] = enriched

    return results


if __name__ == "__main__":
    from analyzer import analyze_all
    from scraper  import scrape_all
    scraped  = scrape_all()
    analyzed = analyze_all(scraped)
    enriched = enrich_all(analyzed, force=True)
    print(json.dumps(list(enriched.values())[0], indent=2))