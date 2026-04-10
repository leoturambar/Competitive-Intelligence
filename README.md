# Competitive Intelligence Tool

Automated pipeline for competitive landscape analysis in biotech and deeptech.
Scrapes public sources, extracts structured data via LLM, and generates a
formatted HTML report. Topic and entities fully configurable via `config.py`.

## What it does

1. **Scrapes** company and research institute websites (with local caching)
2. **Analyses** raw text with an LLM → structured JSON per entity
3. **Generates** a clean HTML report sorted by relevance

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
python main.py                   # run with cache
python main.py --force-scrape    # re-scrape all sources
python main.py --force-analyze   # re-run LLM analysis
python main.py --force           # full fresh run
python main.py --skip-scrape     # use existing raw cache, re-analyze only
```

Report saved to `data/reports/competitive_intelligence_<date>.html` — open directly in browser.

## Project structure

```
competitive-intelligence/
├── config.py        # Topic, entities, LLM backend, output fields
├── scraper.py       # Fetch and clean text from entity URLs
├── analyzer.py      # LLM call per entity → structured JSON
├── reporter.py      # JSON → formatted HTML report
├── main.py          # Entry point, orchestrates the pipeline
├── requirements.txt
└── data/
    ├── raw/         # Cached scraped text (not tracked in git)
    ├── analyzed/    # Cached LLM output (not tracked in git)
    └── reports/     # Generated HTML reports (not tracked in git)
```

## LLM backend

Two options, selectable in `config.py`:

```python
LLM_BACKEND = "ollama"   # local — uses Qwen2.5:14b via Ollama
LLM_BACKEND = "claude"   # API — uses claude-sonnet via Anthropic
```

## Adapting to a new topic

All configuration lives in `config.py`. To analyse a different landscape:

1. Update `TOPIC` and `TOPIC_DESCRIPTION`
2. Replace `ENTITIES` with your list — each entry needs `name`, `urls`, and optionally `notes`
3. Adjust `OUTPUT_FIELDS` if you want different extracted fields
4. Run `python main.py --force` to start fresh

Example entity entry:
```python
{
    "name": "Acme Biotech",
    "urls": ["https://acmebiotech.com/research"],
    "notes": "Founded 2018, Series B, focused on CRISPR delivery."
}
```

## Output fields (default)

| Field | Description |
|---|---|
| `entity_type` | Company / Spinoff / University / Research Institute / Consortium |
| `development_stage` | Research / Prototype / Pilot / Commercial |
| `competitive_position` | Leader / Strong player / Niche player / Adjacent / Early stage |
| `relevance_score` | 1–5 |
| `technology_focus` | What they actually build |
| `plant_application` | Specific application in the configured domain |
| `funding_or_status` | Known funding, grants, or revenue status |
| `notable_outputs` | Papers, patents, products |
| `summary` | 2–3 sentence executive summary |

## License

MIT — see [LICENSE](LICENSE)