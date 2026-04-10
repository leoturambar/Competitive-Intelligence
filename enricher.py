# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — enricher.py
# ─────────────────────────────────────────────
#
#  Enriches analyzed entity data with three targeted passes:
#    1. plant_application  — DDG "{name} plant application organic electronics"
#    2. notable_outputs    — DDG "{name} paper publication patent"
#    3. key_people         — DDG "{name} researcher principal investigator plant"
#
#  Reads from  : data/analyzed/<slug>.json
#  Writes to   : data/enriched/<slug>.json
#  Falls back to original value if enrichment fails or adds nothing.
# ─────────────────────────────────────────────

import json
import re
import random
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from config import (
    LLM_BACKEND, TOPIC,
    REQUEST_TIMEOUT, REQUEST_DELAY, MAX_TEXT_CHARS, DDG_DELAY
)
from analyzer import _call_llm, _parse_json

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

ENRICHED_DIR      = Path("data/enriched")
DDG_ENRICH_RESULTS = 2      # pages to fetch per DDG query
DDG_MIN_CHARS      = 300    # min chars to consider DDG result useful


# ── DDG helpers ───────────────────────────────

def _ddg_links(query: str, max_results: int = DDG_ENRICH_RESULTS) -> list:
    """Return up to max_results URLs from a DuckDuckGo HTML search."""
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
        print(f"      [WARN] DDG query failed: {e}")
        return []


def _fetch_text(url: str) -> str:
    """Fetch URL and return cleaned text."""
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
        print(f"      [WARN] Fetch failed {url}: {e}")
        return ""


def _ddg_text(query: str) -> str:
    """Search DDG and return combined text from top pages."""
    links = _ddg_links(query)
    combined = ""
    for url in links:
        print(f"      [DDG] {url}")
        text = _fetch_text(url)
        if text:
            combined += f"\n\n[Source: {url}]\n{text}"
        time.sleep(REQUEST_DELAY)
    return combined[:MAX_TEXT_CHARS]


def _shorten_query(name: str, topic: str) -> str:
    """
    Shorten a long entity name into a compact DDG-friendly query.
    Removes generic words and keeps distinctive terms.
    """
    # Words to drop — too generic or too long to help DDG
    stopwords = {
        "college", "department", "of", "and", "the", "for",
        "science", "technology", "institute", "division", "faculty",
        "school", "center", "centre",
    }
    # Keep words from the name that are distinctive (>3 chars, not stopwords)
    name_words = [
        w for w in re.sub(r"[,\-—–]", " ", name).lower().split()
        if w not in stopwords and len(w) > 3
    ]
    # Keep max 3 distinctive words from the name
    name_part = " ".join(name_words[:3])
    # Add 2-3 key topic words
    topic_words = [
        w for w in topic.lower().split()
        if w not in stopwords and len(w) > 3
    ][:3]
    topic_part = " ".join(topic_words)
    return f"{name_part} {topic_part}".strip()


# ── Enrichment passes ─────────────────────────

def _enrich_plant_application(name: str, existing: str, raw_text: str) -> str:
    """
    Use targeted DDG search + LLM to extract or improve plant_application.
    Falls back to existing value if nothing better is found.
    """
    print(f"    [ENRICH] plant_application")
    query = _shorten_query(name, TOPIC)
    ddg     = _ddg_text(query)
    context = f"{raw_text}\n\n{ddg}" if ddg else raw_text

    if not context.strip():
        return existing

    prompt = f"""You are an expert in {TOPIC}.

Given the text below about "{name}", extract a concise 1-2 sentence description
of how this entity applies organic electronics or bioelectronics specifically
to living plants.

Focus only on plant-specific applications (sensing, actuation, nutrient delivery,
plant-machine interfaces). If no plant-specific application is found, return null.

Text:
---
{context[:6000]}
---

Return ONLY a JSON object with one field:
{{"plant_application": "..." or null}}

If no plant-specific application is clearly supported by the text, return null. Do NOT invent or guess.

No preamble, no markdown fences.
"""
    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        val  = data.get("plant_application")
        if val and str(val).lower() not in ("null", "none", ""):
            return str(val).strip()
    except Exception as e:
        print(f"      [WARN] LLM failed: {e}")

    return existing


def _enrich_notable_outputs(name: str, existing: str, raw_text: str) -> str:
    """
    Search DDG for papers, patents, products and extract a clean list.
    """
    print(f"    [ENRICH] notable_outputs")

    short = _shorten_query(name, TOPIC)
    q1 = f'{short} paper publication'
    q2 = f'{short} patent product'
    ddg = _ddg_text(q1) + "\n\n" + _ddg_text(q2)

    context = f"{raw_text}\n\n{ddg}" if ddg else raw_text

    if not context.strip():
        return existing

    prompt = f"""You are an expert in {TOPIC}.

Given the text below about "{name}", extract a list of notable outputs:
papers, patents, products, datasets, or tools they have produced.

For each item include: type (paper/patent/product/tool), title or name
(use a short label of max 8 words, not a full sentence), and year if available.

Return ONLY a JSON object:
{{"notable_outputs": ["item 1", "item 2", ...] or null}}

Keep each item concise (one line). Maximum 6 items. Most relevant first.

Only include outputs explicitly mentioned in the text. Do NOT invent or guess titles, patents, or products.

No preamble, no markdown fences.

Text:
---
{context[:6000]}
---
"""
    try:
        raw  = _call_llm(prompt)
        data = _parse_json(raw)
        val  = data.get("notable_outputs")
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


def _enrich_key_people(name: str, existing: str, raw_text: str, topic: str) -> str:
    """
    Search DDG for researchers/PIs specifically working on the topic.
    Uses short name to avoid overly restrictive queries.
    """
    print(f"    [ENRICH] key_people")

    short_name = _shorten_query(name, TOPIC)
    q1 = f'{short_name} researcher principal investigator'
    q2 = f'{short_name} team scientist lab'
    ddg = _ddg_text(q1) + "\n\n" + _ddg_text(q2)

    context = f"{raw_text}\n\n{ddg}" if ddg else raw_text

    if not context.strip():
        return existing

    prompt = f"""You are an expert in {TOPIC}.

Given the text below about "{name}", extract the names of key researchers,
principal investigators, or team members who work specifically on {topic}.

Important: prefer researchers directly involved in the specific topic
("{topic}") over general directors or administrators of the organisation.
Include both senior PIs and notable junior researchers if mentioned.

Return ONLY a JSON object:
{{"key_people": ["Name 1 (role if known)", "Name 2 (role if known)", ...] or null}}

Maximum 4 people.

Only include people explicitly named in the text. Do NOT invent or guess names.

No preamble, no markdown fences.

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
    """
    Run enrichment passes on all analyzed entities.
    Reads raw text from data/raw/ for additional context.
    Results cached in data/enriched/<slug>.json.
    Returns dict: {entity_name: enriched_dict}
    """
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

        enriched["plant_application"] = _enrich_plant_application(
            name,
            existing = str(data.get("plant_application") or ""),
            raw_text = raw_text,
        )

        enriched["notable_outputs"] = _enrich_notable_outputs(
            name,
            existing = str(data.get("notable_outputs") or ""),
            raw_text = raw_text,
        )

        enriched["key_people"] = _enrich_key_people(
            name,
            existing = str(data.get("key_people") or ""),
            raw_text = raw_text,
            topic    = TOPIC,
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