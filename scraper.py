# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — scraper.py
# ─────────────────────────────────────────────

import time
import json
import re
import random
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import (
    ENTITIES, REQUEST_TIMEOUT, REQUEST_DELAY,
    MAX_TEXT_CHARS, DDG_DELAY
)

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

RAW_DIR = Path("data/raw")


def _clean_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "noscript", "iframe", "svg"]):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fetch_url(url: str) -> str:
    """Fetch a URL and return cleaned text, or empty string on failure."""
    headers = {
        "User-Agent": random.choice(UA_LIST),
    }
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return _clean_text(resp.text)
    except Exception as e:
        print(f"    [WARN] Could not fetch {url}: {e}")
        return ""


def scrape_all(force: bool = False, extra_entities: list = None) -> dict:
    """
    Scrape all entities defined in config, plus any extra_entities
    passed in (e.g. from discoverer.py).
    Results cached in data/raw/<entity_slug>.json.
    Set force=True to re-scrape even if cache exists.
    Returns dict: {entity_name: {"text": ..., "urls": ..., "notes": ...}}
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    all_entities = list(ENTITIES) + (extra_entities or [])

    for entity in all_entities:
        name  = entity["name"]
        slug  = re.sub(r"[^a-z0-9]+", "_", name.lower())[:60]
        cache = RAW_DIR / f"{slug}.json"

        if cache.exists() and not force:
            print(f"  [CACHE] {name}")
            with open(cache) as f:
                results[name] = json.load(f)
            continue

        print(f"  [SCRAPE] {name}")
        combined_text = ""

        for url in entity["urls"]:
            print(f"    → {url}")
            text = _fetch_url(url)
            if text:
                combined_text += f"\n\n[Source: {url}]\n{text}"
            time.sleep(REQUEST_DELAY)

        # Append any manual notes from config
        if entity.get("notes"):
            combined_text += f"\n\n[Background notes]: {entity['notes']}"

        # Truncate to budget
        combined_text = combined_text[:MAX_TEXT_CHARS]

        payload = {
            "name":  name,
            "urls":  entity["urls"],
            "notes": entity.get("notes", ""),
            "text":  combined_text,
        }

        with open(cache, "w") as f:
            json.dump(payload, f, indent=2)

        results[name] = payload

    return results


if __name__ == "__main__":
    print("Running scraper standalone...")
    data = scrape_all(force=True)
    print(f"\nDone. Scraped {len(data)} entities.")