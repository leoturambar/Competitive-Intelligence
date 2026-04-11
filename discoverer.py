# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — discoverer.py
# ─────────────────────────────────────────────
#
#  Pipeline:
#    1. DDG search → raw text from papers + lab pages
#    2. _regex_affiliations → extract candidate affiliation strings
#    3. _verify_affiliation → DDG each candidate with topic keywords
#         accept if DDG finds a page containing topic keywords
#         reject otherwise (filters addresses, metadata, noise)
#    4. LLM receives only verified affiliations → classifies + deduplicates
#    5. Return new entity dicts for scrape_all()
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
    DISCOVERY_QUERIES, TOPIC_KEYWORDS,
)
from analyzer import _call_llm, _parse_json

# ── Settings ──────────────────────────────────
DISCOVERY_N_NEW      = 10   # max new entities to return
DISCOVERY_N_QUERIES  = 5    # how many DDG queries to run
DISCOVERY_PAGES_EACH = 6    # pages to fetch per query

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


# ── Network helpers ───────────────────────────

def _ddg_links(query: str, max_results: int = DISCOVERY_PAGES_EACH) -> list:
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if "uddg=" in href:
                try:
                    href = unquote(href.split("uddg=")[1].split("&")[0])
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


def _fetch_text(url: str, max_chars: int = 4000) -> str:
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "noscript", "iframe", "svg"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception as e:
        print(f"    [WARN] Fetch failed {url}: {e}")
        return ""


# ── Affiliation extraction ────────────────────

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

    # Extract lines matching affiliation patterns
    patterns = [
        r'\d+[\.\s]+([A-Z][^,\n]{5,80},\s*[A-Z][^,\n]{3,60})',
        r'[a-z][\.\s]+([A-Z][^,\n]{5,80},\s*[A-Z][^,\n]{3,60})',
        r'(?:Department|Laboratory|Lab\b|Institute|Center|Centre|School|College)\s+of\s+[^,\n]{3,60},\s*[A-Z][^,\n]{3,60}',
        r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+University[^,\n]{0,40}',
    ]

    found = []
    seen  = set()
    for pattern in patterns:
        for m in re.finditer(pattern, region):
            s = m.group(0).strip() if m.lastindex is None else m.group(1).strip()
            s = re.sub(r"\s+", " ", s).strip()
            if len(s) > 15 and s.lower() not in seen:
                seen.add(s.lower())
                found.append(s)

    return found[:30]


# ── Verification step ─────────────────────────

def _verify_affiliation(affiliation: str, topic: str) -> dict | None:
    """
    Verify that an affiliation string corresponds to a real organisation
    working on the topic by searching DDG and checking page content.

    Returns {"name": cleaned_name, "url": url} if verified, None otherwise.
    """
    # Quick pre-filter: reject obvious non-organisations
    noise_patterns = [
        r"\d{3,}",           # long numbers (postal codes, phone)
        r"\b(Street|Avenue|Road|Boulevard|Lane|Drive|Way|Rd\.|Ave\.)\b",
        r"Find articles by",
        r"doi:",
        r"https?://",
        r"^\s*\d",           # starts with number
    ]
    for pat in noise_patterns:
        if re.search(pat, affiliation, re.IGNORECASE):
            return None

    # Must contain at least one organisation indicator
    org_indicators = [
        "university", "universit", "institute", "laboratory",
        "lab ", "center", "centre", "department", "college",
        "school", "hospital", "foundation", "agency", "gmbh",
        "inc.", "corp", "ltd", "s.a.", "s.r.l",
    ]
    if not any(ind in affiliation.lower() for ind in org_indicators):
        return None

    # DDG verification search
    query = f"{affiliation} {topic}"
    print(f"    [VERIFY] {affiliation[:60]}...")
    links = _ddg_links(query, max_results=2)

    for url in links:
        text = _fetch_text(url, max_chars=3000)
        if not text:
            continue
        text_lower = text.lower()
        # Check that the page is actually relevant to the topic
        keyword_hits = sum(1 for kw in TOPIC_KEYWORDS if kw.lower() in text_lower)
        if keyword_hits >= 2:
            print(f"             OK  ({keyword_hits} keyword hits) -> {url}")
            return {"name": affiliation, "url": url}
        time.sleep(REQUEST_DELAY)

    return None


# ── Fallback query generation ─────────────────

def _generate_fallback_queries(used_queries: list) -> list:
    prompt = f"""You are helping search for organisations working on "{TOPIC}".

These search queries returned no results:
{chr(10).join(f'- {q}' for q in used_queries)}

Generate 3 alternative search queries (2-4 words each, different terminology).

Return ONLY: {{"queries": ["query 1", "query 2", "query 3"]}}
No preamble, no markdown fences."""
    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        return data.get("queries", [])
    except Exception:
        return []


# ── Main function ─────────────────────────────

def discover(force: bool = False, on_status=None) -> list:
    """
    Discover new entities relevant to TOPIC via DDG + affiliation verification.

    Flow:
      1. DDG searches collect raw text (papers, lab pages)
      2. _regex_affiliations extracts candidate strings from each page
      3. _verify_affiliation DDG-checks each candidate with topic keywords
      4. LLM classifies verified affiliations into entity dicts
      5. Deduplicate against known ENTITIES, cache, return

    Results cached in data/discovered.json.
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
    page_texts = {}   # url -> text (keep source mapping)
    seen_urls  = set()
    used_queries = []

    for query in DISCOVERY_QUERIES[:DISCOVERY_N_QUERIES]:
        used_queries.append(query)
        print(f"    Query: {query}")
        links = _ddg_links(query)

        if not links:
            print("    -> no links")
            continue

        for url in links:
            if url in seen_urls or url in known_urls:
                continue
            seen_urls.add(url)
            text = _fetch_text(url, max_chars=6000)
            if text:
                page_texts[url] = text
                print(f"    -> {url}  ({len(text)} chars)")
            time.sleep(REQUEST_DELAY)

    # Fallback
    if not page_texts:
        print("  [WARN] Initial queries returned nothing — trying fallback...")
        for query in _generate_fallback_queries(used_queries):
            print(f"    Fallback: {query}")
            for url in _ddg_links(query):
                if url in seen_urls or url in known_urls:
                    continue
                seen_urls.add(url)
                text = _fetch_text(url, max_chars=6000)
                if text:
                    page_texts[url] = text
                time.sleep(REQUEST_DELAY)

    if not page_texts:
        print("  [WARN] No text collected — skipping discovery")
        return []

    # ── Step 2: extract affiliation candidates ─────────────
    print("  [DISCOVER] Extracting affiliation candidates...")
    candidates = []
    seen_candidates = set()

    for url, text in page_texts.items():
        affiliations = _regex_affiliations(text)
        for aff in affiliations:
            if aff.lower() not in seen_candidates:
                seen_candidates.add(aff.lower())
                candidates.append(aff)

    print(f"  [DISCOVER] {len(candidates)} candidates extracted")

    if on_status:
        on_status("discovery", f"{len(candidates)} candidates, verifying...")

    if not candidates:
        print("  [WARN] No affiliation candidates found")
        return []

    # ── Step 3: verify each candidate ─────────────────────
    print("  [DISCOVER] Verifying candidates via DDG...")
    verified = []

    for i, candidate in enumerate(candidates):
        # Skip if matches a known entity name
        if any(known.lower() in candidate.lower() or
               candidate.lower() in known.lower()
               for known in known_names):
            print(f"    [SKIP] already known: {candidate[:50]}")
            continue

        result = _verify_affiliation(candidate, TOPIC)
        if on_status and i % 3 == 0:
            on_status("discovery", f"verifying {i+1}/{len(candidates)}...")
        if result:
            # Skip if verified URL matches a known URL
            if result["url"] in known_urls:
                print(f"    [SKIP] URL already known")
                continue
            verified.append(result)

    print(f"  [DISCOVER] {len(verified)} verified candidates")

    if not verified:
        print("  [WARN] No candidates passed verification")
        Path("data").mkdir(exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump([], f)
        return []

    # ── Step 4: LLM classifies verified candidates ─────────
    print("  [DISCOVER] Classifying with LLM...")

    already_known = "\n".join(f"- {e['name']}" for e in ENTITIES)
    candidate_block = "\n".join(
        f"- {v['name']}  (verified URL: {v['url']})"
        for v in verified
    )

    prompt = f"""You are an expert analyst in {TOPIC}.

Context:
{TOPIC_DESCRIPTION}

These research organisations have been verified as working on {TOPIC}
(each was confirmed by finding relevant topic keywords on their web page):

{candidate_block}

From this list, select up to {DISCOVERY_N_NEW} organisations NOT already known:

{already_known}

For each selected entity return:
- "name": clean official name (e.g. "Uppsala University — Plant Bioelectronics Group")
- "url": the verified URL provided above
- "entity_type": Company | Spinoff | Research Institute | University | Consortium | Other
- "notes": 1 sentence on what they do on {TOPIC}

Rules:
- Only use names and URLs from the verified list above
- Do NOT invent names or URLs
- If all are already known, return {{"new_entities": []}}

Return ONLY:
{{"new_entities": [
  {{"name": "...", "url": "...", "entity_type": "...", "notes": "..."}},
  ...
]}}
No preamble, no markdown fences."""

    try:
        raw          = _call_llm(prompt)
        data         = _parse_json(raw)
        raw_entities = data.get("new_entities", [])
    except Exception as e:
        print(f"  [ERROR] LLM classification failed: {e}")
        raw_entities = []

    # ── Step 5: build final entity list ───────────────────
    new_entities = []
    for e in raw_entities:
        name = (e.get("name") or "").strip()
        url  = (e.get("url")  or "").strip()

        if not name or name.lower() in known_names:
            continue

        entity = {
            "name":        name,
            "urls":        [url] if url else [],
            "notes":       e.get("notes", ""),
            "_discovered": True,
        }
        new_entities.append(entity)
        known_names.add(name.lower())
        print(f"  [DISCOVER] Found: {name}")

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