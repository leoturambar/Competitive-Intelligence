# Competitive Intelligence Tool

An automated pipeline for mapping a niche scientific or technical landscape — given a topic and a seed list of entities, it scrapes public sources, extracts structured data through an LLM, enriches the results with targeted follow-up searches, and produces a formatted HTML report ready to read, share, or iterate on.

<!-- SCREENSHOT: Streamlit UI showing input configuration and live milestone panel -->

---

## The problem

Mapping a competitive or research landscape in a niche technical field is slow work. You search, read, summarise, and repeat — and for topics like organic bioelectronics or plant-interfacing devices, the situation is worse than average: most search results are academic papers rather than company pages. Standard intelligence tools either miss the academic layer entirely or produce noise because they have no domain context.

This is the problem I kept running into as a researcher with a background in life science and deeptech. The niche I used to prototype this tool — organic electronics applied to living plants — has exactly this structure: a small global community, most of its footprint in the form of papers and lab pages, and almost no representation in the usual startup or market databases.

The tool turns that constraint into a feature. Academic papers carry author affiliations, and affiliations name the labs and institutes doing the work. If you can extract and verify those affiliation strings, you have a discovery signal that works *because* the field is niche and paper-heavy — which is precisely when other approaches fail.

---

## The discovery pipeline

This is the most technically interesting part of the project. Running it is optional, but it is the thing that made me want to build this in the first place.

When you pass `--discover`, the pipeline runs a set of configurable DuckDuckGo searches — queries like "plant bioelectronics company startup" or "OECT plant interface research". The raw HTML from each result page is fetched and cleaned down to plain text. So far, fairly standard. The non-obvious step is what happens next.

Academic papers typically cluster their affiliation section near the top of the document, using a predictable typographic structure: numbered or lettered superscripts followed by institution names. The `_regex_affiliations` function exploits this by sliding a 400-word window across the full page text and scoring each position by how many affiliation markers it contains — words like "university", "institute", "laboratory", "center". It keeps the highest-density window and then applies four regex patterns to pull out candidate organization strings. The result is a raw list of up to 30 affiliation-like strings per page, most of which are real institutions and some of which are postal addresses, DOI fragments, or other noise.

The verification step filters this down. Each candidate string is passed through a noise pre-filter — anything containing a long digit sequence (postal codes), a street address word, a URL, or a DOI prefix is rejected immediately. Candidates that survive must contain at least one organisation indicator (university, institute, lab, GmbH, Ltd, etc.). The surviving candidates are then each searched on DuckDuckGo with the topic appended, and the top-2 result pages are fetched and checked: a candidate is accepted only if the resulting page contains at least two of the configured topic keywords. This means a candidate like "Department of Chemistry, University of Bologna" gets rejected if its web page has no signal for organic bioelectronics, but a less well-known lab working specifically on plant electrodes gets accepted because their page contains "OECT", "conducting polymer", "plant" — the relevant keywords.

After verification, only confirmed candidates reach the LLM. The prompt shows the model the full topic description, lists the already-known entities, and asks it to classify and deduplicate the verified list: clean official name, entity type, and a one-sentence description of their relevance. The model never sees the raw paper text or the unverified candidate pool — it receives only the already-vetted set. This keeps the LLM call focused and makes hallucination much less likely.

The final output is a list of new entity dicts that feed directly into the scraping step. At the end of a run, both the CLI and the Streamlit UI offer to save discovered entities back to the input CSV, so they become permanent seed entities for future runs.

If the initial DuckDuckGo queries return no usable pages, the pipeline makes a fallback LLM call to generate three alternative search queries with different terminology, then retries. Discovery results are cached in `data/discovered.json` and only re-run when explicitly forced.

<!-- SCREENSHOT: terminal output showing the discovery step: DDG queries, candidate extraction, verification with keyword hit counts, LLM classification -->

---

## The rest of the pipeline

With a seed entity list established (either from the CSV directly or augmented by discovery), the pipeline runs four steps in sequence.

**Scraping** fetches each entity's configured URLs, strips navigation, headers, footers, scripts, and other structural noise using BeautifulSoup, and collapses the remaining text. Multiple URLs per entity are supported and concatenated. If an entity has no URL, it is included with whatever context comes from its notes field. Each entity's text is truncated to 8,000 characters before being passed downstream — enough for the LLM to work with, not so much that it dominates a context window. Random user-agent rotation and configurable delays between requests avoid rate limiting. All scraped output is cached per-entity as JSON in `data/raw/`.

**Analysis** runs one LLM call per entity with a structured extraction prompt that includes the topic description as grounding context. The model returns a JSON object with twelve fields: entity type (Company, Spinoff, Research Institute, University, Consortium, or Other), country, founding date, technology focus, domain application, development stage (Research / Prototype / Pilot / Commercial), funding or status, key people, notable outputs, competitive position (Leader, Strong player, Niche player, Adjacent, Early stage), a relevance score from 1 to 5, and a two-to-three sentence summary. The parser tolerates partial responses — it strips markdown fences, tries standard JSON parsing, then falls back to scanning for the last valid closing brace. Failures fill with nulls rather than crashing. Results cached in `data/analyzed/`.

**Enrichment** runs three targeted follow-up passes per entity, each involving its own DuckDuckGo search and a focused LLM call. The first extracts a concise description of how the entity applies its technology specifically to living plants — or returns null if no plant-specific signal is found. The second identifies notable outputs (papers, patents, products, tools, datasets), asking the model to return structured objects with type, title, and year, then attempts link resolution: papers are looked up via the Semantic Scholar API (falling back to a DOI URL if found), patents are mapped to Google Patents search URLs. The third extracts key researchers and principal investigators by name and role. Each pass uses a query-shortening function that strips stopwords from long academic entity names before constructing the DuckDuckGo query, to avoid the search engine treating the full name as too literal and returning nothing. If a page context comes back with fewer than 500 characters, the LLM call is skipped to avoid noise. Results cached in `data/enriched/`.

**Report generation** takes the enriched dataset, sorts entities by relevance score descending, and produces a self-contained HTML file. Each entity gets a card with colour-coded badges for its development stage and competitive position, a relevance score rendered as filled and empty dots, a highlighted summary block, and a two-column detail grid covering technology focus, domain application, key people, and notable outputs. Output links are rendered as clickable anchors where link resolution succeeded. The report header includes aggregate counts (total entities, companies and spinoffs, academic and research, other) and the generation date. The whole thing is a single file — no dependencies, fully offline once generated.

<!-- SCREENSHOT: example HTML report showing two or three entity cards with badges, score dots, and clickable output links -->

---

## LLM backends

Two backends are supported and switchable at runtime, either in `config.py` or via the `CI_LLM_BACKEND` environment variable (which the Streamlit UI sets automatically based on your selection).

```python
LLM_BACKEND = "ollama"   # default — local inference, no API cost
LLM_BACKEND = "claude"   # Anthropic API — more reliable on enrichment
```

The Ollama backend calls a locally running Ollama server at `http://localhost:11434` with `qwen2.5:14b` as the default model. It works well for the structured extraction step, where the task is clearly specified and the output format is constrained. It is free, runs offline, and is fast enough that you can iterate through a ten-entity list in a few minutes.

The Claude backend calls `claude-sonnet-4-5` via the Anthropic API. The improvement shows most clearly during enrichment, where the LLM is making judgment calls about relevance, resolving ambiguous affiliation text, and synthesising across two or three web pages that may partially contradict each other. Hallucination risk is higher in those passes because the context is messier, and Claude's instruction-following and refusal to fabricate are noticeably stronger there. The cost for a typical ten-entity run is small — a few cents — but it is worth knowing when it adds value.

To use the Claude backend, set the API key once as a persistent system environment variable:

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."
```

Reopen the terminal after running `setx`. Alternatively, set `CLAUDE_API_KEY` directly in `config.py`.

---

## Caching

Every step writes its output to disk before the next step begins. On a re-run, each entity's cache file is checked first and the step is skipped if it exists. This means you can iterate quickly: change the LLM backend and re-run only analysis, add a new entity and re-run only for that entity, or skip straight to the report if enrichment already ran.

| Step | Cache location |
|---|---|
| Scraping | `data/raw/<slug>.json` |
| Analysis | `data/analyzed/<slug>.json` |
| Enrichment | `data/enriched/<slug>.json` |
| Discovery | `data/discovered.json` |

The slug is derived from the entity name by lowercasing and replacing non-alphanumeric characters with underscores, truncated to 60 characters. Per-step force flags (`--force-scrape`, `--force-analyze`, `--force-enrich`, `--force-discover`) let you invalidate one layer without touching the others. `--force` invalidates everything. The Streamlit UI exposes the same options as checkboxes.

One thing the cache does not do is detect when your inputs change. If you update the topic description in `config.py` or add URLs to the CSV, the old cache files are still considered valid. Run `--force` after any meaningful input change.

---

## Input format

The topic and entity list live in a CSV file under `data/input/`. The file format uses hash-prefixed metadata lines before the header row, which lets the same file carry both configuration and data:

```csv
#topic,organic bioelectronics for plants
#description,Companies and research groups working on OECTs applied to plants
#discovery_queries,plant bioelectronics startup|OECT plant research|electronic plants lab
#topic_keywords,bioelectron|OECT|plant|xylem|conducting polymer
#domain_label,Plant application
name,url,notes
Linköping University — LOE,https://liu.se/en/research/organic-electronics,Pioneer group. Magnus Berggren.
Vivent SA,https://www.vivent.ch,Swiss company. Electrical signal monitoring in plants.
New Entity,,no URL yet — will be scraped via DDG fallback
```

The `#discovery_queries` and `#topic_keywords` lines override the defaults in `config.py` for this run, which means each topic CSV can carry its own domain-specific search vocabulary. The `|` character separates items within a field. Entities with no URL are still included — they contribute their notes text as context, and any discovered URL from the discovery pass gets attached at runtime. Discovered entities can be appended back to the file from the CLI prompt or the UI's save button.

---

## Streamlit UI

The UI is a single-page Streamlit app that wraps the CLI without reimplementing anything. It lets you select a CSV from `data/input/`, choose between the Ollama and Claude backends, toggle individual pipeline steps on and off (scrape, analyze, enrich, report), enable or force the discovery pass, and optionally force a full cache refresh. Before running, it shows the exact command that will be executed.

Progress is tracked through a milestone panel that polls `data/run_status.json` every 1.5 seconds. The status file is written by `main.py` at each pipeline transition and includes the current step, a detail string, and a timestamp. The UI renders it as a vertical list of steps, each marked as pending, running, done, or errored. After a run completes, a button opens the generated report in the browser. If discovery ran and found new entities, the UI lists them and offers a save button.

<!-- SCREENSHOT: Streamlit UI with the milestone panel mid-run, showing one step done (green), one running (blue), rest pending -->

```bash
streamlit run app_ui.py
```

---

## Setup

```bash
git clone https://github.com/leoturambar/competitive-intelligence.git
cd competitive-intelligence

conda create -n ci-tool python=3.11
conda activate ci-tool
pip install -r requirements.txt
```

The dependencies are `requests`, `beautifulsoup4`, and `streamlit`. If you use the Ollama backend, you need Ollama installed and running locally with `qwen2.5:14b` pulled. If you use the Claude backend, you need an Anthropic API key set in your environment.

---

## Usage

```bash
# Run on a CSV topic file
python main.py --entities data/input/entities_plant_bioelectronics.csv

# With discovery enabled
python main.py --entities data/input/entities_plant_bioelectronics.csv --discover

# Use existing cache, skip scraping
python main.py --skip-scrape

# Skip enrichment (faster, less complete)
python main.py --skip-enrich

# Full fresh run, ignore all caches
python main.py --force

# Granular cache control
python main.py --force-scrape --force-analyze

# Re-run discovery only, keep everything else cached
python main.py --discover --force-discover --skip-scrape
```

---

## Project structure

```
competitive-intelligence/
├── config.py           # LLM backend, topic defaults, discovery queries and keywords
├── csv_loader.py       # CSV parsing, save_discovered_to_csv()
├── scraper.py          # Fetch and clean text from entity URLs, cache to data/raw/
├── analyzer.py         # LLM structured extraction per entity, Ollama + Claude backends
├── enricher.py         # Three targeted DDG+LLM passes, Semantic Scholar link resolution
├── discoverer.py       # Discovery: DDG → affiliation regex → keyword verification → LLM
├── reporter.py         # Enriched JSON → self-contained HTML report
├── main.py             # Pipeline orchestration, run_status.json, CLI argument handling
├── app_ui.py           # Streamlit UI
├── requirements.txt
└── data/
    ├── input/          # CSV topic files
    ├── raw/            # Cached scraped text (per entity)
    ├── analyzed/       # Cached LLM extraction output (per entity)
    ├── enriched/       # Cached enrichment output (per entity)
    └── reports/        # Generated HTML reports
```

---

## Output quality

Results vary by entity. Organisations with a rich public web presence — active lab pages, recent publications, a company site — tend to produce complete and reliable cards. Entities with minimal online footprint often have sparse fields, particularly for key people and notable outputs. All output should be reviewed before use. The tool accelerates the research process; it does not replace the domain expertise needed to interpret what the landscape means.

The enrichment step is the one most sensitive to LLM quality. If you see hallucinated or vague enrichment results with Ollama, switching to Claude tends to fix it.

---

## License

MIT — see [LICENSE](LICENSE)
