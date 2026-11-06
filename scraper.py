# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — scraper.py
# ─────────────────────────────────────────────
#
#  For each entity:
#    1. If no URL is given, search for its homepage (DDG, aggregator sites
#       excluded, result must share distinctive words with the entity name).
#    2. Fetch the configured URLs.
#    3. Follow up to N internal "about / team / research / products" links
#       from the first page, so the LLM sees more than a landing page.
#    4. Keep a per-source log (url, title, chars, error) for the report.
#
#  Entities are scraped in parallel; politeness is enforced per host in web.py.
#  Cache: data/projects/<slug>/raw/<entity>.json (fingerprinted on urls+notes).
# ─────────────────────────────────────────────

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import config
import web
from project import Project, fingerprint, load_cached, save_cached

TEXT_BUDGET     = max(config.MAX_TEXT_CHARS, 12000)   # chars per entity passed to the LLM
SUBPAGE_LIMIT   = 2
MAX_WORKERS     = 4
SUBPAGE_PATTERN = re.compile(
    r"about|who-we-are|team|people|research|technolog|product|solution|platform|"
    r"science|publication|pipeline|projects?\b|members|group", re.I)
SUBPAGE_SKIP = re.compile(r"login|signin|cart|privacy|cookie|terms|career|jobs|news/|blog/|\.pdf$|"
                          r"\.jpg$|\.png$|contact|press|events?/", re.I)


def find_homepage(name: str, topic: str) -> tuple[str | None, str]:
    """Search for an entity's homepage. Returns (url, reason)."""
    tokens = web.name_tokens(name)
    for query in (f"{web.shorten_query(name, '', max_name_words=5)} official site",
                  web.shorten_query(name, topic)):
        for r in web.ddg_search(query, max_results=8):
            if web.is_aggregator(r.url):
                continue
            hay = f"{r.title} {web.domain_of(r.url)}".lower()
            hits = sum(1 for t in tokens if t in hay)
            if tokens and hits >= max(1, min(2, len(tokens) // 2)):
                return r.url, f"search: '{query}'"
    return None, "no homepage found"


def _pick_subpages(page: web.Page, limit: int) -> list[str]:
    base = web.domain_of(page.url)
    picked, seen = [], {page.url.rstrip("/")}
    for href, text in page.links:
        clean = href.split("#")[0].rstrip("/")
        if not clean or clean in seen or web.domain_of(clean) != base:
            continue
        path = urlparse(clean).path
        if SUBPAGE_SKIP.search(path) or path.count("/") > 4:
            continue
        if SUBPAGE_PATTERN.search(path) or SUBPAGE_PATTERN.search(text or ""):
            seen.add(clean)
            picked.append(clean)
        if len(picked) >= limit:
            break
    return picked


def scrape_entity(entity: dict, topic: str) -> dict:
    name  = entity["name"]
    urls  = list(entity.get("urls") or [])
    url_source = "input"
    sources = []

    if not urls:
        found, reason = find_homepage(name, topic)
        if found:
            urls, url_source = [found], "search"
            print(f"    [FOUND] {name} → {found}")
        else:
            url_source = "none"
            sources.append({"url": None, "ok": False, "error": reason})

    chunks, used = [], 0
    first_page = None
    for url in urls:
        page = web.fetch(url)
        sources.append({"url": url, "title": page.title, "ok": page.ok and bool(page.text),
                        "chars": len(page.text), "error": page.error})
        if page.ok and page.text:
            first_page = first_page or page
            piece = page.text[: TEXT_BUDGET // max(1, len(urls))]
            chunks.append(f"[Source: {url} | {page.title}]\n{page.description}\n{piece}")
            used += len(piece)

    if first_page and used < TEXT_BUDGET * 0.8:
        remaining = TEXT_BUDGET - used
        for sub in _pick_subpages(first_page, SUBPAGE_LIMIT):
            page = web.fetch(sub)
            sources.append({"url": sub, "title": page.title, "ok": page.ok and bool(page.text),
                            "chars": len(page.text), "error": page.error, "subpage": True})
            if page.ok and page.text:
                piece = page.text[: remaining // 2]
                chunks.append(f"[Source: {sub} | {page.title}]\n{piece}")
                remaining -= len(piece)

    if entity.get("notes"):
        chunks.append(f"[Analyst notes]: {entity['notes']}")

    text = "\n\n".join(chunks)[:TEXT_BUDGET + 2000]
    return {
        "name": name,
        "urls": urls,
        "url_source": url_source,
        "notes": entity.get("notes", ""),
        "tags": entity.get("tags", []),
        "openalex_id": entity.get("openalex_id"),
        "discovered": bool(entity.get("_discovered") or entity.get("discovered")),
        "website": (first_page.url if first_page else (urls[0] if urls else None)),
        "site_name": first_page.name_hint if first_page else "",
        "text": text,
        "sources": sources,
        "fetch_ok": any(s.get("ok") for s in sources),
    }


def scrape_all(project: Project, entities: list[dict], force: bool = False,
               on_progress=None, workers: int = MAX_WORKERS) -> dict:
    """Scrape entities (cached). Returns {entity_name: raw_payload}."""
    results, todo = {}, []
    for e in entities:
        fp = fingerprint(e["name"], e.get("urls") or [], e.get("notes", ""))
        cached = None if force else load_cached(project.entity_path("raw", e["name"]), fp)
        # A cache whose fetches all failed is retried (sites are often only briefly down)
        if cached and (cached.get("fetch_ok") or not (e.get("urls") or cached.get("urls"))):
            print(f"  [CACHE] {e['name']}")
            results[e["name"]] = cached
        else:
            todo.append((e, fp))

    total, done = len(entities), len(results)
    if on_progress:
        on_progress(done, total, "cached" if done else "")

    def _work(item):
        e, fp = item
        print(f"  [SCRAPE] {e['name']}")
        payload = scrape_entity(e, project.topic)
        return e["name"], save_cached(project.entity_path("raw", e["name"]), payload, fp)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_work, item) for item in todo]
        for fut in as_completed(futures):
            try:
                name, payload = fut.result()
                results[name] = payload
                ok = sum(1 for s in payload["sources"] if s.get("ok"))
                print(f"    ✓ {name}: {ok}/{len(payload['sources'])} sources, {len(payload['text'])} chars")
            except Exception as ex:      # one broken entity must not kill the run
                print(f"    [ERROR] scrape failed: {ex}")
            done += 1
            if on_progress:
                on_progress(done, total, "")

    # Preserve input order
    return {e["name"]: results[e["name"]] for e in entities if e["name"] in results}


if __name__ == "__main__":
    import sys
    proj = Project.from_csv(sys.argv[1]) if len(sys.argv) > 1 else Project.from_config()
    data = scrape_all(proj, proj.entities, force=True)
    print(f"\nDone. Scraped {len(data)} entities.")
