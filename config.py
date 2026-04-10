# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — config.py
# ─────────────────────────────────────────────

# ── LLM backend ──────────────────────────────
# Options: "ollama" | "claude"
LLM_BACKEND = "ollama"

OLLAMA_MODEL  = "qwen2.5:14b"
CLAUDE_MODEL  = "claude-sonnet-4-5"
CLAUDE_API_KEY = ""          # set here or in env var ANTHROPIC_API_KEY

# ── Topic ────────────────────────────────────
TOPIC = "organic bioelectronics for plants"
TOPIC_DESCRIPTION = (
    "Companies, research institutes, and spinoffs working on organic electronic "
    "devices (OECTs, organic electrodes, conducting polymers) applied to living "
    "plants — for sensing, actuation, nutrient delivery, or plant-machine interfaces."
)

# ── Companies / entities to analyse ─────────────────
# Each entry: display name + one or more URLs to scrape
ENTITIES = [
    {
        "name": "Linköping University — Laboratory of Organic Electronics",
        "urls": ["https://liu.se/en/research/organic-electronics"],
        "notes": "Pioneer group (Magnus Berggren). Electronic plants, PEDOT:PSS in xylem."
    },
    {
        "name": "RISE Research Institutes of Sweden",
        "urls": ["https://www.ri.se/en/what-we-do/projects/electronic-plants"],
        "notes": "Applied R&D arm. Collaborates with Linköping on plant bioelectronics."
    },
    {
        "name": "Spiber Technologies",
        "urls": ["https://www.spiber.se"],
        "notes": "Swedish spinoff from LiU ecosystem. Bio-based materials."
    },
    {
        "name": "BioDevice Systems",
        "urls": ["https://biodevicesystems.com"],
        "notes": "Czech company, OECT-based biosensors, some plant/agricultural angle."
    },
    {
        "name": "Plantix / PEAT GmbH",
        "urls": ["https://plantix.net"],
        "notes": "Plant health diagnostics via AI — adjacent, digital agriculture."
    },
    {
        "name": "Vivent SA",
        "urls": ["https://www.vivent.ch"],
        "notes": "Swiss company. Electrical signal monitoring in plants (PhytlSigns technology)."
    },
    {
        "name": "Biome Makers",
        "urls": ["https://biomemakers.com"],
        "notes": "Soil microbiome analytics. Adjacent — plant health monitoring."
    },
    {
        "name": "Emili (Electronic Medicine in Living systems)",
        "urls": ["https://emili.eu"],
        "notes": "EU-funded consortium. Organic electronics for living systems, plant component."
    },
    {
        "name": "Wageningen University — Plant Sciences Group",
        "urls": ["https://www.wur.nl/en/research-results/research-institutes/plant-research.htm"],
        "notes": "Academic. Active in plant electrophysiology and biosensing."
    },
    {
        "name": "Cornell University — Bioelectronics Lab",
        "urls": ["https://bioelectronics.cornell.edu"],
        "notes": "US academic. Organic bioelectronics incl. plant interfaces."
    },
]

# ── Output fields extracted by LLM ───────────────────
# These are the fields the analyzer will populate for each entity
OUTPUT_FIELDS = [
    "entity_type",          # Company | Spinoff | Research Institute | University | Consortium
    "country",
    "founded_or_established",
    "technology_focus",     # 1-2 sentence description of their specific tech
    "plant_application",    # What exactly they do with/for plants
    "development_stage",    # Research | Prototype | Pilot | Commercial
    "funding_or_status",    # Funded by / revenue / grants known
    "key_people",           # Notable names if found
    "notable_outputs",      # Papers, patents, products
    "competitive_position", # LLM assessment: leader / follower / niche / adjacent
    "relevance_score",      # 1-5: how central to organic bioelectronics for plants
    "summary",              # 2-3 sentence executive summary
]

# ── Scraping settings ────────────────────────
REQUEST_TIMEOUT   = 12      # seconds
REQUEST_DELAY     = 1.5     # seconds between requests (be polite)
MAX_TEXT_CHARS    = 8000    # chars of scraped text passed to LLM per URL

# ── Report settings ──────────────────────────
REPORT_TITLE  = "Competitive Intelligence Report"
REPORT_SUBTITLE = "Organic Bioelectronics for Plants"
