# ─────────────────────────────────────────────
#  test_discovery.py
#  Standalone test for the discovery pipeline.
#  Run with: python test_discovery.py
#  Shows exactly what DDG returns and what the LLM sees.
# ─────────────────────────────────────────────

import json
import re
import time

import requests
from bs4 import BeautifulSoup
from discoverer import _call_llm, _parse_json, _regex_affiliations
from config import ENTITIES, TOPIC_DESCRIPTION

# ── Config (inline, no dependency on config.py) ──────────
TOPIC        = "organic bioelectronics for plants"
DDG_DELAY    = 3.0
TIMEOUT      = 12
MAX_CHARS    = 2000   # per page, kept short for test clarity

QUERIES = [
    "plant bioelectronics company startup",
    "electronic plants research lab university",
    "organic electronics plant sensing spinoff",
    "OECT plant interface research group",
    "conducting polymer plant bioelectronics",
]

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

import random

# ── Helpers ──────────────────────────────────────────────

def ddg_links(query: str, max_results: int = 4) -> list:
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if href.startswith("http"):
                links.append(href)
            if len(links) >= max_results:
                break
        return links
    except Exception as e:
        print(f"  [ERR] DDG failed: {e}")
        return []


def fetch_text(url: str) -> str:
    headers = {"User-Agent": random.choice(UA_LIST)}
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "form", "noscript", "iframe", "svg"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_CHARS]
    except Exception as e:
        print(f"    [ERR] Fetch failed: {e}")
        return ""


# ── Main test ────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"  Discovery Test — {TOPIC}")
    print(f"{'='*60}\n")

    all_links  = []
    all_text   = ""
    seen_urls  = set()

    for query in QUERIES:
        print(f"[QUERY] {query}")
        links = ddg_links(query)

        if not links:
            print("  → no links returned\n")
            time.sleep(DDG_DELAY)
            continue

        for url in links:
            print(f"  → {url}")
            if url in seen_urls:
                print("    (duplicate, skipping)")
                continue
            seen_urls.add(url)
            all_links.append(url)

            text = fetch_text(url)
            if text:
                print(f"    ✓ {len(text)} chars")
                all_text += f"\n\n[Source: {url}]\n{text}"
            else:
                print(f"    ✗ empty")
            time.sleep(1.0)

        time.sleep(DDG_DELAY)

    print(f"\n{'─'*60}")
    print(f"Total links found : {len(all_links)}")
    print(f"Total text chars  : {len(all_text)}")
    print(f"{'─'*60}\n")

    if not all_text.strip():
        print("[RESULT] No text collected — discovery would return 0 entities.")
        print("         Possible causes:")
        print("         - DDG rate limiting (wait a few minutes and retry)")
        print("         - Selector 'a.result__a' no longer valid")
        print("         - All fetched pages returned 403/empty")
        return

    # Show a snippet of what the LLM would receive
    affiliations = _regex_affiliations(all_text)
    print(f"[AFFILIATIONS EXTRACTED — {len(affiliations)} found]")
    print("─" * 40)
    for a in affiliations:
        print(f"  • {a}")
    print("─" * 40)

    # Optionally test LLM extraction if Ollama is running
    test_llm = input("\nTest LLM entity extraction? (y/n): ").strip().lower()
    if test_llm != "y":
        return

    try:
        affiliations = _regex_affiliations(all_text)
        affiliation_block = "\n".join(f"- {a}" for a in affiliations)
        known = "\n".join(f"- {e['name']}" for e in ENTITIES)

        print(f"[AFFILIATIONS EXTRACTED — {len(affiliations)} found]")
        print("─" * 40)
        for a in affiliations:
            print(f"  • {a}")
        print("─" * 40)

        prompt = f"""You are an expert analyst in {TOPIC}.

Context:
{TOPIC_DESCRIPTION}

Here are author affiliations extracted from academic papers on {TOPIC}:

{affiliation_block}

From this list, identify up to 5 research groups or institutions
NOT already in this known list:

{known}

For each new entity return:
- "name": institution name + lab/group name if available
- "url": use the source paper URL if no specific lab page is known
- "entity_type": Company | Spinoff | Research Institute | University | Consortium | Other
- "notes": 1 sentence on what they do on {TOPIC}
- "source_url": leave empty or repeat url

Rules:
- Only include entities from the affiliation list above — do NOT invent
- Do NOT use generic homepages
- If none qualify, return {{"new_entities": []}}

Return ONLY JSON:
{{"new_entities": [{{"name": "...", "url": "...", "entity_type": "...", "notes": "...", "source_url": "..."}}]}}
No preamble, no markdown fences.
"""
        print("\n[LLM] Calling Ollama...")
        raw    = _call_llm(prompt)
        result = _parse_json(raw)
        found  = result.get("new_entities", [])
        print(f"\n[LLM RESULT] {len(found)} entities found:")
        for e in found:
            print(f"  • {e.get('name')} — {e.get('url')}")
            print(f"    {e.get('notes')}")

    except Exception as e:
        print(f"[ERR] LLM test failed: {e}")


if __name__ == "__main__":
    run()