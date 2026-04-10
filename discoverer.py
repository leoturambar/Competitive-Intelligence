# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — discoverer.py
# ─────────────────────────────────────────────
#
#  Searches DuckDuckGo for entities working on the configured topic
#  that are NOT already in ENTITIES. Returns a list of new entity
#  dicts ready to be injected into the scraper pipeline.
#
#  Called from main.py when --discover flag is set.
# ─────────────────────────────────────────────

import json
import re
import random
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import (
    ENTITIES, TOPIC, TOPIC_DESCRIPTION,
    REQUEST_TIMEOUT, REQUEST_DELAY, MAX_TEXT_CHARS, DDG_DELAY
)
from analyzer import _call_llm, _parse_json

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

DISCOVERY_N_NEW      = 5     # how many new entities to find
DISCOVERY_N_QUERIES  = 3     # how many DDG queries to run
DISCOVERY_PAGES_EACH = 3     # pages to fetch per query

# Varied queries to maximise coverage
DISCOVERY_QUERIES = [
    "{topic} company startup research",
    "{topic} lab university research group",
    "{topic} spinoff institute consortium",
]


def _ddg_links(query: str, max_results: int = DISCOVERY_PAGES_EACH) -> list:
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if href.startswith("http"):
                links.append(href)
            if len(links) >= max_results:
                break
        time.sleep(DDG_DELAY)
        return links
    except Exception as e:
        print(f"  [WARN] DDG query failed: {e}")
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
        print(f"  [WARN] Fetch failed {url}: {e}")
        return ""


def discover(force: bool = False) -> list:
    """
    Search DDG for new entities relevant to TOPIC.
    Returns a list of entity dicts: {name, urls, notes}
    ready to be appended to ENTITIES and fed into scrape_all().

    Results are cached in data/discovered.json.
    Set force=True to redo discovery.
    """
    cache_path = Path("data/discovered.json")

    if cache_path.exists() and not force:
        print("  [CACHE] discovery results")
        with open(cache_path) as f:
            return json.load(f)

    # Build set of already-known entity names (lowercase) for deduplication
    known_names = {e["name"].lower() for e in ENTITIES}
    known_urls  = {url for e in ENTITIES for url in e.get("urls", [])}

    # ── Step 1: collect raw text from DDG searches ────────────────────
    print("  [DISCOVER] Searching DuckDuckGo...")
    all_text = ""
    seen_urls = set()

    for q_template in DISCOVERY_QUERIES[:DISCOVERY_N_QUERIES]:
        query = q_template.format(topic=TOPIC)
        print(f"    Query: {query}")
        links = _ddg_links(query)

        for url in links:
            if url in seen_urls or url in known_urls:
                continue
            seen_urls.add(url)
            print(f"    → {url}")
            text = _fetch_text(url)
            if text:
                all_text += f"\n\n[Source: {url}]\n{text[:2000]}"
            time.sleep(REQUEST_DELAY)

    if not all_text.strip():
        print("  [WARN] No text collected for discovery — skipping")
        return []

    all_text = all_text[:MAX_TEXT_CHARS]

    # ── Step 2: LLM identifies new entities ───────────────────────────
    print("  [DISCOVER] Asking LLM to identify new entities...")

    already_known = "\n".join(f"- {e['name']}" for e in ENTITIES)

    prompt = f"""You are an expert analyst in {TOPIC}.

Context about the landscape:
{TOPIC_DESCRIPTION}

Below is text collected from web searches about this topic.
Your task: identify up to {DISCOVERY_N_NEW} organisations (companies, 
research labs, universities, spinoffs, consortia) that are working on 
"{TOPIC}" and are NOT already in this known list:

{already_known}

For each new entity return:
- "name": full official name
- "url": their most relevant webpage URL (homepage or specific research page)
- "entity_type": Company | Spinoff | Research Institute | University | Consortium | Other
- "notes": 1 sentence describing what they do in this space

Return ONLY a JSON object:
{{"new_entities": [
  {{"name": "...", "url": "...", "entity_type": "...", "notes": "..."}},
  ...
]}}

Rules:
- Only include entities genuinely working on {TOPIC}
- Do not invent entities — only include ones explicitly mentioned in the text
- If fewer than {DISCOVERY_N_NEW} are found, return only those found
- If none are found, return {{"new_entities": []}}
- No preamble, no markdown fences

Text:
---
{all_text}
---
"""

    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        raw_entities = data.get("new_entities", [])
    except Exception as e:
        print(f"  [ERROR] LLM discovery failed: {e}")
        return []

    # ── Step 3: format and deduplicate ────────────────────────────────
    new_entities = []
    for e in raw_entities:
        name = e.get("name", "").strip()
        url  = e.get("url",  "").strip()

        if not name or not url:
            continue
        if name.lower() in known_names:
            continue

        entity = {
            "name":  name,
            "urls":  [url],
            "notes": e.get("notes", ""),
            # mark as auto-discovered for traceability
            "_discovered": True,
        }
        new_entities.append(entity)
        known_names.add(name.lower())
        print(f"  [DISCOVER] Found: {name}")

    print(f"  [DISCOVER] {len(new_entities)} new entities identified")

    # Cache results
    Path("data").mkdir(exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(new_entities, f, indent=2)

    return new_entities


if __name__ == "__main__":
    results = discover(force=True)
    print(f"\n{len(results)} new entities found:")
    for e in results:
        print(f"  {e['name']} — {e['urls'][0]}")