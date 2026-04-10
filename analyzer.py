# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — analyzer.py
# ─────────────────────────────────────────────

import json
import os
import re
from pathlib import Path

import requests

from config import (
    LLM_BACKEND, OLLAMA_MODEL, CLAUDE_MODEL, CLAUDE_API_KEY,
    TOPIC, TOPIC_DESCRIPTION, OUTPUT_FIELDS,
)

ANALYZED_DIR = Path("data/analyzed")


def _build_prompt(entity_name: str, raw_text: str) -> str:
    fields_list = "\n".join(f'  - "{f}"' for f in OUTPUT_FIELDS)
    return f"""You are an expert analyst in {TOPIC}.

Context about the landscape you are mapping:
{TOPIC_DESCRIPTION}

You will be given raw text scraped from the web about one specific entity.
Your task: extract structured information and return ONLY a valid JSON object.

Entity name: {entity_name}

Raw text:
---
{raw_text}
---

Return a JSON object with exactly these fields:
{fields_list}

Rules:
- If a field cannot be determined from the text, use null.
- "relevance_score" must be an integer 1–5.
- "development_stage" must be one of: Research | Prototype | Pilot | Commercial | Unknown.
- "entity_type" must be one of: Company | Spinoff | Research Institute | University | Consortium | Other.
- "competitive_position" must be one of: Leader | Strong player | Niche player | Adjacent | Early stage | Unknown.
- Be concise. "summary" max 3 sentences. "technology_focus" and "plant_application" max 2 sentences each.
- Return ONLY the JSON object. No preamble, no explanation, no markdown fences.
"""


# ── Ollama backend ────────────────────────────

def _call_ollama(prompt: str) -> str:
    resp = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["response"]


# ── Claude backend ────────────────────────────

def _call_claude(prompt: str) -> str:
    api_key = CLAUDE_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("Claude API key not set. Set CLAUDE_API_KEY in config.py or ANTHROPIC_API_KEY env var.")

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"]


# ── Dispatcher ────────────────────────────────

def _call_llm(prompt: str) -> str:
    if LLM_BACKEND == "ollama":
        return _call_ollama(prompt)
    elif LLM_BACKEND == "claude":
        return _call_claude(prompt)
    else:
        raise ValueError(f"Unknown LLM_BACKEND: {LLM_BACKEND}")


def _parse_json(raw: str) -> dict:
    """Extract JSON from LLM response, tolerating minor formatting issues."""
    # Strip potential markdown fences
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    # Qwen sometimes returns bare "null" when it has nothing to say
    if raw.strip().lower() == "null":
        return {}
    # Find first { ... } block
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"No JSON object found in LLM response:\n{raw[:300]}")


# ── Main function ─────────────────────────────

def analyze_all(scraped: dict, force: bool = False) -> dict:
    """
    Run LLM analysis on all scraped entities.
    Results cached in data/analyzed/<slug>.json.
    Returns dict: {entity_name: structured_dict}
    """
    ANALYZED_DIR.mkdir(parents=True, exist_ok=True)
    results = {}

    for name, data in scraped.items():
        slug  = re.sub(r"[^a-z0-9]+", "_", name.lower())[:60]
        cache = ANALYZED_DIR / f"{slug}.json"

        if cache.exists() and not force:
            print(f"  [CACHE] {name}")
            with open(cache) as f:
                results[name] = json.load(f)
            continue

        print(f"  [ANALYZE] {name}  (backend: {LLM_BACKEND})")

        raw_text = data.get("text", "")
        if not raw_text.strip():
            print(f"    [WARN] No text available — filling with nulls.")
            structured = {f: None for f in OUTPUT_FIELDS}
            structured["entity_name"] = name
        else:
            prompt = _build_prompt(name, raw_text)
            try:
                raw_response = _call_llm(prompt)
                structured   = _parse_json(raw_response)
                structured["entity_name"] = name
            except Exception as e:
                print(f"    [ERROR] LLM failed for {name}: {e}")
                structured = {f: None for f in OUTPUT_FIELDS}
                structured["entity_name"] = name
                structured["summary"] = f"Analysis failed: {e}"

        with open(cache, "w") as f:
            json.dump(structured, f, indent=2)

        results[name] = structured

    return results


if __name__ == "__main__":
    # Quick test: analyze from existing raw cache
    from scraper import scrape_all
    scraped = scrape_all()
    analyzed = analyze_all(scraped, force=True)
    print(json.dumps(list(analyzed.values())[0], indent=2))
