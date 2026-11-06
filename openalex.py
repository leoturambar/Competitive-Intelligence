# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — openalex.py
# ─────────────────────────────────────────────
#
#  Thin client for the OpenAlex scholarly graph (https://openalex.org).
#  Used for:
#    - matching an entity to an OpenAlex institution
#    - topic-specific publication counts per year (activity trend)
#    - most-cited topic papers with real DOIs (no hallucinated links)
#    - most active authors on the topic at that institution
#    - discovery: which institutions publish most on the topic
#
#  No key required. Set OPENALEX_API_KEY for a dedicated quota.
#  All responses are cached on disk (data/cache/openalex) for 14 days.
# ─────────────────────────────────────────────

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from datetime import date
from pathlib import Path

import web

BASE      = "https://api.openalex.org"
CACHE_DIR = Path("data/cache/openalex")
CACHE_TTL = 14 * 24 * 3600
_lock = threading.Lock()
_last = 0.0
MIN_INTERVAL = 0.25          # ≤ 4 req/s, well inside the polite limits

GENERIC = {"university", "universitat", "universität", "université", "universidad", "universita",
           "università", "institute", "institut", "instituto", "of", "the", "and", "for", "de", "di",
           "la", "der", "für", "technology", "science", "sciences", "research", "center", "centre",
           "national", "college", "school", "department", "laboratory", "lab", "group", "inc", "ltd",
           "gmbh", "sa", "ag", "ab", "srl", "spa", "llc", "corp", "corporation", "company"}


COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "DE": "Germany", "FR": "France", "IT": "Italy",
    "ES": "Spain", "PT": "Portugal", "NL": "Netherlands", "BE": "Belgium", "LU": "Luxembourg",
    "CH": "Switzerland", "AT": "Austria", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "FI": "Finland", "IS": "Iceland", "IE": "Ireland", "PL": "Poland", "CZ": "Czech Republic",
    "SK": "Slovakia", "HU": "Hungary", "RO": "Romania", "BG": "Bulgaria", "GR": "Greece",
    "SI": "Slovenia", "HR": "Croatia", "RS": "Serbia", "EE": "Estonia", "LV": "Latvia",
    "LT": "Lithuania", "UA": "Ukraine", "RU": "Russia", "TR": "Turkey", "IL": "Israel",
    "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "JP": "Japan", "KR": "South Korea",
    "SG": "Singapore", "MO": "Macau", "IN": "India", "AU": "Australia", "NZ": "New Zealand",
    "CA": "Canada", "MX": "Mexico", "BR": "Brazil", "AR": "Argentina", "CL": "Chile",
    "ZA": "South Africa", "EG": "Egypt", "SA": "Saudi Arabia", "AE": "United Arab Emirates",
    "IR": "Iran", "PK": "Pakistan", "TH": "Thailand", "MY": "Malaysia", "ID": "Indonesia",
    "VN": "Vietnam", "PH": "Philippines",
}


def country_name(code: str | None) -> str | None:
    return COUNTRY_NAMES.get((code or "").upper(), code) if code else None


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _tokens(s: str, drop_generic: bool = True) -> set[str]:
    s = re.sub(r"\([^)]*\)", " ", s)          # "Vivent (Switzerland)" → "Vivent"
    words = re.findall(r"[a-z0-9]+", _ascii(s))
    return {w for w in words if len(w) > 1 and not (drop_generic and w in GENERIC)}


def _get(path: str, params: dict) -> dict | None:
    global _last
    params = {k: v for k, v in params.items() if v is not None}
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    key = hashlib.sha1(json.dumps([path, sorted((k, str(v)) for k, v in params.items()
                                                if k != "api_key")]).encode()).hexdigest()
    cache = CACHE_DIR / f"{key}.json"
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_TTL:
            return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    with _lock:
        wait = _last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    try:
        resp = web.session().get(f"{BASE}{path}", params=params, timeout=20,
                                 headers={"User-Agent": "competitive-intelligence-tool/2.0"})
        if resp.status_code != 200:
            print(f"      [WARN] OpenAlex {path} → HTTP {resp.status_code}")
            return None
        data = resp.json()
    except Exception as e:
        print(f"      [WARN] OpenAlex request failed: {e}")
        return None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data


def topic_query(topic: str, keywords: list[str] | None = None) -> str:
    """Search string for title/abstract matching: topic words minus filler."""
    filler = {"for", "and", "the", "of", "in", "on", "with", "to", "a", "an", "applied", "based"}
    words = [w for w in re.findall(r"[\w-]+", topic) if w.lower() not in filler]
    return " ".join(words[:6])


def _short_id(oa_id: str) -> str:
    return oa_id.rsplit("/", 1)[-1] if oa_id else oa_id


# ── Institutions ──────────────────────────────

INST_FIELDS = "id,display_name,homepage_url,country_code,type,works_count,cited_by_count,geo,display_name_acronyms"


def match_institution(name: str, homepage: str | None = None) -> dict | None:
    """Best OpenAlex institution for an entity name, or None if no confident match.

    A match requires every distinctive token of the institution's name to appear
    in the entity name (or a homepage-domain match). Tries the full name, then
    each segment of names like "University X — Lab Y".
    """
    entity_tokens = _tokens(name)
    entity_domain = web.domain_of(homepage) if homepage else ""
    segments = [name] + [s.strip() for s in re.split(r"\s[—–-]\s|,|/|\|", name) if s.strip()]
    best, best_score = None, 0.0
    for seg in dict.fromkeys(segments):
        data = _get("/institutions", {"search": seg, "per_page": 8, "select": INST_FIELDS})
        for inst in (data or {}).get("results", []):
            inst_tokens = _tokens(inst.get("display_name", ""))
            acr = {a.lower() for a in inst.get("display_name_acronyms") or []}
            dom = web.domain_of(inst.get("homepage_url") or "")
            domain_hit = bool(entity_domain and dom and (entity_domain == dom or entity_domain.endswith("." + dom)))
            if not inst_tokens and not domain_hit:
                continue
            covered = inst_tokens <= entity_tokens or bool(acr & entity_tokens and len(inst_tokens) <= 2)
            if not (covered or domain_hit):
                continue
            # Prefer full name coverage, then shared words; penalise extra words
            # (e.g. a campus "…, Alnarp" sharing the parent's domain).
            score = ((10 if covered else 0) + (5 if domain_hit else 0)
                     + 2 * len(inst_tokens & entity_tokens) - 3 * len(inst_tokens - entity_tokens)
                     + min(inst.get("works_count", 0), 50000) / 50000)
            if score > best_score:
                best, best_score = inst, score
        if best is not None and seg == name:
            break
    return _inst_record(best) if best else None


def _inst_record(best: dict) -> dict:
    return {
        "id": _short_id(best["id"]),
        "display_name": best.get("display_name"),
        "homepage_url": best.get("homepage_url"),
        "country_code": best.get("country_code"),
        "type": best.get("type"),
        "works_count": best.get("works_count"),
        "cited_by_count": best.get("cited_by_count"),
        "city": (best.get("geo") or {}).get("city"),
        "url": f"https://openalex.org/{_short_id(best['id'])}",
    }


def institution(oa_id: str) -> dict | None:
    inst = institutions_by_id([oa_id]).get(_short_id(oa_id))
    return _inst_record(inst) if inst else None


def institutions_by_id(ids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(ids), 40):
        chunk = [_short_id(x) for x in ids[i:i + 40]]
        data = _get("/institutions", {"filter": "openalex:" + "|".join(chunk),
                                      "per_page": 50, "select": INST_FIELDS})
        for inst in (data or {}).get("results", []):
            out[_short_id(inst["id"])] = inst
    return out


# ── Topic activity at an institution ─────────

def _work_record(w: dict) -> dict:
    doi = w.get("doi")
    return {
        "type": "paper",
        "title": w.get("display_name") or w.get("title"),
        "year": str(w.get("publication_year")) if w.get("publication_year") else None,
        "doi": doi,
        "url": doi or (w.get("primary_location") or {}).get("landing_page_url") or w.get("id"),
        "cited_by": w.get("cited_by_count", 0),
        "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "source": "openalex",
    }


def topic_activity(inst_id: str, query: str, years: int = 10) -> dict:
    """Topic-specific publication profile of one institution."""
    flt = f"institutions.id:{inst_id},title_and_abstract.search:{query}"
    this_year = date.today().year
    by_year = _get("/works", {"filter": flt, "group_by": "publication_year", "per_page": 200}) or {}
    counts = {int(g["key"]): g["count"] for g in by_year.get("group_by", []) if str(g.get("key", "")).isdigit()}
    series = [{"year": y, "count": counts.get(y, 0)} for y in range(this_year - years + 1, this_year + 1)]

    top = _get("/works", {"filter": flt, "sort": "cited_by_count:desc", "per_page": 5,
                          "select": "id,doi,display_name,publication_year,cited_by_count,primary_location"}) or {}
    recent = _get("/works", {"filter": flt, "sort": "publication_date:desc", "per_page": 3,
                             "select": "id,doi,display_name,publication_year,cited_by_count,primary_location"}) or {}
    authors = _get("/works", {"filter": flt, "group_by": "authorships.author.id", "per_page": 8}) or {}

    total = (top.get("meta") or {}).get("count", 0)
    last3 = sum(c["count"] for c in series[-3:])
    prev3 = sum(c["count"] for c in series[-6:-3])
    return {
        "topic_works": total,
        "series": series,
        "momentum": (round((last3 - prev3) / prev3, 2) if prev3 else (1.0 if last3 else 0.0)),
        "top_works": [_work_record(w) for w in top.get("results", [])],
        "recent_works": [_work_record(w) for w in recent.get("results", [])],
        "top_authors": [
            {"name": g.get("key_display_name"), "topic_works": g.get("count"),
             "url": f"https://openalex.org/{_short_id(g.get('key', ''))}"}
            for g in authors.get("group_by", [])[:6] if g.get("key_display_name")
        ],
    }


# ── Paper lookup (link resolution) ───────────

def find_work(title: str) -> dict | None:
    """Look up a paper by title; accept only a close title match."""
    if not title or len(title) < 12:
        return None
    data = _get("/works", {"search": title, "per_page": 3,
                           "select": "id,doi,display_name,publication_year,cited_by_count,primary_location"})
    want = _tokens(title, drop_generic=False)
    for w in (data or {}).get("results", []):
        got = _tokens(w.get("display_name") or "", drop_generic=False)
        if want and got and len(want & got) / len(want | got) >= 0.6:
            return _work_record(w)
    return None


# ── Discovery: who publishes on this topic? ──

def top_institutions(query: str, n: int = 30, since_year: int | None = None) -> list[dict]:
    flt = f"title_and_abstract.search:{query}"
    if since_year:
        flt += f",from_publication_date:{since_year}-01-01"
    data = _get("/works", {"filter": flt, "group_by": "authorships.institutions.id", "per_page": n}) or {}
    groups = [g for g in data.get("group_by", []) if g.get("key") and g.get("key_display_name")]
    details = institutions_by_id([g["key"] for g in groups])
    out = []
    for g in groups:
        iid = _short_id(g["key"])
        inst = details.get(iid, {})
        out.append({
            "openalex_id": iid,
            "name": g["key_display_name"],
            "topic_works": g["count"],
            "homepage_url": inst.get("homepage_url"),
            "country_code": inst.get("country_code"),
            "type": inst.get("type"),
            "works_count": inst.get("works_count"),
        })
    return out
