# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — discoverer.py
# ─────────────────────────────────────────────
#
#  Searches DuckDuckGo for pages relevant to TOPIC — including academic
#  papers — and extracts new entities (labs, groups, companies) from them.
#
#  Key insight: for niche scientific topics, DDG mostly returns papers.
#  Papers contain author affiliations → those are the real entities.
#  The LLM extracts institutions from author metadata, not just company names.
#
#  Pipeline:
#    1. DDG search (varied queries, paper-inclusive)
#    2. Fetch accessible pages (papers, lab pages, company pages)
#    3. LLM extracts research groups / institutions / companies from text,
#       using author affiliations as primary signal for academic entities
#    4. Deduplicate against known ENTITIES, cache, return
#
#  Called from main.py when --discover flag is set.
# ─────────────────────────────────────────────

import json
import random
import re
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from config import (
    ENTITIES, TOPIC, TOPIC_DESCRIPTION,
    REQUEST_TIMEOUT, REQUEST_DELAY, DDG_DELAY, MAX_TEXT_CHARS,
)
from analyzer import _call_llm, _parse_json

# ── Settings ──────────────────────────────────
DISCOVERY_N_NEW      = 10    # max new entities to return
DISCOVERY_N_QUERIES  = 5    # how many DDG queries to run
DISCOVERY_PAGES_EACH = 8    # pages to fetch per query

# Queries designed to find both companies AND academic papers/labs.
# Papers are valuable: they contain author affiliations which identify
# research groups working on the topic.
DISCOVERY_QUERIES = [
    "plant bioelectronics company startup",
    "electronic plants research lab university",
    "organic electronics plant sensing",
    "OECT plant interface research",
    "conducting polymer plant bioelectronics lab",
]

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


# ── Helpers ───────────────────────────────────

def _ddg_links(query: str, max_results: int = DISCOVERY_PAGES_EACH) -> list:
    """Search DDG and return decoded result URLs."""
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            # DDG wraps links as //duckduckgo.com/l/?uddg=<encoded_url>
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
        time.sleep(DDG_DELAY)
        return links
    except Exception as e:
        print(f"    [WARN] DDG query failed: {e}")
        return []


def _fetch_text(url: str) -> str:
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "noscript", "iframe", "svg"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        return re.sub(r"\s+", " ", text).strip()
    except Exception as e:
        print(f"    [WARN] Fetch failed {url}: {e}")
        return ""


def _extract_affiliations(text: str, url: str) -> str:
    """
    For academic paper pages, extract author/affiliation sections
    which are the most valuable signal for entity discovery.
    Falls back to full text if no affiliation section found.
    """
    # Common section markers in paper pages
    markers = [
        "author", "affiliation", "institution", "department",
        "university", "institute", "laboratory", "lab ",
    ]

    lines = text.split(" ")
    best_start = 0
    best_score = 0

    # Find the region with highest density of affiliation keywords
    window = 300  # words
    for i in range(0, max(1, len(lines) - window), 50):
        chunk = " ".join(lines[i:i + window]).lower()
        score = sum(chunk.count(m) for m in markers)
        if score > best_score:
            best_score = score
            best_start = i

    if best_score > 3:
        # Return the dense region plus some surrounding context
        start = max(0, best_start - 50)
        end   = min(len(lines), best_start + window + 50)
        extracted = " ".join(lines[start:end])
        return f"[Affiliations extracted from {url}]\n{extracted}"

    # No affiliation section found — return beginning of text
    return f"[Source: {url}]\n{text[:2000]}"


def _generate_fallback_queries(used_queries: list) -> list:
    """Ask LLM for broader queries if initial ones returned nothing."""
    prompt = f"""You are helping search for organisations working on "{TOPIC}".

These search queries returned no results:
{chr(10).join(f'- {q}' for q in used_queries)}

Generate 3 alternative search queries that are:
- Shorter and broader (2-4 words each)
- Still focused on finding research groups, labs, or companies in this field
- Using different terminology or synonyms

Return ONLY a JSON object:
{{"queries": ["query 1", "query 2", "query 3"]}}
No preamble, no markdown fences."""
    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        return data.get("queries", [])
    except Exception as e:
        print(f"    [WARN] Could not generate fallback queries: {e}")
        return []


def _regex_affiliations(text: str) -> list:
    """
    Extract institution names directly from paper affiliation patterns.
    Returns list of raw affiliation strings.
    """
    # Pattern: numbered/lettered affiliation lines
    # e.g. "1. Laboratory of Organic Electronics, Linköping University"
    # e.g. "a Department of Chemistry, Uppsala University, Sweden"
    patterns = [
        r'\d+[\.\s]+([A-Z][^,\n]{5,80},\s*[A-Z][^,\n]{3,60})',
        r'[a-z][\.\s]+([A-Z][^,\n]{5,80},\s*[A-Z][^,\n]{3,60})',
        r'(?:Department|Laboratory|Institute|Center|Centre|School)\s+of\s+[^,\n]{3,60},\s*[A-Z][^,\n]{3,60}',
    ]
    found = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        found.extend(matches)
    # Deduplicate and clean
    seen = set()
    clean = []
    for f in found:
        f = f.strip()
        if f.lower() not in seen and len(f) > 15:
            seen.add(f.lower())
            clean.append(f)
    return clean[:20]  # max 20 affiliations


# ── Main function ─────────────────────────────

def discover(force: bool = False) -> list:
    """
    Search DDG for new entities relevant to TOPIC.
    Accepts both company/lab pages AND academic papers as input —
    author affiliations in papers are treated as entity signals.

    Results cached in data/discovered.json.
    Set force=True to redo discovery.
    Returns list of entity dicts ready for scrape_all().
    """
    cache_path = Path("data/discovered.json")

    if cache_path.exists() and not force:
        print("  [CACHE] discovery results")
        with open(cache_path) as f:
            return json.load(f)

    known_names = {e["name"].lower() for e in ENTITIES}
    known_urls  = {url for e in ENTITIES for url in e.get("urls", [])}

    # ── Step 1: collect text from DDG ─────────────────────
    print("  [DISCOVER] Searching DuckDuckGo...")
    all_text     = ""
    seen_urls    = set()
    used_queries = list(DISCOVERY_QUERIES[:DISCOVERY_N_QUERIES])

    for query in used_queries:
        print(f"    Query: {query}")
        links = _ddg_links(query)

        if not links:
            print(f"    -> no links")
            continue

        for url in links:
            if url in seen_urls or url in known_urls:
                continue
            seen_urls.add(url)
            print(f"    -> {url}")
            text = _fetch_text(url)
            if text:
                extracted = _extract_affiliations(text, url)
                all_text += f"\n\n{extracted}"
                print(f"      ({len(text)} chars raw, {len(extracted)} extracted)")
            time.sleep(REQUEST_DELAY)

    # Fallback if nothing collected
    if not all_text.strip():
        print("  [WARN] Initial queries returned nothing — trying LLM fallback queries...")
        fallback_queries = _generate_fallback_queries(used_queries)
        for query in fallback_queries:
            print(f"    Fallback: {query}")
            links = _ddg_links(query)
            for url in links:
                if url in seen_urls or url in known_urls:
                    continue
                seen_urls.add(url)
                print(f"    -> {url}")
                text = _fetch_text(url)
                if text:
                    extracted = _extract_affiliations(text, url)
                    all_text += f"\n\n{extracted}"
                    print(f"      ({len(text)} chars raw, {len(extracted)} extracted)")
                time.sleep(REQUEST_DELAY)

    if not all_text.strip():
        print("  [WARN] No text collected — skipping discovery")
        return []

    # ── Step 2: LLM extracts entities ─────────────────────
    print("  [DISCOVER] Asking LLM to identify new entities...")

    already_known = "\n".join(f"- {e['name']}" for e in ENTITIES)

    affiliations = _regex_affiliations(all_text)
    affiliation_block = "\n".join(f"- {a}" for a in affiliations)

    prompt = f"""You are an expert analyst in {TOPIC}.

Here are author affiliations extracted from academic papers on {TOPIC}:

Context:
{TOPIC_DESCRIPTION}

Here are author affiliations extracted from academic papers on {TOPIC}:

{affiliation_block}

From this list, identify up to {DISCOVERY_N_NEW} research groups or institutions
NOT already in this known list:

{already_known}

For each new entity return:
- "name": institution name + lab/group name if available
- "url": use the source paper URL as "url" if no specific lab page is known
- "entity_type": Company | Spinoff | Research Institute | University | Consortium | Other
- "notes": 1 sentence on what they do on {TOPIC}
- "source_url": leave empty or repeat url

Rules:
- Only include entities from the affiliation list above — do NOT invent
- Do NOT use generic homepages — use the paper URL if no lab page is known
- If none qualify, return {{"new_entities": []}}

Return ONLY a JSON object:
{{"new_entities": [
  {{"name": "...", "url": "...", "entity_type": "...", "notes": "...", "source_url": "..."}},
  ...
]}}
No preamble, no markdown fences.
"""

    try:
        raw          = _call_llm(prompt)
        data         = _parse_json(raw)
        raw_entities = data.get("new_entities", [])
    except Exception as e:
        print(f"  [ERROR] LLM discovery failed: {e}")
        return []

    # ── Step 3: format, validate, deduplicate ─────────────
    new_entities = []
    for e in raw_entities:
        name       = e.get("name", "").strip()
        url        = e.get("url", "").strip()
        source_url = e.get("source_url", "").strip()

        if not name:
            continue
        if name.lower() in known_names:
            continue

        # Fall back to source paper URL if no specific URL
        if not url:
            url = source_url

        # Reject entities with only a generic homepage (no path beyond domain)
        parsed_path = re.sub(r"https?://[^/]+", "", url).strip("/")
        if not name:
            continue

        entity = {
            "name":        name,
            "urls":        [url] if url else [],
            "notes":       e.get("notes", ""),
            "_discovered": True,
            "_source":     source_url,
        }
        new_entities.append(entity)
        known_names.add(name.lower())
        print(f"  [DISCOVER] Found: {name}")
        if url:
            print(f"             URL : {url}")

    print(f"  [DISCOVER] {len(new_entities)} new entities identified")

    Path("data").mkdir(exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(new_entities, f, indent=2)

    return new_entities


if __name__ == "__main__":
    results = discover(force=True)
    print(f"\n{len(results)} new entities found:")
    for e in results:
        url = e["urls"][0] if e["urls"] else "—"
        print(f"  {e['name']}")
        print(f"    URL  : {url}")
        print(f"    Notes: {e.get('notes', '')}")