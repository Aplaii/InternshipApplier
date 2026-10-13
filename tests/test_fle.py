"""Deuxième modèle (dossier fle/) : stages FLE / DIFLES en Europe ou au Canada."""

from __future__ import annotations

import pytest

from app import automation, db, profiles
from app.scrapers import SEARCHERS, ScrapedOffer, SearchParams, wttj
from fle import PROFILE as FLE
from fle.regions import region_of, region_ok
from fle.topics import detect_topics


@pytest.fixture
def fle_profile():
    profiles.use(FLE)
    yield FLE
    profiles.use(None)


def offer(source_id: str, title: str, location: str = "", source: str = "linkedin") -> ScrapedOffer:
    return ScrapedOffer(source=source, source_id=source_id, url=f"https://example.org/{source_id}",
                        title=title, company="École", location=location)


# --------------------------------------------------------------------------- détecteur


@pytest.mark.parametrize("title, topics", [
    ("Stage FLE - Alliance Française de Bruxelles", ["FLE", "Alliance / Institut français"]),
    ("Stagiaire en français langue étrangère (DIFLES)", ["FLE"]),
    ("Stage professeur de français", ["Enseignement du français"]),
    ("French Teacher Intern", ["Enseignement du français"]),
    ("Teaching intern - French as a second language", ["FLS / immersion"]),
    ("Stage en francisation des personnes immigrantes", ["FLS / immersion"]),
    ("Stage FOS / français des affaires", ["FOS"]),
    ("Stage formatrice en français (alphabétisation)", ["Intégration / alphabétisation", "Enseignement du français"]),
    ("Stage didactique des langues", ["Didactique des langues"]),
    ("Profesor de francés (prácticas)", ["Enseignement du français"]),
    ("Praktikum Französischlehrer", ["Enseignement du français"]),
])
def test_detects_fle_offers(title, topics):
    assert detect_topics(title) == topics


@pytest.mark.parametrize("title", [
    "Stage Data Analyst", "Stage chargé de communication", "Stage ingénierie pédagogique",
    "Praktikum Marketing Französisch", "Stage flexible marketing", "Stage Ingénieur IA générative",
])
def test_rejects_other_offers(title):
    assert detect_topics(title) == []


def test_description_refines_fle_offers():
    # Thème non qualifiant (ingénierie pédagogique) ajouté seulement si l'offre est déjà FLE.
    assert detect_topics("Stage FLE", "Conception de ressources pédagogiques") == ["FLE", "Ingénierie pédagogique"]
    assert detect_topics("Stage e-learning", "Ingénierie pédagogique, ressources pédagogiques") == []
    # Une description qui insiste sur le FLE suffit.
    assert detect_topics("Stage assistant pédagogique", "Cours de FLE pour adultes. Public FLE débutant.") == ["FLE"]
    assert detect_topics("Stage assistant pédagogique", "Une mention du FLE.") == []


# --------------------------------------------------------------------------- zone


@pytest.mark.parametrize("location, region", [
    ("Montréal, QC", "canada"), ("Toronto, Ontario, Canada", "canada"), ("London, ON", "canada"),
    ("Berlin, Germany", "europe"), ("Bruxelles, Belgique", "europe"), ("Genève", "europe"),
    ("New York, NY", "other"), ("San Francisco, CA", "other"), ("Casablanca, Maroc", "other"),
    ("Saint-Denis - 974", "other"), ("Bogotá, Colombie", "other"),
    ("Lyon - 69", None), ("Saint-Maurice - 94", None), ("Télétravail", None), ("", None),
    ("Colombie-Britannique, Canada", "canada"),
])
def test_region_of(location, region):
    assert region_of(location) == region
    assert region_ok(location) is (region != "other")


# --------------------------------------------------------------------------- recherche


def test_plan_searches_spreads_over_profile_locations(fle_profile):
    params = SearchParams(keywords="", location="", max_results=60)
    plan = automation.plan_searches("linkedin", params, ["FLE", "French teacher"])
    assert [(p.keywords, p.location) for p in plan[:4]] == [
        ("FLE", "European Union"), ("FLE", "Switzerland"), ("FLE", "United Kingdom"), ("FLE", "Canada"),
    ]
    assert len(plan) == 8 and all(p.max_results == 15 for p in plan)
    # Un lieu saisi remplace la liste du profil ; les autres sites gardent le lieu saisi.
    assert [p.location for p in automation.plan_searches("linkedin", SearchParams(location="Montréal"), ["FLE"])] == ["Montréal"]
    hellowork = automation.plan_searches("hellowork", params, ["FLE"])
    assert [(p.location, p.max_results) for p in hellowork] == [("", 60)]
    # Welcome to the Jungle : bureaux en Europe et au Canada.
    query = wttj.build_query(automation.plan_searches("wttj", params, ["FLE"])[0], now_ts=0)
    assert "offices.country_code:CA" in query["filters"] and "offices.country_code:BE" in query["filters"]
    assert query["filters"].startswith("contract_type:internship AND (offices.country_code:FR OR ")


def test_wttj_default_country_is_france():
    assert wttj.build_query(SearchParams(), now_ts=0)["filters"].startswith("contract_type:internship AND offices.country_code:FR AND ")


def test_fetch_keeps_fle_offers_in_europe_or_canada(client, monkeypatch, fle_profile):
    seen = []

    async def linkedin(_client, params):
        seen.append((params.keywords, params.location))
        return [
            offer("1", "Stage FLE", "Montréal, QC"),
            offer("2", "Stage professeur de français", "Madrid, Spain"),
            offer("3", "Stage FLE", "New York, NY"),
            offer("4", "Stage marketing", "Paris"),
        ]

    monkeypatch.setitem(SEARCHERS, "linkedin", linkedin)
    result = client.post("/api/fetch", json={"sources": ["linkedin"], "max_per_source": 40}).json()
    assert {kw for kw, _ in seen} == set(FLE.default_queries)
    assert {loc for _, loc in seen} == set(FLE.search_locations["linkedin"])
    assert result["results"][0] == {
        "source": "linkedin", "label": "LinkedIn", "found": 4, "kept": 2, "off_region": 1, "new": 2, "error": None,
    }
    assert result["filtered_total"] == 1 and result["off_region_total"] == 1
    items = client.get("/api/offers").json()["items"]
    assert sorted(i["title"] for i in items) == ["Stage FLE", "Stage professeur de français"]
    meta = client.get("/api/meta").json()["profile"]
    assert meta["id"] == "fle" and meta["region_label"] == "Europe et Canada"
    assert client.get("/api/settings").json()["search_location"] == ""
    assert client.get("/favicon.svg").content == fle_favicon()
    stats = client.get("/api/stats").json()
    assert {t["topic"] for t in stats["topics"]} == {"FLE", "Enseignement du français"}


def fle_favicon() -> bytes:
    with open(FLE.favicon, "rb") as handle:
        return handle.read()


def test_topics_recomputed_when_profile_changes(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1", "Stage FLE"), offer("2", "Stage Data Scientist")])
        assert db.list_offers(conn, ai_only=True)[1] == 1  # profil IA : seul le data scientist
    profiles.use(FLE)
    try:
        db.init_db()
        with db.get_conn() as conn:
            items, total = db.list_offers(conn, ai_only=True)
        assert total == 1 and items[0]["title"] == "Stage FLE" and items[0]["ai_topics"] == ["FLE"]
    finally:
        profiles.use(None)


def test_unknown_profile_module():
    with pytest.raises(ModuleNotFoundError):
        profiles.load("profil_inexistant")
    assert profiles.load("") is profiles.AI_PROFILE and profiles.load("fle") is FLE
