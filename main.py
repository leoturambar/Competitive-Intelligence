# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — main.py
# ─────────────────────────────────────────────

import argparse
import config
from scraper  import scrape_all
from analyzer import analyze_all
from enricher import enrich_all
from reporter import generate_report


def apply_csv(csv_path: str):
    """
    Load topic and entities from a CSV file and override config globals.
    This allows running the tool on different topics without editing config.py.
    """
    from csv_loader import load_csv
    data = load_csv(csv_path)

    if data["topic"]:
        config.TOPIC = data["topic"]
    if data["description"]:
        config.TOPIC_DESCRIPTION = data["description"]
    if data["entities"]:
        config.ENTITIES = data["entities"]

    # Update report subtitle to match topic
    config.REPORT_SUBTITLE = data["topic"].title()

    return data["slug"]


def run(
    force_scrape   = False,
    force_analyze  = False,
    force_enrich   = False,
    force_discover = False,
    skip_scrape    = False,
    skip_enrich    = False,
    discover       = False,
    csv_slug       = None,
):
    print(f"\n{'='*56}")
    print(f"  Competitive Intelligence Tool")
    print(f"  Topic : {config.TOPIC}")
    print(f"  LLM   : {config.LLM_BACKEND}")
    if csv_slug:
        print(f"  Input : {csv_slug}.csv")
    print(f"{'='*56}\n")

    # ── Step 0: Discovery (optional) ────────────────────
    extra_entities = []
    if discover:
        print("[STEP 0] Discovering new entities...")
        from discoverer import discover as run_discovery
        extra_entities = run_discovery(force=force_discover)
        print(f"         → {len(extra_entities)} new entities discovered\n")

    # ── Step 1: Scrape ──────────────────────────────────
    if skip_scrape:
        print("[STEP 1] Scraping SKIPPED (loading from cache only)")
        scraped = scrape_all(force=False, extra_entities=extra_entities)
    else:
        print("[STEP 1] Scraping entity pages...")
        scraped = scrape_all(force=force_scrape, extra_entities=extra_entities)
        print(f"         → {len(scraped)} entities scraped\n")

    # ── Step 2: Analyze ─────────────────────────────────
    print("[STEP 2] Analysing with LLM...")
    analyzed = analyze_all(scraped, force=force_analyze)
    print(f"         → {len(analyzed)} entities analysed\n")

    # ── Step 3: Enrich ──────────────────────────────────
    if skip_enrich:
        print("[STEP 3] Enrichment SKIPPED")
        final = analyzed
    else:
        print("[STEP 3] Enriching (plant application, outputs, people)...")
        final = enrich_all(analyzed, force=force_enrich)
        print(f"         → {len(final)} entities enriched\n")

    # ── Step 4: Report ──────────────────────────────────
    print("[STEP 4] Generating report...")
    from csv_loader import csv_slug_to_report_name
    from datetime import date
    filename = (
        csv_slug_to_report_name(csv_slug, date.today().isoformat())
        if csv_slug
        else None
    )
    path = generate_report(final, filename=filename)
    print(f"\n{'='*56}")
    print(f"  ✓ Report ready: {path}")
    print(f"{'='*56}\n")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Competitive Intelligence Tool"
    )
    parser.add_argument("--entities",       type=str, default=None,
                        help="Path to CSV file with topic and entity list")
    parser.add_argument("--discover",       action="store_true",
                        help="Auto-discover new entities via DDG + LLM")
    parser.add_argument("--force-discover", action="store_true",
                        help="Re-run discovery even if cache exists")
    parser.add_argument("--force-scrape",   action="store_true",
                        help="Re-scrape all entities")
    parser.add_argument("--force-analyze",  action="store_true",
                        help="Re-run LLM analysis")
    parser.add_argument("--force-enrich",   action="store_true",
                        help="Re-run enrichment passes")
    parser.add_argument("--force",          action="store_true",
                        help="Force all steps from scratch")
    parser.add_argument("--skip-scrape",    action="store_true",
                        help="Skip scraping, use existing raw cache")
    parser.add_argument("--skip-enrich",    action="store_true",
                        help="Skip enrichment, go straight to report")
    args = parser.parse_args()

    # Load CSV if provided — overrides config.py topic and entities
    csv_slug = None
    if args.entities:
        csv_slug = apply_csv(args.entities)

    run(
        force_scrape   = args.force or args.force_scrape,
        force_analyze  = args.force or args.force_analyze,
        force_enrich   = args.force or args.force_enrich,
        force_discover = args.force or args.force_discover,
        skip_scrape    = args.skip_scrape,
        skip_enrich    = args.skip_enrich,
        discover       = args.discover or args.force_discover,
        csv_slug       = csv_slug,
    )