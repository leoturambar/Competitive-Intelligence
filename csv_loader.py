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
        "slug":              str,
        "discovery_queries": list,   # optional, overrides config
        "topic_keywords":    list,   # optional, overrides config
        "domain_label":      str,    # optional, overrides config
    }
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    topic              = ""
    description        = ""
    entities           = []
    discovery_queries  = []
    topic_keywords     = []
    domain_label       = ""
    slug               = p.stem  # e.g. "entities_plant_bioelectronics"

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
            if stripped.startswith("#discovery_queries,"):
                discovery_queries = [q.strip() for q in stripped[len("#discovery_queries,"):].split("|") if q.strip()]
                continue
            if stripped.startswith("#topic_keywords,"):
                topic_keywords = [k.strip() for k in stripped[len("#topic_keywords,"):].split("|") if k.strip()]
                continue
            if stripped.startswith("#domain_label,"):
                domain_label = stripped[len("#domain_label,"):].strip()
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
        "topic":             topic,
        "description":       description,
        "entities":          entities,
        "slug":              slug,
        "discovery_queries": discovery_queries,
        "topic_keywords":    topic_keywords,
        "domain_label":      domain_label,
    }


def csv_slug_to_report_name(slug: str, date_str: str) -> str:
    """Convert CSV slug to report filename."""
    # Remove common prefixes like "entities_"
    clean = re.sub(r"^entities_", "", slug)
    return f"competitive_intelligence_{clean}_{date_str}.html"


def save_discovered_to_csv(new_entities: list, csv_path: str | None = None) -> str:
    import csv as _csv
    from datetime import date

    if csv_path is None:
        csv_path = f"data/input/entities_discovered_{date.today().isoformat()}.csv"

    p = Path(csv_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        with open(p, "a", encoding="utf-8", newline="") as f:
            writer = _csv.writer(f)
            for e in new_entities:
                url   = e["urls"][0] if e.get("urls") else ""
                notes = e.get("notes", "")
                writer.writerow([e["name"], url, notes])
    else:
        with open(p, "w", encoding="utf-8", newline="") as f:
            writer = _csv.writer(f)
            f.write("#topic,\n")
            f.write("#description,Auto-generated from discovery\n")
            writer.writerow(["name", "url", "notes"])
            for e in new_entities:
                url   = e["urls"][0] if e.get("urls") else ""
                notes = e.get("notes", "")
                writer.writerow([e["name"], url, notes])

    return str(p)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "data/input/entities_plant_bioelectronics.csv"
    data = load_csv(path)
    print(f"Topic      : {data['topic']}")
    print(f"Description: {data['description']}")
    print(f"Entities   : {len(data['entities'])}")
    for e in data["entities"]:
        print(f"  {e['name']} — {e['urls']}")