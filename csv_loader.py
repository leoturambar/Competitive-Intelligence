# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — csv_loader.py
# ─────────────────────────────────────────────
#
#  Reads and writes topic CSV files.
#
#  Format:
#    #topic,<topic string>
#    #description,<description string>
#    #discovery_queries,query one|query two|...
#    #topic_keywords,kw1|kw2|...
#    #domain_label,<label for the domain-application field>
#    name,url,notes                 ← header row (optional extra column: tags)
#    Entity Name,https://...,optional notes
#
#  - Metadata lines are parsed with the csv module, so quoted values and
#    stray trailing commas (common when files are saved from Excel) work.
#  - The url column may hold several URLs separated by "|" or spaces.
#  - The url column may be empty — the scraper then searches for a homepage.
# ─────────────────────────────────────────────

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

META_KEYS = ("topic", "description", "discovery_queries", "topic_keywords", "domain_label")
LIST_KEYS = ("discovery_queries", "topic_keywords")


def _meta_value(line: str) -> tuple[str, str] | None:
    """Parse '#key,value[,...]' → (key, value). Unquoted commas in the value are kept."""
    try:
        fields = next(csv.reader([line.lstrip("﻿")]))
    except Exception:
        return None
    if not fields or not fields[0].startswith("#"):
        return None
    key = fields[0][1:].strip().lower()
    value = ", ".join(f.strip() for f in fields[1:] if f.strip())
    return key, value


def _split_list(value: str) -> list[str]:
    return [v.strip().strip(",").strip() for v in value.split("|") if v.strip().strip(",").strip()]


def _split_urls(value: str) -> list[str]:
    parts = re.split(r"[|\s]+", value or "")
    return [p.strip() for p in parts if p.strip().lower().startswith(("http://", "https://"))]


def parse_csv_text(text: str, slug: str = "project") -> dict:
    meta = {k: "" for k in META_KEYS}
    body_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("﻿#"):
            kv = _meta_value(stripped)
            if kv and kv[0] in META_KEYS:
                meta[kv[0]] = kv[1]
            continue
        body_lines.append(line)

    entities, seen = [], set()
    if body_lines:
        reader = csv.DictReader(io.StringIO("\n".join(body_lines)))
        reader.fieldnames = [(f or "").strip().lower().lstrip("﻿") for f in (reader.fieldnames or [])]
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            entity = {
                "name":  name,
                "urls":  _split_urls(row.get("url") or row.get("urls") or ""),
                "notes": (row.get("notes") or "").strip(),
            }
            tags = (row.get("tags") or "").strip()
            if tags:
                entity["tags"] = _split_list(tags.replace(";", "|"))
            entities.append(entity)

    return {
        "topic":             meta["topic"],
        "description":       meta["description"],
        "entities":          entities,
        "slug":              slug,
        "discovery_queries": _split_list(meta["discovery_queries"]),
        "topic_keywords":    _split_list(meta["topic_keywords"]),
        "domain_label":      meta["domain_label"],
    }


def load_csv(path: str | Path) -> dict:
    """Parse a topic CSV file. See module docstring for the format."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    return parse_csv_text(p.read_text(encoding="utf-8-sig"), slug=p.stem)


def render_csv(data: dict) -> str:
    """Serialise a project dict (same shape as load_csv output) back to CSV text."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["#topic", data.get("topic", "")])
    w.writerow(["#description", data.get("description", "")])
    if data.get("discovery_queries"):
        w.writerow(["#discovery_queries", "|".join(data["discovery_queries"])])
    if data.get("topic_keywords"):
        w.writerow(["#topic_keywords", "|".join(data["topic_keywords"])])
    if data.get("domain_label"):
        w.writerow(["#domain_label", data["domain_label"]])
    has_tags = any(e.get("tags") for e in data.get("entities", []))
    w.writerow(["name", "url", "notes"] + (["tags"] if has_tags else []))
    for e in data.get("entities", []):
        row = [e["name"], " | ".join(e.get("urls") or []), e.get("notes", "")]
        if has_tags:
            row.append("; ".join(e.get("tags") or []))
        w.writerow(row)
    return buf.getvalue()


def write_csv(data: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_csv(data), encoding="utf-8")
    return p


def csv_slug_to_report_name(slug: str, date_str: str) -> str:
    """Convert CSV slug to report filename."""
    clean = re.sub(r"^entities_", "", slug)
    return f"competitive_intelligence_{clean}_{date_str}.html"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def save_discovered_to_csv(new_entities: list, csv_path: str | None = None) -> str:
    """Append discovered entities to a topic CSV, skipping names/URLs already present."""
    from datetime import date

    if csv_path is None:
        csv_path = f"data/input/entities_discovered_{date.today().isoformat()}.csv"
    p = Path(csv_path)

    if p.exists():
        data = load_csv(p)
    else:
        data = {"topic": "", "description": "Auto-generated from discovery", "entities": []}

    known_names = {_norm(e["name"]) for e in data["entities"]}
    known_urls  = {u.rstrip("/") for e in data["entities"] for u in e.get("urls", [])}
    for e in new_entities:
        urls = [u for u in (e.get("urls") or []) if u]
        if _norm(e["name"]) in known_names or any(u.rstrip("/") in known_urls for u in urls):
            continue
        data["entities"].append({"name": e["name"], "urls": urls[:1], "notes": e.get("notes", "")})
        known_names.add(_norm(e["name"]))

    write_csv(data, p)
    return str(p)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "data/input/entities_plant_bioelectronics.csv"
    data = load_csv(path)
    print(f"Topic      : {data['topic']!r}")
    print(f"Description: {data['description']!r}")
    print(f"Keywords   : {data['topic_keywords']}")
    print(f"Entities   : {len(data['entities'])}")
    for e in data["entities"]:
        print(f"  {e['name']} — {e['urls']}")
