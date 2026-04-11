# Competitive Intelligence Tool

Automated pipeline for competitive landscape analysis in biotech and deeptech.
Scrapes public sources, extracts structured data via LLM, and generates a
formatted HTML report. Topic and entities fully configurable via CSV or `config.py`.

## What it does

1. **Discovers** new entities automatically via DuckDuckGo + regex affiliation
   extraction + DDG verification (optional)
2. **Scrapes** company and research institute websites (with local caching)
3. **Analyses** raw text with an LLM → structured JSON per entity
4. **Enriches** three fields with targeted DDG searches: domain application,
   notable outputs, key people
5. **Generates** a clean HTML report sorted by relevance

A Streamlit UI (`app_ui.py`) provides a graphical interface with live milestone
progress, flag controls, and one-click report opening.

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

### Command line
```bash
python main.py --entities data/input/entities_plant_bioelectronics.csv
python main.py --entities data/input/entities_plant_bioelectronics.csv --discover
python main.py --force          # full fresh run, ignore all caches
python main.py --skip-scrape    # use existing raw cache
python main.py --skip-enrich    # skip enrichment, go straight to report
python main.py --force-discover # re-run discovery ignoring cache
```

### Streamlit UI
```bash
streamlit run app_ui.py
```
Or double-click `launch_ui.bat` on Windows.

## Input: CSV format

Topic and entities are configured via CSV files in `data/input/`:

```csv
#topic,organic bioelectronics for plants
#description,Companies and research groups working on OECTs applied to plants
#discovery_queries,plant bioelectronics startup|OECT plant research|electronic plants lab
#topic_keywords,bioelectron|OECT|plant|xylem|conducting polymer
#domain_label,Plant application
name,url,notes
Linköping University — LOE,https://liu.se/...,Pioneer group, Eleni Stavrinidou PI
Vivent SA,https://www.vivent.ch,Swiss company, plant electrical signal monitoring
```

Lines starting with `#` are metadata. `#discovery_queries` and `#topic_keywords`
are optional — if omitted, defaults from `config.py` are used.
Pass the file with `--entities data/input/your_file.csv`.
Without `--entities`, the tool uses `config.py` directly.

## How discovery works

For niche scientific topics, DDG mostly returns academic papers rather than
company pages. The discovery pipeline turns this into an advantage:

1. DDG searches collect raw text from papers and lab pages
2. `_regex_affiliations()` extracts author affiliation strings from paper text
3. Each candidate is verified via a second DDG search — accepted only if the
   found page contains at least 2 topic keywords
4. Verified candidates are classified by the LLM against the known entity list
5. New entities feed into the normal scrape → analyze → enrich pipeline

This filters out street addresses, metadata fragments, and known duplicates
before the LLM ever sees them.

## Project structure

```
competitive-intelligence/
├── config.py        # LLM backend, defaults for topic/queries/keywords
├── csv_loader.py    # CSV parsing and save_discovered_to_csv()
├── scraper.py       # Fetch and clean text from entity URLs
├── analyzer.py      # LLM call per entity → structured JSON
├── enricher.py      # Targeted DDG passes for outputs, people, application
├── discoverer.py    # Auto-discover new entities via DDG + verification + LLM
├── reporter.py      # JSON → formatted HTML report
├── main.py          # Entry point, orchestrates the pipeline
├── app_ui.py        # Streamlit UI
├── test_discovery.py # Standalone test for the discovery pipeline
├── launch_ui.bat    # Windows launcher for Streamlit UI
├── requirements.txt
└── data/
    ├── input/       # CSV topic files (tracked in git)
    ├── raw/         # Cached scraped text
    ├── analyzed/    # Cached LLM output
    ├── enriched/    # Cached enrichment output
    └── reports/     # Generated HTML reports
```

## LLM backend

```python
LLM_BACKEND = "ollama"   # local — Qwen2.5:14b via Ollama (default)
LLM_BACKEND = "claude"   # API  — claude-sonnet via Anthropic
```

Selectable from the UI or via `CI_LLM_BACKEND` environment variable.
Ollama works well for structured extraction. Claude gives more reliable
results for enrichment, where hallucination risk is higher.

## Caching

Each step caches output as JSON. Subsequent runs skip cached steps automatically.

| Step | Cache |
|---|---|
| Scraping | `data/raw/<slug>.json` |
| Analysis | `data/analyzed/<slug>.json` |
| Enrichment | `data/enriched/<slug>.json` |
| Discovery | `data/discovered.json` |

Force refresh: `--force` (all), `--force-scrape`, `--force-analyze`,
`--force-enrich`, `--force-discover`.

> Changing `config.py` or CSV does not auto-invalidate caches.
> Always run `--force` after changing topic or entity list.

## Output quality notes

LLM output quality varies by entity. Entities with rich web presence produce
reliable results. Entities with minimal online footprint may have sparse fields.
All output should be reviewed before use — the tool accelerates research,
it does not replace domain expertise.

For higher reliability on enrichment, switch to `LLM_BACKEND = "claude"`.

## License

MIT — see [LICENSE](LICENSE)