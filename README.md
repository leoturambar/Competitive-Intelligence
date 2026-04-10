# Competitive Intelligence Tool — Organic Bioelectronics for Plants

Automated pipeline that scrapes public sources, extracts structured 
information via LLM, and generates a formatted competitive landscape 
report. Built as a portfolio project demonstrating domain expertise 
in bioelectronics combined with practical AI/automation skills.

## What it does

1. **Scrapes** company and research institute websites (with local caching)
2. **Analyses** raw text with an LLM → structured JSON per entity
3. **Generates** a clean HTML report sorted by relevance

## Stack

Python · requests · BeautifulSoup4 · Anthropic API (claude-sonnet) · Ollama (local LLM)

## Setup

```bash
conda create -n ci-tool python=3.11
conda activate ci-tool
pip install -r requirements.txt
```

Set your Anthropic API key (Windows):
```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```

## Usage

```bash
python main.py                  # run with cache
python main.py --force-scrape   # re-scrape all sources
python main.py --force-analyze  # re-run LLM analysis
python main.py --force          # full fresh run
```

Report saved to `data/reports/competitive_intelligence_<date>.html`.

## Configuration

Edit `config.py` to change:
- `LLM_BACKEND` — `"ollama"` or `"claude"`
- `ENTITIES` — list of companies/institutes to analyse
- `TOPIC` / `TOPIC_DESCRIPTION` — adapt to a different landscape
- `OUTPUT_FIELDS` — fields extracted per entity

## Example output fields

| Field | Description |
|---|---|
| entity_type | Company / Spinoff / University / Consortium |
| development_stage | Research / Prototype / Pilot / Commercial |
| competitive_position | Leader / Niche player / Adjacent / ... |
| relevance_score | 1–5 |
| technology_focus | What they actually build |
| plant_application | Specific use in/for plants |
| summary | 2–3 sentence executive summary |