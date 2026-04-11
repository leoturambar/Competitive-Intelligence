# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — csv_loader.py
# ─────────────────────────────────────────────
#
#  Reads topic and entity list from a CSV file.
#
#  CSV format:
#    #topic,<topic string>
#    #description,<description string>
#    name,url,notes          ← header row
#    Entity Name,https://...,optional notes
#    ...
#
#  Lines starting with # are metadata, not entities.
#  The url column may be empty — entity will use DDG fallback.
# ─────────────────────────────────────────────

import csv
import re
from pathlib import Path


def load_csv(path: str) -> dict:
    """
    Parse a topic CSV file.
    Returns:
    {
        "topic":       str,
        "description": str,
        "entities":    [{"name": ..., "urls": [...], "notes": ...}, ...]
        "slug":        str   # filename without extension, for output naming
    }
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    topic       = ""
    description = ""
    entities    = []
    slug        = p.stem  # e.g. "entities_plant_bioelectronics"

    with open(p, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue

            # Metadata lines
            if stripped.startswith("#topic,"):
                topic = stripped[len("#topic,"):].strip()
                continue
            if stripped.startswith("#description,"):
                description = stripped[len("#description,"):].strip()
                continue
            if stripped.startswith("#"):
                continue  # ignore other comment lines

            break  # first non-comment, non-empty line = header — stop pre-scan

    # Now read entities via csv.DictReader, skipping # lines
    with open(p, encoding="utf-8") as f:
        filtered = (line for line in f if not line.startswith("#"))
        reader = csv.DictReader(filtered)
        for row in reader:
            name = (row.get("name") or "").strip()
            url  = (row.get("url")  or "").strip()
            note = (row.get("notes") or "").strip()

            if not name:
                continue

            entity = {
                "name":  name,
                "urls":  [url] if url else [],
                "notes": note,
            }
            entities.append(entity)

    return {
        "topic":       topic,
        "description": description,
        "entities":    entities,
        "slug":        slug,
    }


def csv_slug_to_report_name(slug: str, date_str: str) -> str:
    """Convert CSV slug to report filename."""
    # Remove common prefixes like "entities_"
    clean = re.sub(r"^entities_", "", slug)
    return f"competitive_intelligence_{clean}_{date_str}.html"


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "data/input/entities_plant_bioelectronics.csv"
    data = load_csv(path)
    print(f"Topic      : {data['topic']}")
    print(f"Description: {data['description']}")
    print(f"Entities   : {len(data['entities'])}")
    for e in data["entities"]:
        print(f"  {e['name']} — {e['urls']}")