# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — main.py
# ─────────────────────────────────────────────
#
#  Pipeline:  [discovery] → scraping → analysis → enrichment → landscape → report
#
#  Each step caches per project (data/projects/<slug>/) with input
#  fingerprints, so re-runs only redo what changed.
#
#  Examples:
#    python main.py --entities data/input/entities_plant_bioelectronics.csv
#    python main.py --entities ... --discover
#    python main.py --entities ... --only "Vivent SA" --force-analyze
#    python main.py --entities ... --report-only
# ─────────────────────────────────────────────

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

# Windows consoles default to cp1252: never let a "→" crash the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.chdir(Path(__file__).resolve().parent)   # data paths are relative to the project root

import llm  # noqa: E402
from project import Project, Status, read_json  # noqa: E402


class _Tee:
    """Mirror stdout into the project's run.log (read live by the UI)."""

    def __init__(self, stream, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream, self.file = stream, open(path, "w", encoding="utf-8", buffering=1)

    def write(self, s):
        try:
            self.stream.write(s)
        except Exception:
            pass
        self.file.write(s)

    def flush(self):
        for f in (self.stream, self.file):
            try:
                f.flush()
            except Exception:
                pass


def _banner(project: Project):
    print(f"\n{'=' * 60}")
    print("  Competitive Intelligence Tool")
    print(f"  Project : {project.slug}  ({len(project.entities)} entities)")
    print(f"  Topic   : {project.topic}")
    print(f"  LLM     : {llm.describe()}")
    print(f"{'=' * 60}\n")


def run(
    project: Project,
    discover: bool = False,
    force_discover: bool = False,
    force_scrape: bool = False,
    force_analyze: bool = False,
    force_enrich: bool = False,
    force_landscape: bool = False,
    skip_scrape: bool = False,
    skip_enrich: bool = False,
    skip_landscape: bool = False,
    use_openalex: bool = True,
    use_web_enrich: bool = True,
    only: list[str] | None = None,
    report_only: bool = False,
    save_discovered: bool | None = None,
) -> Path:
    from analyzer import analyze_all
    from enricher import enrich_all
    from landscape import synthesize
    from reporter import generate_report
    from scraper import scrape_all

    project.apply_to_config()
    _banner(project)
    t0 = time.time()

    steps = (["discovery"] if discover and not report_only else []) + \
            ([] if report_only else ["scraping", "analysis"]) + \
            ([] if (skip_enrich or report_only) else ["enrichment"]) + \
            ([] if skip_landscape else ["landscape"]) + ["report"]
    status = Status(project, steps)

    try:
        if report_only:
            records = project.load_results()
            print(f"[REPORT-ONLY] {len(records)} cached entity records")
        else:
            # ── Discovery ─────────────────────────────
            if discover:
                print("[STEP 0] Discovering new entities...")
                status.start("discovery", detail="searching")
                from discoverer import discover as run_discovery
                found = run_discovery(project, force=force_discover,
                                      on_status=lambda msg: status.progress("discovery", 0, detail=msg),
                                      use_openalex=use_openalex)
                status.finish("discovery", f"{len(found)} new entities")
                print(f"         -> {len(found)} new entities\n")

            entities = project.all_entities(include_discovered=True)
            if only:
                wanted = {o.lower() for o in only}
                entities = [e for e in entities if e["name"].lower() in wanted
                            or any(w in e["name"].lower() for w in wanted)]
                print(f"  Restricted to {len(entities)} entities: {[e['name'] for e in entities]}\n")

            # ── Scraping ──────────────────────────────
            print("[STEP 1] Scraping entity pages...")
            status.start("scraping", total=len(entities))
            if skip_scrape:
                scraped = {}
                for e in entities:
                    raw = read_json(project.entity_path("raw", e["name"]))
                    if raw:
                        scraped[e["name"]] = raw
                print(f"  loaded {len(scraped)} from cache (scraping skipped)")
            else:
                scraped = scrape_all(project, entities, force=force_scrape,
                                     on_progress=lambda d, t, m="": status.progress("scraping", d, t, m))
            status.finish("scraping", f"{len(scraped)} entities")
            print(f"         -> {len(scraped)} entities scraped\n")

            # ── Analysis ──────────────────────────────
            print("[STEP 2] Analysing with LLM...")
            status.start("analysis", total=len(scraped))
            analyzed = analyze_all(project, scraped, force=force_analyze,
                                   on_progress=lambda d, t, m="": status.progress("analysis", d, t, m))
            failed = sum(1 for r in analyzed.values() if r.get("_error"))
            status.finish("analysis", f"{len(analyzed)} analysed" + (f", {failed} failed" if failed else ""))
            print(f"         -> {len(analyzed)} entities analysed ({failed} failed)\n")

            # ── Enrichment ────────────────────────────
            if skip_enrich:
                print("[STEP 3] Enrichment skipped\n")
            else:
                print("[STEP 3] Enriching (research profile, news, people, outputs)...")
                status.start("enrichment", total=len(analyzed))
                enrich_all(project, analyzed, scraped, force=force_enrich,
                           use_web=use_web_enrich, use_openalex=use_openalex,
                           on_progress=lambda d, t, m="": status.progress("enrichment", d, t, m))
                status.finish("enrichment", f"{len(analyzed)} entities")
                print()
            # The report always covers the full project, not just --only entities
            records = project.load_results()

        # ── Landscape synthesis ───────────────────
        landscape = None
        if not skip_landscape:
            print("[STEP 4] Synthesising landscape...")
            status.start("landscape", total=1)
            landscape = synthesize(project, records, force=force_landscape)
            status.finish("landscape", "done" if not landscape.get("error") else "failed")
            print()

        # ── Report ────────────────────────────────
        print("[STEP 5] Generating report...")
        status.start("report", total=1)
        path = generate_report(project, records, landscape=landscape,
                               filename=f"report_{date.today().isoformat()}.html")
        status.finish("report", path.name)

        elapsed = time.time() - t0
        s = llm.STATS
        summary = (f"{len(records)} entities · {elapsed / 60:.1f} min · {s['calls']} LLM calls"
                   + (f" · {s['input_tokens'] + s['output_tokens']:,} tokens" if s["input_tokens"] else ""))
        status.complete(report=str(path), message=summary)
        print(f"\n{'=' * 60}\n  ✓ Report ready: {path}\n  {summary}\n{'=' * 60}\n")
    except KeyboardInterrupt:
        status.fail("Cancelled by user")
        raise
    except Exception as e:
        status.fail(f"{type(e).__name__}: {e}")
        raise

    if discover and save_discovered is not False:
        _offer_save(project, save_discovered)
    return path


def _offer_save(project: Project, auto: bool | None):
    new = [e for e in project.discovered_entities(("new",))]
    if not new:
        return
    if auto is None:
        if not sys.stdin or not sys.stdin.isatty():
            print(f"  {len(new)} discovered entities kept for review (use the UI or --save-discovered).")
            return
        print(f"\n  {len(new)} new entities were discovered:")
        for e in new:
            print(f"    • {e['name']}  — {e.get('evidence') or ''}")
        try:
            answer = input("  Add them to the project CSV? (y/n): ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            print("  Skipped.")
            return
    from csv_loader import save_discovered_to_csv
    from discoverer import set_status
    target = project.csv_path or Path(f"data/input/entities_{project.slug}.csv")
    saved = save_discovered_to_csv(new, str(target))
    for e in new:
        set_status(project, e["name"], "accepted")
    print(f"  Saved -> {saved}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Competitive Intelligence Tool")
    p.add_argument("--entities", type=str, default=None, help="topic CSV (default: config.py)")
    p.add_argument("--discover", action="store_true", help="run discovery before scraping")
    p.add_argument("--force", action="store_true", help="ignore every cache")
    for step in ("discover", "scrape", "analyze", "enrich", "landscape"):
        p.add_argument(f"--force-{step}", action="store_true")
    p.add_argument("--skip-scrape", action="store_true", help="use cached pages only (offline)")
    p.add_argument("--skip-enrich", action="store_true")
    p.add_argument("--skip-landscape", action="store_true")
    p.add_argument("--no-openalex", action="store_true", help="disable OpenAlex research data")
    p.add_argument("--no-web-enrich", action="store_true", help="disable the news/funding web pass")
    p.add_argument("--only", action="append", help="process only entities matching this name (repeatable)")
    p.add_argument("--report-only", action="store_true", help="rebuild the report from cached results")
    p.add_argument("--save-discovered", action="store_true", help="append discovered entities to the CSV")
    p.add_argument("--backend", choices=["ollama", "claude"], help="override LLM backend")
    p.add_argument("--model", help="override model name for the chosen backend")
    args = p.parse_args(argv)

    if args.backend:
        os.environ["CI_LLM_BACKEND"] = args.backend
    if args.model:
        os.environ["CI_CLAUDE_MODEL" if llm.backend() == "claude" else "CI_OLLAMA_MODEL"] = args.model

    project = Project.from_csv(args.entities) if args.entities else Project.from_config()
    sys.stdout = _Tee(sys.stdout, project.log_path)

    ok, msg = llm.check_backend()
    if not ok:
        print(f"[ERROR] {msg}")
        Status(project, ["analysis"]).fail(msg)
        sys.exit(2)

    run(
        project,
        discover=args.discover or args.force_discover,
        force_discover=args.force or args.force_discover,
        force_scrape=args.force or args.force_scrape,
        force_analyze=args.force or args.force_analyze,
        force_enrich=args.force or args.force_enrich,
        force_landscape=args.force or args.force_landscape,
        skip_scrape=args.skip_scrape,
        skip_enrich=args.skip_enrich,
        skip_landscape=args.skip_landscape,
        use_openalex=not args.no_openalex,
        use_web_enrich=not args.no_web_enrich,
        only=args.only,
        report_only=args.report_only,
        save_discovered=True if args.save_discovered else None,
    )


if __name__ == "__main__":
    main()
