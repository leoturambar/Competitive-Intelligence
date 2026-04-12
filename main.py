# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — main.py
# ─────────────────────────────────────────────

import argparse
import json
from datetime import datetime
from pathlib import Path

import config
from scraper  import scrape_all
from analyzer import analyze_all
from enricher import enrich_all
from reporter import generate_report


# ── Status file (read by Streamlit UI) ───────

def _write_status(step: str, detail: str = "", done: bool = False, error: bool = False):
    """Write current run milestone to data/run_status.json for UI polling.

    Maintains a cumulative history of completed steps so the Streamlit UI
    can show each step as done (green) as the run progresses.
    """
    Path("data").mkdir(exist_ok=True)
    status_file = Path("data/run_status.json")

    # Carry forward the history of completed steps
    history = {}
    if status_file.exists():
        try:
            with open(status_file, encoding="utf-8") as f:
                existing = json.load(f)
            history = existing.get("history", {})
            cur = existing.get("current", existing)
            prev_step   = cur.get("step", "")
            prev_detail = cur.get("detail", "")
            # When we transition to a new step, mark the previous one as done
            if prev_step and prev_step != step and prev_step not in ("done", "error"):
                history[prev_step] = prev_detail
        except Exception:
            pass

    with open(status_file, "w", encoding="utf-8") as f:
        json.dump({
            "current": {
                "step":   step,
                "detail": detail,
                "done":   done,
                "error":  error,
                "ts":     datetime.now().isoformat(),
            },
            "history": history,
        }, f)


def apply_csv(csv_path: str):
    """Load topic and entities from CSV and override config globals."""
    from csv_loader import load_csv
    data = load_csv(csv_path)

    if data["topic"]:
        config.TOPIC = data["topic"]
    if data["description"]:
        config.TOPIC_DESCRIPTION = data["description"]
    if data["entities"]:
        config.ENTITIES = data["entities"]
    if data.get("discovery_queries"):
        config.DISCOVERY_QUERIES = data["discovery_queries"]
    if data.get("topic_keywords"):
        config.TOPIC_KEYWORDS = data["topic_keywords"]
    if data.get("domain_label"):
        config.DOMAIN_LABEL = data["domain_label"]
    config.REPORT_SUBTITLE = data["topic"].title()
    return data["slug"]


def _offer_save_to_csv(new_entities: list, csv_slug: str | None):
    """
    Ask user whether to save discovered entities back to CSV.
    Called from command line only — UI handles this separately.
    """
    if not new_entities:
        return
    print(f"\n{'─'*56}")
    print(f"  {len(new_entities)} new entities were discovered.")
    print(f"  Save them to the entity CSV for future runs? (y/n): ", end="", flush=True)
    try:
        answer = input().strip().lower()
    except EOFError:
        return
    if answer != "y":
        print("  Skipped.")
        return

    from csv_loader import save_discovered_to_csv
    target = f"data/input/{csv_slug}.csv" if csv_slug else None
    saved_path = save_discovered_to_csv(new_entities, target)
    print(f"  Saved -> {saved_path}")
    print(f"{'─'*56}\n")


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
        _write_status("discovery", "searching DuckDuckGo...")
        from discoverer import discover as run_discovery
        extra_entities = run_discovery(
            force=force_discover,
            on_status=lambda step, detail: _write_status(step, detail)
        )
        msg = f"{len(extra_entities)} new entities found"
        print(f"         -> {msg}\n")
        _write_status("discovery", msg, done=True)

    if extra_entities:
        with open("data/run_discovered.json", "w", encoding="utf-8") as f:
            json.dump(extra_entities, f)

    # ── Step 1: Scrape ──────────────────────────────────
    if skip_scrape:
        print("[STEP 1] Scraping SKIPPED (loading from cache only)")
        _write_status("scraping", "loading from cache", done=True)
        scraped = scrape_all(force=False, extra_entities=extra_entities,
                             on_progress=lambda i, t: _write_status("scraping", f"{i} / {t} entities"))
    else:
        n_total = len(config.ENTITIES) + len(extra_entities)
        print("[STEP 1] Scraping entity pages...")
        _write_status("scraping", f"0 / {n_total} entities")
        scraped = scrape_all(force=force_scrape, extra_entities=extra_entities,
                             on_progress=lambda i, t: _write_status("scraping", f"{i} / {t} entities"))
        msg = f"{len(scraped)} entities scraped"
        print(f"         -> {msg}\n")
        _write_status("scraping", msg, done=True)

    # ── Step 2: Analyze ─────────────────────────────────
    print("[STEP 2] Analysing with LLM...")
    _write_status("analysis", f"0 / {len(scraped)} entities")
    analyzed = analyze_all(scraped, force=force_analyze,
                           on_progress=lambda i, t: _write_status("analysis", f"{i} / {t} entities"))
    msg = f"{len(analyzed)} entities analysed"
    print(f"         -> {msg}\n")
    _write_status("analysis", msg, done=True)

    # ── Step 3: Enrich ──────────────────────────────────
    if skip_enrich:
        print("[STEP 3] Enrichment SKIPPED")
        _write_status("enrichment", "skipped", done=True)
        final = analyzed
    else:
        print("[STEP 3] Enriching (plant application, outputs, people)...")
        _write_status("enrichment", f"0 / {len(analyzed)} entities")
        final = enrich_all(analyzed, force=force_enrich,
                           on_progress=lambda i, t: _write_status("enrichment", f"{i} / {t} entities"))
        msg = f"{len(final)} entities enriched"
        print(f"         -> {msg}\n")
        _write_status("enrichment", msg, done=True)

    # ── Step 4: Report ──────────────────────────────────
    print("[STEP 4] Generating report...")
    _write_status("report", "generating...")
    from csv_loader import csv_slug_to_report_name
    from datetime import date
    filename = (
        csv_slug_to_report_name(csv_slug, date.today().isoformat())
        if csv_slug else None
    )
    path = generate_report(final, filename=filename)
    msg = path.name
    print(f"\n{'='*56}")
    print(f"  ✓ Report ready: {path}")
    print(f"{'='*56}\n")
    _write_status("done", msg, done=True)

    # ── Step 5: Save discovered entities to CSV (if any) ──
    if extra_entities and discover:
        _offer_save_to_csv(extra_entities, csv_slug)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Competitive Intelligence Tool")
    parser.add_argument("--entities",       type=str,          default=None)
    parser.add_argument("--discover",       action="store_true")
    parser.add_argument("--force-discover", action="store_true")
    parser.add_argument("--force-scrape",   action="store_true")
    parser.add_argument("--force-analyze",  action="store_true")
    parser.add_argument("--force-enrich",   action="store_true")
    parser.add_argument("--force",          action="store_true")
    parser.add_argument("--skip-scrape",    action="store_true")
    parser.add_argument("--skip-enrich",    action="store_true")
    args = parser.parse_args()

    csv_slug = None
    if args.entities:
        csv_slug = apply_csv(args.entities)

    try:
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
    except Exception as e:
        _write_status("error", str(e), error=True)
        raise