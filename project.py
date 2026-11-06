# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — project.py
# ─────────────────────────────────────────────
#
#  A Project is one topic CSV plus its own workspace:
#
#    data/projects/<slug>/
#        raw/<entity>.json         scraped text + sources
#        analyzed/<entity>.json    LLM structured extraction
#        enriched/<entity>.json    + OpenAlex metrics, outputs, people
#        discovery.json            discovered entities + full candidate log
#        landscape.json            cross-entity synthesis
#        status.json               live run status (read by the UI)
#        run.log                   console log of the last run
#        reports/*.html|json|csv   generated reports and exports
#
#  Every cache file stores a fingerprint of the inputs that produced it,
#  so editing a URL, a note, or the topic description automatically
#  invalidates exactly the layers that depend on it.
# ─────────────────────────────────────────────

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config

DATA_DIR     = Path("data")
INPUT_DIR    = DATA_DIR / "input"
PROJECTS_DIR = DATA_DIR / "projects"

CACHE_VERSION = 2   # bump to invalidate all caches after a schema change


def slugify(text: str, max_len: int = 60) -> str:
    """ASCII slug; long names get a short hash suffix so truncation never collides."""
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", norm.lower()).strip("_") or "entity"
    if len(slug) > max_len:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[:max_len - 7].rstrip('_')}_{digest}"
    return slug


def fingerprint(*parts) -> str:
    blob = json.dumps([CACHE_VERSION, *parts], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def load_cached(path: Path, fp: str):
    """Return cached payload if it exists and was produced from the same inputs."""
    data = read_json(path)
    if isinstance(data, dict) and data.get("_fp") == fp:
        return data
    return None


def save_cached(path: Path, data: dict, fp: str) -> dict:
    data = dict(data)
    data["_fp"] = fp
    data["_updated"] = datetime.now().isoformat(timespec="seconds")
    write_json(path, data)
    return data


@dataclass
class Project:
    slug: str
    topic: str
    description: str = ""
    discovery_queries: list = field(default_factory=list)
    topic_keywords: list = field(default_factory=list)
    domain_label: str = "Domain application"
    entities: list = field(default_factory=list)
    csv_path: Path | None = None

    # ── Construction ──────────────────────────
    @classmethod
    def from_csv(cls, path: str | Path) -> "Project":
        from csv_loader import load_csv
        data = load_csv(path)
        slug = re.sub(r"^entities_", "", Path(path).stem)
        return cls(
            slug=slug,
            topic=data["topic"] or slug.replace("_", " "),
            description=data["description"],
            discovery_queries=data["discovery_queries"] or [],
            topic_keywords=data["topic_keywords"] or [],
            domain_label=data["domain_label"] or "Domain application",
            entities=data["entities"],
            csv_path=Path(path),
        )

    @classmethod
    def from_config(cls) -> "Project":
        return cls(
            slug="default",
            topic=config.TOPIC,
            description=config.TOPIC_DESCRIPTION,
            discovery_queries=list(config.DISCOVERY_QUERIES),
            topic_keywords=list(config.TOPIC_KEYWORDS),
            domain_label=config.DOMAIN_LABEL,
            entities=list(config.ENTITIES),
        )

    def to_dict(self) -> dict:
        return {
            "topic": self.topic, "description": self.description,
            "discovery_queries": self.discovery_queries, "topic_keywords": self.topic_keywords,
            "domain_label": self.domain_label, "entities": self.entities,
        }

    def save_csv(self, path: str | Path | None = None) -> Path:
        from csv_loader import write_csv
        target = Path(path or self.csv_path or INPUT_DIR / f"entities_{self.slug}.csv")
        self.csv_path = target
        return write_csv(self.to_dict(), target)

    # ── Effective settings (CSV overrides config defaults) ──
    @property
    def queries(self) -> list:
        if self.discovery_queries:
            return self.discovery_queries
        return [f"{self.topic} company startup", f"{self.topic} research lab university",
                f"{self.topic} research group", f"{self.topic} spinoff"]

    @property
    def keywords(self) -> list:
        if self.topic_keywords:
            return self.topic_keywords
        return [w for w in re.findall(r"[A-Za-z][\w-]{3,}", self.topic)]

    @property
    def context_fp(self) -> str:
        """Fingerprint of the topic definition (affects every LLM step)."""
        return fingerprint(self.topic, self.description, self.domain_label)

    # ── Paths ─────────────────────────────────
    @property
    def dir(self) -> Path:
        return PROJECTS_DIR / self.slug

    raw_dir       = property(lambda self: self.dir / "raw")
    analyzed_dir  = property(lambda self: self.dir / "analyzed")
    enriched_dir  = property(lambda self: self.dir / "enriched")
    reports_dir   = property(lambda self: self.dir / "reports")
    discovery_path = property(lambda self: self.dir / "discovery.json")
    landscape_path = property(lambda self: self.dir / "landscape.json")
    status_path   = property(lambda self: self.dir / "status.json")
    log_path      = property(lambda self: self.dir / "run.log")

    def entity_path(self, layer: str, name: str) -> Path:
        return self.dir / layer / f"{slugify(name)}.json"

    # ── Legacy bridge ─────────────────────────
    def apply_to_config(self) -> None:
        """Mirror project settings into config globals (for any code still reading them)."""
        config.TOPIC = self.topic
        config.TOPIC_DESCRIPTION = self.description
        config.ENTITIES = self.entities
        config.DISCOVERY_QUERIES = self.queries
        config.TOPIC_KEYWORDS = self.keywords
        config.DOMAIN_LABEL = self.domain_label
        config.REPORT_SUBTITLE = self.topic.strip().title()

    # ── Results ───────────────────────────────
    def load_results(self) -> list[dict]:
        """Best available record per entity (enriched > analyzed), current entities only."""
        out = []
        for e in self.all_entities(include_discovered=True):
            rec = read_json(self.entity_path("enriched", e["name"])) or \
                  read_json(self.entity_path("analyzed", e["name"]))
            if rec:
                out.append(rec)
        return out

    def discovered_entities(self, statuses: tuple = ("new", "accepted")) -> list[dict]:
        """Discovered entities still in play (dismissed ones are excluded)."""
        disc = read_json(self.discovery_path, {}) or {}
        return [c for c in disc.get("entities", []) if c.get("status", "new") in statuses]

    def all_entities(self, include_discovered: bool = False) -> list[dict]:
        ents = list(self.entities)
        if include_discovered:
            known = {e["name"].lower() for e in ents}
            for d in self.discovered_entities():
                if d["name"].lower() not in known:
                    ents.append(d)
        return ents


def list_projects() -> list[Path]:
    return sorted(INPUT_DIR.glob("*.csv")) if INPUT_DIR.exists() else []


# ── Run status (polled by the UI) ─────────────

STEPS = ["discovery", "scraping", "analysis", "enrichment", "landscape", "report"]


class Status:
    """Writes a structured status file: one record per step with progress counters."""

    def __init__(self, project: Project, steps: list[str]):
        self.path = project.status_path
        self.data = {
            "project": project.slug,
            "started": datetime.now().isoformat(timespec="seconds"),
            "state": "running",
            "steps": {s: {"state": "pending", "done": 0, "total": 0, "detail": ""} for s in steps},
            "current": None,
            "message": "",
            "report": None,
        }
        self._flush()

    def _flush(self):
        self.data["ts"] = datetime.now().isoformat(timespec="seconds")
        try:
            write_json(self.path, self.data)
        except OSError:
            pass   # a locked status file must never kill a run

    def start(self, step: str, total: int = 0, detail: str = ""):
        st = self.data["steps"].setdefault(step, {})
        st.update(state="running", done=0, total=total, detail=detail)
        self.data["current"] = step
        self._flush()

    def progress(self, step: str, done: int, total: int | None = None, detail: str = ""):
        st = self.data["steps"].setdefault(step, {"state": "running"})
        st["done"] = done
        if total is not None:
            st["total"] = total
        if detail:
            st["detail"] = detail
        self._flush()

    def finish(self, step: str, detail: str = "", state: str = "done"):
        st = self.data["steps"].setdefault(step, {})
        st.update(state=state, detail=detail)
        if st.get("total"):
            st["done"] = st["total"]
        self._flush()

    def complete(self, report: str | None = None, message: str = ""):
        self.data.update(state="done", current=None, report=report, message=message)
        self._flush()

    def fail(self, message: str):
        cur = self.data.get("current")
        if cur:
            self.data["steps"][cur]["state"] = "error"
        self.data.update(state="error", message=message)
        self._flush()
