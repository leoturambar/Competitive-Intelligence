# Competitive Intelligence Tool

Automated pipeline for competitive landscape analysis in biotech and deeptech.
Scrapes public sources, extracts structured data via LLM, and generates a
formatted HTML report. Topic and entities fully configurable via `config.py`.

## What it does

1. **Discovers** new entities automatically via DuckDuckGo + regex affiliation extraction + LLM (optional)
2. **Scrapes** company and research institute websites (with local caching)
3. **Analyses** raw text with an LLM → structured JSON per entity
4. **Enriches** three fields with targeted DDG searches: plant application, notable outputs, key people
5. **Generates** a clean HTML report sorted by relevance

## Stack

Python · requests · BeautifulSoup4 · Anthropic API (claude-sonnet) · Ollama (local LLM)

## Setup

```bash
git clone https://github.com/leoturambar/competitive-intelligence.git
cd competitive-intelligence

conda create -n ci-tool python=3.11
conda activate ci-tool
pip install -r requirements.txt
```

**Anthropic API key** — set once as a system environment variable on Windows,
persists across all projects:
```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```
Reopen the terminal after running `setx`.

## Usage

```bash
python main.py                    # run with cache
python main.py --discover         # also auto-discover new entities
python main.py --force-scrape     # re-scrape all sources
python main.py --force-analyze    # re-run LLM analysis
python main.py --force-enrich     # re-run enrichment passes only
python main.py --skip-scrape      # use existing raw cache, re-analyze
python main.py --skip-enrich      # skip enrichment, go straight to report
python main.py --force            # full fresh run (all steps)
python main.py --force --discover # full fresh run + discovery
```

Report saved to `data/reports/competitive_intelligence_<date>.html` — open directly in browser.

## Project structure

```
competitive-intelligence/
├── config.py        # Topic, entities, LLM backend, output fields, delays
├── scraper.py       # Fetch and clean text from entity URLs, DDG fallback
├── analyzer.py      # LLM call per entity → structured JSON
├── enricher.py      # Targeted DDG passes for plant_application, outputs, people
├── discoverer.py    # Auto-discover new entities via DDG + regex + LLM
├── reporter.py      # JSON → formatted HTML report
├── main.py          # Entry point, orchestrates the pipeline
├── test_discovery.py # Standalone test for the discovery pipeline
├── requirements.txt
└── data/
    ├── raw/         # Cached scraped text (not tracked in git)
    ├── analyzed/    # Cached LLM output (not tracked in git)
    ├── enriched/    # Cached enrichment output (not tracked in git)
    ├── discovered/  # Cached discovery results (not tracked in git)
    └── reports/     # Generated HTML reports (not tracked in git)
```

## LLM backend

Two options, selectable in `config.py`:

```python
LLM_BACKEND = "ollama"   # local — uses Qwen2.5:14b via Ollama (default)
LLM_BACKEND = "claude"   # API  — uses claude-sonnet via Anthropic
```

Ollama is sufficient for structured extraction tasks. Claude API gives
more reliable results on ambiguous or data-sparse entities, particularly
for the enrichment passes where hallucination risk is higher.

## How discovery works

For niche scientific topics, DuckDuckGo mostly returns academic papers rather
than company pages. The discovery pipeline turns this into an advantage:

1. DDG search with varied queries finds papers on the topic
2. `_regex_affiliations()` extracts author affiliation strings directly from
   the paper text — these are the real entities (research groups, labs, universities)
3. The LLM classifies affiliations against the known entity list and returns new ones
4. New entities feed into the normal scrape → analyze → enrich pipeline

This approach finds groups that would never appear in a company directory
but are active contributors to the field.

## Adapting to a new topic

All configuration lives in `config.py`. To analyse a different landscape:

1. Update `TOPIC` and `TOPIC_DESCRIPTION`
2. Replace `ENTITIES` with your list — each entry needs `name`, `urls`, and optionally `notes`
3. Adjust `OUTPUT_FIELDS` if you want different extracted fields
4. Update `DISCOVERY_QUERIES` with topic-relevant search terms
5. Run `python main.py --force` to start fresh

Example entity entry:
```python
{
    "name": "Acme Biotech",
    "urls": ["https://acmebiotech.com/research"],
    "notes": "Founded 2018, Series B, focused on CRISPR delivery."
}
```

> **Note:** changing `config.py` does not automatically invalidate the cache.
> Always run `python main.py --force` after changing topic or entities.

## Caching

Each pipeline step caches its output as JSON files:

| Step | Cache location |
|---|---|
| Scraping | `data/raw/<slug>.json` |
| Analysis | `data/analyzed/<slug>.json` |
| Enrichment | `data/enriched/<slug>.json` |
| Discovery | `data/discovered.json` |

On subsequent runs, cached steps are skipped automatically.

**When to force a refresh:**
- `--force-scrape` — source websites have been updated
- `--force-analyze` — you changed the prompt, output fields, or LLM backend
- `--force-enrich` — you changed enrichment queries or prompts
- `--force` — you changed `TOPIC`, `TOPIC_DESCRIPTION`, or `ENTITIES`

## Output fields

| Field | Description |
|---|---|
| `entity_type` | Company / Spinoff / University / Research Institute / Consortium |
| `development_stage` | Research / Prototype / Pilot / Commercial |
| `competitive_position` | Leader / Strong player / Niche player / Adjacent / Early stage |
| `relevance_score` | 1–5 |
| `technology_focus` | What they build |
| `plant_application` | Specific application in the configured domain (enriched) |
| `funding_or_status` | Known funding, grants, or revenue status |
| `key_people` | Researchers / PIs working on the topic (enriched) |
| `notable_outputs` | Papers, patents, products (enriched) |
| `summary` | 2–3 sentence executive summary |

## Notes on DuckDuckGo scraping

The tool uses DuckDuckGo's HTML interface for web searches (no API key required).
DDG applies rate limiting on frequent requests. If you see connection errors or
timeouts during enrichment or discovery, the tool retries automatically.

DDG wraps result URLs in a redirect format — the tool decodes these automatically
via `urllib.parse.unquote`. If DDG changes this format, update the `_ddg_links`
function in `enricher.py` and `discoverer.py`.

You can adjust scraping behaviour in `config.py`:
```python
REQUEST_DELAY = 1.5   # seconds between page fetches
DDG_DELAY     = 4.0   # seconds between DDG queries
```

Increasing `DDG_DELAY` reduces the risk of IP-level rate limiting during long runs.

## Output quality notes

LLM output quality varies by entity. Entities with rich web presence (dedicated
lab pages, published papers) produce reliable results. Entities with minimal
online footprint may have sparse or inconsistent fields. All output should be
reviewed before use — the tool is a research accelerator, not a source of truth.

For higher reliability on enrichment fields, switch to `LLM_BACKEND = "claude"`
in `config.py`.

## License

MIT — see [LICENSE](LICENSE)