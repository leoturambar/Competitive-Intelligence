# ─────────────────────────────────────────────
#  COMPETITIVE INTELLIGENCE TOOL — web.py
# ─────────────────────────────────────────────
#
#  Shared network helpers used by every pipeline step:
#    - one pooled requests.Session with retries
#    - polite, per-host rate limiting
#    - HTML → clean text extraction (main content first)
#    - DuckDuckGo HTML search
#    - query shortening for long academic names
# ─────────────────────────────────────────────

from __future__ import annotations

import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]

NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form",
              "noscript", "iframe", "svg", "button", "template"]

# Domains that host *content about* entities rather than the entities themselves.
# A page on these domains is never accepted as an entity's homepage.
AGGREGATOR_DOMAINS = {
    "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov", "sciencedirect.com", "springer.com",
    "link.springer.com", "nature.com", "wiley.com", "onlinelibrary.wiley.com",
    "mdpi.com", "frontiersin.org", "researchgate.net", "academia.edu", "arxiv.org",
    "biorxiv.org", "x-mol.com", "semanticscholar.org", "scholar.google.com",
    "wikipedia.org", "linkedin.com", "crunchbase.com", "youtube.com", "twitter.com",
    "x.com", "facebook.com", "instagram.com", "medium.com", "reddit.com",
    "acs.org", "pubs.acs.org", "rsc.org", "pubs.rsc.org", "iop.org", "iopscience.iop.org",
    "tandfonline.com", "cell.com", "science.org", "pnas.org", "plos.org",
    "journals.plos.org", "europepmc.org", "doi.org", "patents.google.com",
    "news.google.com", "phys.org", "sciencedaily.com", "eurekalert.org",
    "techxplore.com", "duckduckgo.com", "bing.com", "google.com", "openalex.org",
    "dimensions.ai", "pitchbook.com", "zoominfo.com", "glassdoor.com", "indeed.com",
}

_session_lock = threading.Lock()
_session: requests.Session | None = None
_host_last: dict[str, float] = {}
_host_lock = threading.Lock()


def session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            retry = Retry(total=2, backoff_factor=1.0,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=("GET", "POST"))
            adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
            s.mount("http://", adapter)
            s.mount("https://", adapter)
            _session = s
        return _session


def _headers() -> dict:
    return {
        "User-Agent": random.choice(UA_LIST),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    host = host.split(":")[0]
    return host[4:] if host.startswith("www.") else host


def is_aggregator(url: str) -> bool:
    d = domain_of(url)
    return any(d == a or d.endswith("." + a) for a in AGGREGATOR_DOMAINS)


def _throttle(url: str, delay: float):
    """Wait so that consecutive requests to the same host are >= delay apart."""
    host = domain_of(url)
    while True:
        with _host_lock:
            now = time.monotonic()
            last = _host_last.get(host, 0.0)
            wait = last + delay - now
            if wait <= 0:
                _host_last[host] = now
                return
        time.sleep(min(wait, delay))


# ── Page fetching ─────────────────────────────

@dataclass
class Page:
    url: str
    ok: bool = False
    status: int | None = None
    title: str = ""
    site_name: str = ""
    description: str = ""
    text: str = ""
    links: list = field(default_factory=list)   # [(absolute_url, anchor_text)]
    error: str = ""

    @property
    def name_hint(self) -> str:
        return self.site_name or self.title


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def html_to_page(url: str, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    page = Page(url=url, ok=True)

    og_site = soup.find("meta", property="og:site_name")
    og_desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    page.site_name = _clean(og_site.get("content", "")) [:120] if og_site else ""
    page.description = _clean(og_desc.get("content", ""))[:400] if og_desc else ""
    if soup.title:
        page.title = _clean(soup.title.get_text())[:160]
    elif soup.find("h1"):
        page.title = _clean(soup.find("h1").get_text())[:160]

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        page.links.append((urljoin(url, href), _clean(a.get_text())[:80]))

    for tag in soup(NOISE_TAGS):
        tag.decompose()

    # Prefer the main content region when the page marks one up
    main = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    body_text = _clean((main or soup).get_text(separator=" "))
    if main and len(body_text) < 400:          # main was too thin — use the whole page
        body_text = _clean(soup.get_text(separator=" "))
    page.text = body_text
    return page


def fetch(url: str, timeout: float | None = None) -> Page:
    """Fetch a URL and return a parsed Page (never raises)."""
    timeout = timeout or config.REQUEST_TIMEOUT
    _throttle(url, config.REQUEST_DELAY)
    try:
        resp = session().get(url, headers=_headers(), timeout=timeout, allow_redirects=True)
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code >= 400:
            return Page(url=url, status=resp.status_code, error=f"HTTP {resp.status_code}")
        if "pdf" in ctype or url.lower().endswith(".pdf"):
            return Page(url=url, status=resp.status_code, error="PDF skipped")
        if "html" not in ctype and "xml" not in ctype and ctype:
            return Page(url=url, status=resp.status_code, error=f"unsupported content-type {ctype}")
        resp.encoding = resp.apparent_encoding or resp.encoding
        page = html_to_page(resp.url, resp.text)
        page.status = resp.status_code
        return page
    except Exception as e:
        return Page(url=url, error=f"{type(e).__name__}: {e}"[:200])


def fetch_text(url: str, max_chars: int | None = None) -> str:
    page = fetch(url)
    if not page.ok:
        print(f"      [WARN] {url}: {page.error}")
        return ""
    return page.text[:max_chars] if max_chars else page.text


# ── Search ────────────────────────────────────

@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""


_search_lock = threading.Lock()
_search_last = 0.0
_search_cache: dict = {}


def _parse_ddg_html(html: str) -> list[SearchResult]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for res in soup.select("div.result"):
        a = res.select_one("a.result__a")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            try:
                href = unquote(href.split("uddg=")[1].split("&")[0])
            except Exception:
                continue
        if "duckduckgo.com/y.js" in href:
            continue   # ad
        snip = res.select_one(".result__snippet")
        out.append(SearchResult(href, _clean(a.get_text()), _clean(snip.get_text()) if snip else ""))
    return out


def _parse_ddg_lite(html: str) -> list[SearchResult]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a.result-link"):
        href = a.get("href", "")
        if "uddg=" in href:
            href = unquote(href.split("uddg=")[1].split("&")[0])
        snippet = ""
        row = a.find_parent("tr")
        nxt = row.find_next_sibling("tr") if row else None
        if nxt and nxt.select_one(".result-snippet"):
            snippet = _clean(nxt.select_one(".result-snippet").get_text())
        out.append(SearchResult(href, _clean(a.get_text()), snippet))
    return out


def _brave(query: str) -> tuple[list[SearchResult], bool]:
    """Brave Search API (optional, set BRAVE_SEARCH_API_KEY; free tier ≈ 2k queries/month)."""
    resp = session().get("https://api.search.brave.com/res/v1/web/search",
                         params={"q": query, "count": 10},
                         headers={"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"],
                                  "Accept": "application/json"},
                         timeout=config.REQUEST_TIMEOUT)
    if resp.status_code == 429:
        return [], True
    if resp.status_code != 200:
        return [], False
    items = (resp.json().get("web") or {}).get("results") or []
    return [SearchResult(i.get("url", ""), _clean(i.get("title", "")),
                         _clean(BeautifulSoup(i.get("description", ""), "html.parser").get_text()))
            for i in items], False


def _ddg(url: str, parse):
    def run(query: str) -> tuple[list[SearchResult], bool]:
        resp = session().post(url, data={"q": query}, headers=_headers(), timeout=config.REQUEST_TIMEOUT)
        text = resp.text
        blocked = resp.status_code in (202, 403, 429) or "anomaly" in text.lower() or "bots use duckduckgo" in text.lower()
        return ([] if blocked else parse(text)), blocked
    return run


def engines() -> list:
    out = []
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        out.append(("brave", _brave))
    out += [("duckduckgo", _ddg("https://html.duckduckgo.com/html/", _parse_ddg_html)),
            ("duckduckgo-lite", _ddg("https://lite.duckduckgo.com/lite/", _parse_ddg_lite))]
    return out


_blocked_until: dict[str, float] = {}
BLOCK_COOLDOWN = 300.0
SEARCH_STATS = {"queries": 0, "blocked": 0, "empty": 0}


def search_available() -> bool:
    now = time.monotonic()
    return any(_blocked_until.get(n, 0) <= now for n, _ in engines())


def ddg_search(query: str, max_results: int = 6) -> list[SearchResult]:
    """Web search with engine fallback (Brave API if configured → DDG html → DDG lite).

    - Serialised and rate limited: search engines block bursts.
    - Circuit breaker: an engine that answers with a bot challenge is skipped
      for BLOCK_COOLDOWN seconds instead of being hammered (and never "solved").
    - Results are memoised for the process lifetime.
    The name is kept for backwards compatibility.
    """
    global _search_last
    key = (query.strip().lower(), max_results)
    if key in _search_cache:
        return _search_cache[key]

    results: list[SearchResult] = []
    tried = False
    with _search_lock:
        SEARCH_STATS["queries"] += 1
        for name, run in engines():
            if _blocked_until.get(name, 0) > time.monotonic():
                continue
            tried = True
            wait = _search_last + (0.5 if name == "brave" else config.DDG_DELAY) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                parsed, blocked = run(query)
            except Exception as e:
                print(f"      [WARN] {name} search failed: {e}")
                parsed, blocked = [], False
            finally:
                _search_last = time.monotonic()
            if blocked:
                SEARCH_STATS["blocked"] += 1
                _blocked_until[name] = time.monotonic() + BLOCK_COOLDOWN
                print(f"      [WARN] {name} is rate-limiting (bot check) — pausing it for {BLOCK_COOLDOWN / 60:.0f} min")
                continue
            if parsed:
                results = parsed
                break
        if not tried:
            print("      [SKIP] web search unavailable (all engines rate-limited)")
        elif not results:
            SEARCH_STATS["empty"] += 1

    seen, out = set(), []
    for r in results:
        if r.url.startswith("http") and r.url not in seen:
            seen.add(r.url)
            out.append(r)
        if len(out) >= max_results:
            break
    if results or not tried:
        _search_cache[key] = out if results else _search_cache.get(key, out)
    return out


def ddg_links(query: str, max_results: int = 6) -> list[str]:
    return [r.url for r in ddg_search(query, max_results)]


STOPWORDS = {
    "college", "department", "of", "and", "the", "for", "science", "sciences",
    "technology", "technologies", "institute", "division", "faculty", "school",
    "center", "centre", "laboratory", "lab", "group", "research", "inc", "ltd",
    "gmbh", "university", "a", "an", "in", "on", "at", "de", "di", "la",
}


def shorten_query(name: str, topic: str = "", max_name_words: int = 4, max_topic_words: int = 3) -> str:
    """Compact, DDG-friendly query from a long entity name (+ optional topic words).

    Keeps distinctive words (proper nouns, acronyms) and drops generic
    institutional vocabulary. Falls back to the raw name if everything is generic.
    """
    raw = re.sub(r"[,\-—–/()|·:]", " ", name)
    words = [w for w in raw.split() if w.lower() not in STOPWORDS and len(w) > 2]
    name_part = " ".join(words[:max_name_words]) or name
    topic_words = [w for w in re.sub(r"[^\w\s]", " ", topic).split()
                   if w.lower() not in STOPWORDS and len(w) > 3][:max_topic_words]
    return f"{name_part} {' '.join(topic_words)}".strip()


def name_tokens(name: str) -> set[str]:
    """Distinctive lowercase tokens of an entity name (for fuzzy matching)."""
    raw = re.sub(r"[^\w\s]", " ", name.lower())
    return {w for w in raw.split() if w not in STOPWORDS and len(w) > 2}
