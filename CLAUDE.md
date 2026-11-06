# Competitive Intelligence Tool — Claude Code Context

## What this is
Automated pipeline for competitive landscape analysis in biotech and deeptech.
Scrapes public sources, extracts structured data via LLM, and generates a
formatted HTML report. Topic and entities configurable via CSV.

## Stack
Python · requests · BeautifulSoup4 · Anthropic SDK · Ollama · OpenAlex API · Streamlit · pytest

## Project structure
- main.py — CLI, orchestrates the pipeline (discovery → scraping → analysis → enrichment → landscape → report)
- app_ui.py — Streamlit app: projects, run with live progress/log, results explorer, discovery review
- config.py — defaults (backend, models, topic, delays); user-owned, don't edit
- project.py — Project model (one per topic CSV), per-project paths, fingerprinted cache helpers, Status
- csv_loader.py — robust CSV read/write, save_discovered_to_csv()
- web.py — shared HTTP session, per-host rate limit, HTML cleaning, search (Brave API → DDG html → DDG lite) with circuit breaker
- llm.py — single LLM entry point (call_json with JSON schema), Ollama + Claude backends, tolerant parsing
- openalex.py — institution matching, topic activity, paper lookup, bibliometric discovery (cached 14 days)
- scraper.py — homepage search for URL-less entities, multi-page scraping
- analyzer.py — schema-constrained extraction + normalize()
- enricher.py — OpenAlex research profile, web-intelligence pass, link resolution, merging
- discoverer.py — OpenAlex + web-affiliation discovery, verification funnel, decision log
- landscape.py — cross-entity stats and synthesis
- reporter.py — self-contained interactive HTML report (+ CSV/JSON)
- tests/ — offline unit tests (python -m pytest tests)
- data/ — gitignored: input/ CSVs, projects/<slug>/ caches and reports, cache/openalex

## Key logic
Discovery (discoverer.py) has two candidate sources: OpenAlex institutions publishing on
the topic, and the original DDG → regex affiliation → verification pipeline (now rejecting
aggregator pages and requiring the candidate's name on the page). Both feed dedupe →
LLM relevance check → index-based LLM naming. Do not simplify this pipeline without
explicit instruction.

Caching is intentional and load-bearing. Each project has its own cache under
data/projects/<slug>/; each file stores a fingerprint of its inputs (project.fingerprint),
so input changes invalidate exactly the dependent layers. Do not remove or bypass it;
bump CACHE_VERSION / ENRICH_VERSION when a cached schema changes.

All LLM output that reaches the report is inserted via textContent/escaping in
reporter.py's template — keep it that way.

## LLM backend
Ollama (default, local, qwen3.5:9b, num_ctx 16k) and Claude (Anthropic SDK, structured
outputs, effort via CI_CLAUDE_EFFORT). Selectable in config.py, the app sidebar,
--backend/--model, or CI_LLM_BACKEND / CI_OLLAMA_MODEL / CI_CLAUDE_MODEL.

## Rules
- Write all code and docstrings in English
- Never modify config files containing API keys or credentials
- Do not push sensitive data, keys, or personal content to git
- the entire data/ directory is gitignored — do not force-add any files from it
- Ask before changing the pipeline orchestration in main.py
- Ask before changing the HTML report structure in reporter.py