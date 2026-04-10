# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — main.py
# ─────────────────────────────────────────────

import argparse
import sys

from config import LLM_BACKEND, TOPIC, REPORT_SUBTITLE
from scraper  import scrape_all
from analyzer import analyze_all
from reporter import generate_report


def run(force_scrape=False, force_analyze=False, skip_scrape=False):
    print(f"\n{'='*56}")
    print(f"  Competitive Intelligence Tool")
    print(f"  Topic : {TOPIC}")
    print(f"  LLM   : {LLM_BACKEND}")
    print(f"{'='*56}\n")

    # ── Step 1: Scrape ──────────────────────────
    if skip_scrape:
        print("[STEP 1] Scraping SKIPPED (loading from cache only)")
        from scraper import scrape_all as _s
        scraped = _s(force=False)
    else:
        print("[STEP 1] Scraping entity pages...")
        scraped = scrape_all(force=force_scrape)
        print(f"         → {len(scraped)} entities scraped\n")

    # ── Step 2: Analyze ─────────────────────────
    print("[STEP 2] Analysing with LLM...")
    analyzed = analyze_all(scraped, force=force_analyze)
    print(f"         → {len(analyzed)} entities analysed\n")

    # ── Step 3: Report ──────────────────────────
    print("[STEP 3] Generating report...")
    path = generate_report(analyzed)
    print(f"\n{'='*56}")
    print(f"  ✓ Report ready: {path}")
    print(f"{'='*56}\n")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Competitive Intelligence Tool — Organic Bioelectronics for Plants"
    )
    parser.add_argument(
        "--force-scrape",
        action="store_true",
        help="Re-scrape all entities even if cache exists"
    )
    parser.add_argument(
        "--force-analyze",
        action="store_true",
        help="Re-run LLM analysis even if cache exists"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force both scrape and analysis"
    )
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip scraping entirely, use existing raw cache"
    )
    args = parser.parse_args()

    run(
        force_scrape  = args.force or args.force_scrape,
        force_analyze = args.force or args.force_analyze,
        skip_scrape   = args.skip_scrape,
    )
