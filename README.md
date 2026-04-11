# Competitive Intelligence Tool

Automated pipeline for competitive landscape analysis in biotech and deeptech.
Given a topic and a list of entities, it scrapes public sources, extracts
structured data via LLM, and produces a formatted HTML report — ready to read,
share, or iterate on.

Built to explore what's possible when you wire LLMs into a real research
workflow. The domain is life science and deeptech, which is where my background
is. The problem it solves — mapping a niche technical landscape quickly — is
one I've run into repeatedly as a researcher.

<!-- SCREENSHOT: Streamlit UI with live milestone progress -->

---

## The problem

Mapping a competitive or research landscape in a niche scientific field is
slow. You search, you read, you summarise, you repeat. For topics like
organic bioelectronics or plant-interfacing devices, most results are academic
papers, not company pages — which makes standard tools useless.

This pipeline turns that into an advantage: it extracts entity candidates
directly from paper affiliations, verifies them against the topic, and feeds
them into a structured analysis chain.

---

## Pipeline

```
CSV input (topic + seed entities)
        ↓
  [Discovery]  DDG search → affiliation extraction → LLM verification
        ↓
  [Scraping]   Fetch and clean entity websites (cached)
        ↓
  [Analysis]   LLM → structured JSON per entity
        ↓
  [Enrichment] Targeted DDG passes for domain, outputs, key people
        ↓
  HTML report  sorted by relevance
```

The discovery step is the non-obvious part. For niche scientific topics,
search results are mostly papers. The pipeline extracts author affiliation
strings from that raw text, verifies each candidate via a second search
(accepted only if the page contains ≥2 topic keywords), then classifies
against the known entity list. This filters street addresses, metadata
fragments, and duplicates before the LLM ever sees them.

<!-- GIF: pipeline running in terminal or Streamlit UI, milestone by milestone -->

---

## Output

A clean HTML report with one card per entity: description, domain application,
notable outputs, key people, and a relevance score. Fully offline once
generated — shareable as a single file.

<!-- SCREENSHOT: example HTML report, a few entity cards -->

---

## Stack

Python · requests · BeautifulSoup4 · Anthropic API (claude-sonnet) · Ollama

Two LLM backends are supported and selectable at runtime:

```python
LLM_BACKEND = "ollama"   # local — Qwen2.5:14b (default, no API cost)
LLM_BACKEND = "claude"   # API  — claude-sonnet (more reliable on enrichment)
```

Ollama works well for structured extraction. Claude gives more reliable
results for enrichment, where hallucination risk is higher. Switching is
one line in `config.py` or a toggle in the UI.

---

## Setup

```bash
git clone https://github.com/leoturambar/competitive-intelligence.git
cd competitive-intelligence

conda create -n ci-tool python=3.11
conda activate ci-tool
pip install -r requirements.txt
```

**API key** (if using Claude backend) — set once as a system environment
variable on Windows, persists across projects:

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Reopen the terminal after running `setx`.

---

## Usage

### Command line

```bash
python main.py --entities data/input/entities_plant_bioelectronics.csv
python main.py --entities data/input/entities_plant_bioelectronics.csv --discover
python main.py --force          # full fresh run, ignore all caches
python main.py --skip-scrape    # use existing raw cache
python main.py --skip-enrich    # skip enrichment, go straight to report
```

### Streamlit UI

```bash
streamlit run app_ui.py
```

Or double-click `launch_ui.bat` on Windows.

<!-- SCREENSHOT: Streamlit UI flag controls and report button -->

---

## Input format

Topic and entities are configured via CSV:

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

Lines starting with `#` are metadata. `#discovery_queries` and
`#topic_keywords` are optional — defaults from `config.py` are used if omitted.

---

## Caching

Each pipeline step caches output as JSON. Subsequent runs skip cached steps
automatically.

| Step | Cache location |
|---|---|
| Scraping | `data/raw/<slug>.json` |
| Analysis | `data/analyzed/<slug>.json` |
| Enrichment | `data/enriched/<slug>.json` |
| Discovery | `data/discovered.json` |

Force refresh: `--force` (all), or per-step flags `--force-scrape`,
`--force-analyze`, `--force-enrich`, `--force-discover`.

> Changing `config.py` or CSV does not auto-invalidate caches.
> Run `--force` after changing topic or entity list.

---

## Project structure

```
competitive-intelligence/
├── config.py           # LLM backend, defaults for topic/queries/keywords
├── csv_loader.py       # CSV parsing and save_discovered_to_csv()
├── scraper.py          # Fetch and clean text from entity URLs
├── analyzer.py         # LLM call per entity → structured JSON
├── enricher.py         # Targeted DDG passes for outputs, people, application
├── discoverer.py       # Auto-discover new entities via DDG + verification + LLM
├── reporter.py         # JSON → formatted HTML report
├── main.py             # Entry point, orchestrates the pipeline
├── app_ui.py           # Streamlit UI
├── test_discovery.py   # Standalone test for the discovery pipeline
├── launch_ui.bat       # Windows launcher
├── requirements.txt
└── data/
    ├── input/          # CSV topic files (tracked in git)
    ├── raw/            # Cached scraped text
    ├── analyzed/       # Cached LLM output
    ├── enriched/       # Cached enrichment output
    └── reports/        # Generated HTML reports
```

---

## Notes on output quality

LLM output quality varies by entity. Entities with rich web presence produce
reliable results. Entities with minimal online footprint may have sparse fields.
All output should be reviewed before use — the tool accelerates research, it
does not replace domain expertise.

For higher reliability on enrichment, switch to `LLM_BACKEND = "claude"`.

---

## License

MIT — see [LICENSE](LICENSE)