"""Offline unit tests for the pipeline's deterministic parts (no network, no LLM)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import analyzer  # noqa: E402
import csv_loader  # noqa: E402
import discoverer  # noqa: E402
import enricher  # noqa: E402
import llm  # noqa: E402
import openalex  # noqa: E402
import project as proj  # noqa: E402
import reporter  # noqa: E402
import web  # noqa: E402


# ── CSV ───────────────────────────────────────

EXCEL_CSV = """#topic,Edible Electronics ,
#description,"Devices made from food-safe materials — for health, packaging ",
#topic_keywords,edible|ingestible|gut sensor ,
#domain_label,Edible application ,
name,url,notes
Tufts Lab ,https://a.example/ | https://b.example/x ,Silk pioneer.
No URL Co,,needs search
Tufts Lab,https://dup.example,duplicate row
"""


def test_metadata_trailing_commas_and_quotes():
    d = csv_loader.parse_csv_text(EXCEL_CSV)
    assert d["topic"] == "Edible Electronics"
    assert d["description"] == "Devices made from food-safe materials — for health, packaging"
    assert d["topic_keywords"] == ["edible", "ingestible", "gut sensor"]
    assert d["domain_label"] == "Edible application"


def test_unquoted_commas_in_description_are_kept():
    d = csv_loader.parse_csv_text("#description,Sensors using clocks, NV centers, SQUIDs\nname,url,notes\n")
    assert d["description"] == "Sensors using clocks, NV centers, SQUIDs"


def test_entities_multi_url_and_dedupe():
    ents = csv_loader.parse_csv_text(EXCEL_CSV)["entities"]
    assert [e["name"] for e in ents] == ["Tufts Lab", "No URL Co"]
    assert ents[0]["urls"] == ["https://a.example/", "https://b.example/x"]
    assert ents[1]["urls"] == []


def test_csv_roundtrip(tmp_path):
    d = csv_loader.parse_csv_text(EXCEL_CSV)
    p = csv_loader.write_csv(d, tmp_path / "x.csv")
    d2 = csv_loader.load_csv(p)
    for k in ("topic", "description", "topic_keywords", "domain_label"):
        assert d2[k] == d[k]
    assert d2["entities"] == d["entities"]


def test_save_discovered_skips_known(tmp_path):
    p = tmp_path / "t.csv"
    csv_loader.write_csv(csv_loader.parse_csv_text(EXCEL_CSV), p)
    csv_loader.save_discovered_to_csv([
        {"name": "tufts lab", "urls": ["https://zzz.example"]},          # name already known
        {"name": "Other", "urls": ["https://a.example"]},                 # URL already known
        {"name": "Brand New Lab", "urls": ["https://new.example"], "notes": "n"},
    ], str(p))
    names = [e["name"] for e in csv_loader.load_csv(p)["entities"]]
    assert names == ["Tufts Lab", "No URL Co", "Brand New Lab"]


# ── Project / cache ───────────────────────────

def test_slugify_ascii_and_no_truncation_collisions():
    assert proj.slugify("Linköping University — LOE") == "linkoping_university_loe"
    a = proj.slugify("Umeå Plant Science Centre — Department of Forest Genetics and Plant Physiology A")
    b = proj.slugify("Umeå Plant Science Centre — Department of Forest Genetics and Plant Physiology B")
    assert a != b and len(a) <= 60 and len(b) <= 60


def test_fingerprinted_cache(tmp_path):
    path = tmp_path / "c.json"
    fp1, fp2 = proj.fingerprint("url-a", "notes"), proj.fingerprint("url-b", "notes")
    proj.save_cached(path, {"x": 1}, fp1)
    assert proj.load_cached(path, fp1)["x"] == 1
    assert proj.load_cached(path, fp2) is None


def test_project_from_csv_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "data" / "input" / "entities_demo.csv"
    csv_loader.write_csv(csv_loader.parse_csv_text(EXCEL_CSV), p)
    pr = proj.Project.from_csv(p)
    assert pr.slug == "demo"
    assert pr.entity_path("raw", "Tufts Lab") == Path("data/projects/demo/raw/tufts_lab.json")
    assert pr.keywords == ["edible", "ingestible", "gut sensor"]


# ── LLM output parsing / normalisation ────────

@pytest.mark.parametrize("raw", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    'Sure! Here it is: {"a": 1} hope that helps',
    '<think>hmm {"no": 0}</think>{"a": 1}',
    '{"a": 1,}',
])
def test_parse_json_variants(raw):
    assert llm.parse_json(raw)["a"] == 1


def test_parse_json_nested_braces_in_strings():
    assert llm.parse_json('x {"s": "a } b", "n": {"k": 2}} y') == {"s": "a } b", "n": {"k": 2}}


def test_parse_json_failure():
    with pytest.raises(ValueError):
        llm.parse_json("no json here")


def test_strictify_adds_additional_properties():
    s = llm._strictify(analyzer.SCHEMA)
    assert s["additionalProperties"] is False
    assert s["properties"]["key_people"]["items"]["additionalProperties"] is False


def test_normalize_enums_scores_and_legacy_strings():
    rec = analyzer.normalize({
        "identity": "Not an organization",
        "entity_type": "university", "development_stage": "teleportation",
        "competitive_position": "niche", "relevance_score": "4.6", "evidence_quality": "high",
        "key_people": "Ada Lovelace (PI), Alan Turing",
        "notable_outputs": [{"type": "Paper", "title": "A study", "year": None}, {"type": "x", "title": "a study"}],
        "tags": ["OECT", "oect", "Plants"], "summary": "null",
    })
    assert rec["identity"] == "Not an organisation"
    assert rec["entity_type"] == "University"
    assert rec["development_stage"] == "Unknown"           # unknown label → Unknown, not a guess
    assert rec["competitive_position"] == "Niche player"
    assert rec["relevance_score"] == 5
    assert rec["evidence_quality"] == "High"
    assert [p["name"] for p in rec["key_people"]] == ["Ada Lovelace (PI)", "Alan Turing"]
    assert len(rec["notable_outputs"]) == 1 and rec["notable_outputs"][0]["type"] == "paper"
    assert rec["tags"] == ["oect", "plants"]
    assert rec["summary"] is None


def test_score_is_clamped():
    assert analyzer.normalize({"relevance_score": 9})["relevance_score"] == 5
    assert analyzer.normalize({"relevance_score": "n/a"})["relevance_score"] is None


# ── Web helpers ───────────────────────────────

def test_html_to_page_prefers_main_and_strips_noise():
    html = """<html><head><title>Lab X</title><meta property="og:site_name" content="Lab X Site">
    <meta name="description" content="We build sensors."></head><body>
    <nav>menu menu</nav><main>""" + ("Real content about OECT sensors. " * 30) + """
    <a href="/about">About us</a></main><footer>cookie banner</footer><script>evil()</script></body></html>"""
    page = web.html_to_page("https://lab.example/", html)
    assert page.site_name == "Lab X Site" and page.description == "We build sensors."
    assert "menu" not in page.text and "cookie" not in page.text and "evil" not in page.text
    assert ("https://lab.example/about", "About us") in page.links


def test_aggregator_detection_and_query_shortening():
    assert web.is_aggregator("https://pubmed.ncbi.nlm.nih.gov/123")
    assert web.is_aggregator("https://www.researchgate.net/x")
    assert not web.is_aggregator("https://www.vivent.ch")
    q = web.shorten_query("College of Engineering, Huazhong Agricultural University", "organic bioelectronics for plants")
    assert "Huazhong" in q and "College" not in q


# ── Discovery filters ─────────────────────────

@pytest.mark.parametrize("cand,ok", [
    ("Donghuangchenggen North Street, Beijing", False),
    ("Pushchino, Russia Find articles by Yulia V. Plekhanova", False),
    ("Department of Physics, Politecnico di Milano", True),
    ("12 Main Road University Campus", False),
    ("Some Random Words Here", False),
])
def test_affiliation_prefilter(cand, ok):
    assert discoverer.prefilter(cand) is ok


def test_matches_known_by_tokens_and_domain():
    known = [{"name": "Linköping University — Laboratory of Organic Electronics", "urls": ["https://liu.se/x"]}]
    assert discoverer._matches_known({"name": "Linköping University"}, known)
    assert discoverer._matches_known({"name": "Something else", "url": "https://liu.se/other"}, known)
    assert not discoverer._matches_known({"name": "Uppsala University", "url": "https://uu.se"}, known)


# ── OpenAlex matching (offline, stubbed API) ──

def test_match_institution_requires_name_coverage(monkeypatch):
    fake = {"results": [
        {"id": "https://openalex.org/I1", "display_name": "Vivent (Switzerland)", "works_count": 3, "type": "company"},
        {"id": "https://openalex.org/I2", "display_name": "Uppsala University", "works_count": 99999, "type": "education"},
    ]}
    monkeypatch.setattr(openalex, "_get", lambda path, params: fake)
    m = openalex.match_institution("Vivent SA")
    assert m and m["id"] == "I1"
    monkeypatch.setattr(openalex, "_get", lambda path, params: {"results": fake["results"][1:]})
    assert openalex.match_institution("Vivent SA") is None


# ── Enrichment merging ────────────────────────

def test_merge_people_dedupes_name_variants():
    out = enricher.merge_people(
        [{"name": "Magnus Berggren", "role": None}],
        [{"name": "Magnus Berggren A", "role": "11 topic papers", "topic_works": 11, "url": "https://openalex.org/A1"}],
        [{"name": "Eleni Stavrinidou", "role": "PI"}])
    assert [p["name"] for p in out] == ["Magnus Berggren", "Eleni Stavrinidou"]
    assert out[0]["topic_works"] == 11 and out[0]["role"] == "11 topic papers"


def test_merge_outputs_dedupes_and_links_patents(monkeypatch):
    monkeypatch.setattr(openalex, "find_work", lambda t: None)
    out = enricher.merge_outputs(
        [{"type": "paper", "title": "Electronic plants", "url": "https://doi.org/10/x"}],
        [{"type": "paper", "title": "Electronic Plants!"}, {"type": "patent", "title": "Plant electrode EP 1234567 A1"}],
        [{"label": "Product: Sensor v2", "url": None}],
        website="https://co.example")
    titles = [o["title"] for o in out]
    assert titles == ["Electronic plants", "Plant electrode EP 1234567 A1", "Product: Sensor v2"]
    assert out[1]["url"] == "https://patents.google.com/patent/EP1234567A1"


# ── Report ────────────────────────────────────

def test_report_is_self_contained_and_escapes_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pr = proj.Project(slug="demo", topic="demo <topic>", description="d", entities=[])
    evil = "</script><script>alert(1)</script>"
    records = [{
        "entity_name": "Evil Corp", "entity_type": "Company", "summary": evil, "relevance_score": 4,
        "development_stage": "Pilot", "notable_outputs": [{"label": "Paper: x", "url": "javascript:alert(1)"}],
        "key_people": "A, B", "research": {"topic_works": 3, "series": [{"year": 2025, "count": 3}]},
    }]
    path = reporter.generate_report(pr, records, landscape={"executive_summary": "ok"})
    html = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html            # payload cannot close the data block
    assert "<title>Demo &lt;Topic&gt; — Competitive Landscape</title>" in html
    assert 'src="http' not in html and "href=\"http" not in html.split("<script")[0]   # no external assets
    data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    ent = data["entities"][0]
    assert ent["summary"] == evil and ent["people"][0]["name"] == "A"
    assert ent["outputs"][0]["title"] == "Paper: x"
    assert path.with_suffix(".csv").exists()


def test_bibliometric_floor_only_for_exact_institution():
    res = {"institution": {"display_name": "Umeå Plant Science Centre"}, "topic_works": 11}
    assert enricher.bibliometric_floor("Umeå Plant Science Centre", res) == 3
    assert enricher.bibliometric_floor("Umeå Plant Science Centre", {**res, "topic_works": 20}) == 4
    assert enricher.bibliometric_floor("Umeå Plant Science Centre", {**res, "topic_works": 2}) is None
    big = {"institution": {"display_name": "Cornell University"}, "topic_works": 40}
    assert enricher.bibliometric_floor("Cornell University — Bioelectronics Lab", big) is None
