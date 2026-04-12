# Competitive Intelligence Tool — Claude Code Context

## What this is
Automated pipeline for competitive landscape analysis in biotech and deeptech.
Scrapes public sources, extracts structured data via LLM, and generates a
formatted HTML report. Topic and entities configurable via CSV.

## Stack
Python · requests · BeautifulSoup4 · Anthropic API (claude-sonnet) · Ollama · Streamlit

## Project structure
- main.py — entry point, orchestrates the full pipeline
- app_ui.py — Streamlit UI with live milestone progress and flag controls
- config.py — LLM backend selection, defaults for topic/queries/keywords
- csv_loader.py — CSV parsing and save_discovered_to_csv()
- scraper.py — fetch and clean text from entity URLs
- analyzer.py — LLM call per entity → structured JSON
- enricher.py — targeted DDG passes for domain, outputs, key people
- discoverer.py — auto-discover new entities via DDG + affiliation extraction +
  LLM verification
- reporter.py — JSON → formatted HTML report
- data/input/ — CSV topic files (gitignored, not tracked)
- data/raw|analyzed|enriched/ — caches (gitignored, not tracked)
- data/reports/ — generated HTML reports (gitignored, not tracked)

## Key logic
The discovery pipeline is the most complex part (discoverer.py):
DDG search → regex affiliation extraction from paper text → keyword
verification → LLM classification against known entity list.
Do not simplify this pipeline without explicit instruction.

Caching is intentional and load-bearing — each step caches to JSON so
subsequent runs skip completed steps. Do not remove or bypass caching logic.

## LLM backend
Two backends supported and selectable at runtime: Ollama (default, local,
Qwen2.5:14b) and Claude (Anthropic API, claude-sonnet). Both work in the
Streamlit UI. Backend selection is in config.py and via UI toggle.

## Rules
- Write all code and docstrings in English
- Never modify config files containing API keys or credentials
- Do not push sensitive data, keys, or personal content to git
- the entire data/ directory is gitignored — do not force-add any files from it
- Ask before changing the pipeline orchestration in main.py
- Ask before changing the HTML report structure in reporter.py