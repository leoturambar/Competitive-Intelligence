# Competitive Intelligence Tool

An automated pipeline for mapping a niche scientific or technical landscape. Give it a topic and a few seed organisations; it reads their websites, pulls publication data from OpenAlex, mines news for funding and partnerships, finds organisations you missed, and produces an interactive report that says not only *who* is out there but *what it means*: who leads, how the field is segmented, where the commercial activity is, and where the gaps are.

<!-- SCREENSHOT: report Overview tab — KPIs, executive summary, key players, research momentum -->

---

## The problem

Mapping a competitive or research landscape in a niche technical field is slow work. You search, read, summarise, and repeat — and for topics like organic bioelectronics or plant-interfacing devices, the situation is worse than average: most of the field's footprint is papers and lab pages, not company sites. Standard intelligence tools either miss the academic layer entirely or produce noise because they have no domain context.

The tool turns that constraint into a feature. A paper-heavy field leaves a precise bibliometric trail — who publishes on the topic, how much, how recently, and with whom — and that trail is a far better discovery and ranking signal than keyword search alone.

---

## What you get

**An interactive, self-contained HTML report** (one file, works offline, prints cleanly):

| Tab | What it shows |
|---|---|
| **Overview** | KPIs, an executive summary, key players with the reason they matter, technology/application segments, trends, white spaces, a watchlist of emerging players, and research momentum |
| **Landscape** | Positioning map (development stage × relevance, sized by publication output, coloured by industry / academic / other) and distributions by type, stage and country — click any bar to filter |
| **Entities** | Searchable, filterable, sortable table (or card view) with per-entity publication sparklines; click a row for a full profile: summary, technology, application, business model, funding, strengths & risks, dated recent developments, research profile, key people, notable outputs with verified links, and every source used |
| **Compare** | Up to four organisations side by side |
| **Sources & method** | How the report was built, data-quality flags (non-organisations, thin evidence), discovered entities |

Every report is also exported as **CSV** and **JSON** next to the HTML.

**A Streamlit app** to create and edit projects, run the pipeline with live progress and logs, explore results, preview the report, and review discovered organisations one by one.

---

## The pipeline

```
[discovery] → scraping → analysis → enrichment → landscape synthesis → report
```

### Discovery (optional)

Two independent candidate sources feed one verification funnel.

**A. Bibliometric (OpenAlex).** Works whose title or abstract match the topic in the last eight years are grouped by author institution. Institutions with at least two papers become candidates, carrying their publication count and homepage as evidence. For the plant-bioelectronics prototype this immediately surfaces the real community — Linköping, the Swedish University of Agricultural Sciences, Umeå Plant Science Centre, Wallenberg Wood Science Center — with no noise from addresses or journal pages.

**B. Web affiliations.** The original pipeline, kept and hardened. DuckDuckGo searches collect paper and lab pages; `_regex_affiliations` slides a 400-word window over each page, keeps the region densest in affiliation markers, and pulls candidate organisation strings with four regex patterns. A pre-filter rejects postal addresses, "Find articles by…", DOIs and URLs. Each survivor is searched again with the topic appended and accepted only if a result page (1) is not an aggregator — journals, PubMed, ResearchGate, Wikipedia, LinkedIn and similar never count as an organisation's homepage — (2) mentions the candidate's own distinctive name words, and (3) contains at least two topic keywords. Conditions (1) and (2) fix the v1 failure mode where an affiliation string was "verified" by an unrelated page (e.g. a São Carlos affiliation resolving to a KAUST page).

**Funnel.** Candidates from both sources are deduplicated against known entities (by distinctive name tokens and by domain) and against each other, ranked by evidence, then reviewed by the LLM in two calls: a relevance check that also confirms the candidate is a real organisation, and a naming pass that produces a clean official name, type and one-line note. The naming pass works by index, so the model can never invent a name or URL. Every candidate and every decision — kept, dropped as not relevant, not verified, already known, duplicate — is logged to `discovery.json` and shown in the app, where you accept or dismiss each proposal. Accepted ones are appended to the project CSV.

### Scraping

Each entity's URLs are fetched (multiple URLs per entity supported). From the first page the scraper follows up to two internal *about / team / research / technology / products* links, so the model sees more than a landing page. Pages are cleaned of navigation, footers, scripts and cookie banners, preferring the `<main>`/`<article>` region. Entities with no URL get their homepage searched automatically (aggregators excluded, result must share name words with the entity). Requests are pooled, retried, and rate-limited per host; entities are scraped in parallel. A per-source log (URL, title, characters, errors) is kept and shown in the report.

### Analysis

One LLM call per entity, constrained by a JSON schema on both backends (Ollama structured outputs / Claude structured outputs), and instructed to use only the source text. Fields: identity check (organisation / research group / not an organisation), official name, type, country, city, founding date, technology focus, domain application, development stage, business model, funding/status, key people with roles, notable outputs, competitive position, a calibrated 1–5 relevance score *with a one-line rationale*, strengths, risks, tags, summary, and an evidence-quality rating. Results are normalised (enum matching, score clamping, list dedupe), so the report never has to guess. Failed analyses are not cached — they are retried on the next run instead of being frozen as "Analysis failed".

### Enrichment

Generic for any topic (v1's prompts were hard-wired to "living plants"):

- **Research profile (OpenAlex)** — the entity is matched to an OpenAlex institution (every distinctive word of the institution's name must appear in the entity's name, or the homepage domains must match). For the match: topic-filtered paper counts per year for the last decade, three-year momentum, the most-cited topic papers with real DOIs, and the most active authors on the topic. For Linköping this returns Eleni Stavrinidou and Magnus Berggren as the top authors — exactly right.
- **Web intelligence** — one news/funding/partnership search plus the entity's own pages, in a single LLM call extracting dated recent developments, funding, partners, a sharper domain application, and extra people and outputs.
- **Link resolution** — papers are looked up on OpenAlex and linked only on a close title match (v1 took Semantic Scholar's first hit, which was often a different paper); patents with a number link to Google Patents; products link to the website. People and outputs from all sources are merged and deduplicated.

### Landscape synthesis

Deterministic statistics (by type, stage, position, country; core players; total topic papers) plus one LLM pass over compact entity profiles producing the executive summary, key players, segments, trends, white spaces and watchlist. Organisation names in the output are validated against the mapped list, so the synthesis can only talk about organisations that are actually in the report.

---

## Caching

Each topic CSV is a **project** with its own workspace:

```
data/projects/<slug>/
    raw/ analyzed/ enriched/   one JSON per entity
    discovery.json             proposals + full candidate log
    landscape.json             synthesis
    status.json  run.log       live progress (read by the app)
    reports/                   report_<date>.html|.json|.csv
```

Every cache file stores a **fingerprint of the inputs that produced it**. Change an entity's URL or notes and only that entity is re-scraped and re-analysed; change the topic description and every LLM step is redone; switch model and analyses are redone with the new one. No more `--force` after editing inputs, and no more cross-topic contamination (in v1 all topics shared one cache, so an entity present in two topics reused the first topic's relevance score, and discovery results leaked between topics). Pages whose fetches all failed are retried on the next run. OpenAlex responses are cached for 14 days in `data/cache/openalex`.

`--force` and the per-step `--force-*` flags still exist for refreshing stale web data.

---

## LLM backends

| | Ollama (default) | Claude |
|---|---|---|
| Where | local, free, offline | Anthropic API |
| Default model | `qwen3.5:9b` (`config.OLLAMA_MODEL`) | `config.CLAUDE_MODEL` |
| Structured output | JSON-schema constrained decoding | `output_config.format` JSON schema |
| Parallelism | sequential | 4 entities at a time |

The Ollama context window is raised to 16k tokens (`CI_OLLAMA_NUM_CTX`); Ollama's small default silently truncated v1's long prompts. Backend and model can be switched in the app sidebar, with `--backend` / `--model`, or with the `CI_LLM_BACKEND`, `CI_OLLAMA_MODEL`, `CI_CLAUDE_MODEL` environment variables. Claude reads `ANTHROPIC_API_KEY` from the environment; `CI_CLAUDE_EFFORT` (default `low`) controls its effort level.

---

## Web search

Search is used for homepage lookup, discovery verification and the news pass. It goes through DuckDuckGo's HTML endpoints, serialised and rate-limited. When DuckDuckGo answers with a bot check, that engine is paused for five minutes (a circuit breaker) and the run continues without it — OpenAlex data, websites and analysis are unaffected. For reliable search at volume, set a **Brave Search API** key (free tier ≈ 2,000 queries/month) and it is used first:

```bash
setx BRAVE_SEARCH_API_KEY "..."
```

Optionally set `OPENALEX_API_KEY` for a dedicated OpenAlex quota.

---

## Input format

```csv
#topic,organic bioelectronics for plants
#description,Companies and research groups working on OECTs applied to plants
#discovery_queries,plant bioelectronics startup|OECT plant research|electronic plants lab
#topic_keywords,bioelectron|OECT|plant|xylem|conducting polymer
#domain_label,Plant application
name,url,notes
Linköping University — LOE,https://liu.se/en/research/organic-electronics,Pioneer group.
Vivent SA,https://www.vivent.ch | https://www.vivent.ch/technology,Plant electrophysiology.
New Entity,,no URL yet — the homepage will be searched
```

Metadata lines are parsed with the csv module, so quoted values and the trailing commas Excel adds are handled (in v1 they leaked into the topic and the last keyword, which then never matched). Several URLs per entity can be separated with `|`. Projects can be created and edited entirely from the app.

---

## Usage

```bash
streamlit run app_ui.py
```

```bash
python main.py --entities data/input/entities_plant_bioelectronics.csv
python main.py --entities data/input/entities_plant_bioelectronics.csv --discover
python main.py --entities ... --only "Vivent" --force-analyze      # one entity, fresh analysis
python main.py --entities ... --report-only                         # rebuild report from cache
python main.py --entities ... --skip-scrape                         # offline: cached pages only
python main.py --entities ... --no-web-enrich                       # OpenAlex only, no search
python main.py --entities ... --backend claude --model claude-opus-5-5
```

Run `python main.py --help` for every flag. Tests: `python -m pytest tests`.

---

## Setup

```bash
git clone https://github.com/leoturambar/competitive-intelligence.git
cd competitive-intelligence
conda create -n ci-tool python=3.11
conda activate ci-tool
pip install -r requirements.txt
ollama pull qwen3.5:9b        # for the local backend
```

---

## Project structure

```
competitive-intelligence/
├── config.py        # defaults: backend, models, topic, delays
├── project.py       # Project model, per-project paths, fingerprinted cache, run status
├── csv_loader.py    # robust CSV read/write, save discovered entities
├── web.py           # HTTP session, rate limiting, HTML cleaning, search with fallback/circuit breaker
├── llm.py           # Ollama + Claude backends, JSON-schema output, tolerant parsing
├── openalex.py      # institution matching, topic activity, paper lookup, bibliometric discovery
├── scraper.py       # homepage search, multi-page scraping
├── analyzer.py      # schema-constrained extraction + normalisation
├── enricher.py      # research profile, web intelligence, link resolution
├── discoverer.py    # bibliometric + web-affiliation discovery, verification funnel
├── landscape.py     # cross-entity statistics and synthesis
├── reporter.py      # interactive HTML report + CSV/JSON exports
├── main.py          # CLI and pipeline orchestration
├── app_ui.py        # Streamlit app
└── tests/           # offline unit tests
```

---

## Output quality

Organisations with a rich public footprint produce complete, well-sourced profiles; thin ones are marked *thin evidence* in the report so you know where to add URLs or notes. OpenAlex matching is institution-level: for a lab inside a large university, the publication counts cover the whole university's papers *on the topic* (which is usually what you want, but worth knowing). LLM output can still be wrong — every profile lists its sources so claims can be checked. The tool accelerates the research; it does not replace the domain expertise needed to interpret it.

---

## License

MIT — see [LICENSE](LICENSE)
