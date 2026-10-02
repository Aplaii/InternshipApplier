"""Analyse des pages des sites d'offres (pages réelles enregistrées dans tests/fixtures)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.scrapers import SearchParams, SourceError, hellowork, identify_url, linkedin, source_label_for_url, wttj
from app.scrapers.base import (
    clean, extract_emails, html_to_text, normalize_date, parse_generic_page, parse_relative_fr,
    request_with_retry,
)
from conftest import FIXTURES, fixture_text

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- HelloWork


def test_hellowork_search_page():
    offers = hellowork.parse_search_page(fixture_text("hellowork_search.html"), NOW)
    assert [o.source_id for o in offers] == ["83968273", "83952587", "83884926"]
    first = offers[0]
    assert first.source == "hellowork"
    assert first.url == "https://www.hellowork.com/fr-fr/emplois/83968273.html"
    assert first.title == "Stage Planificateur H/F"
    assert first.company == "Veolia Eau"
    assert first.location == "Le Plessis-Robinson - 92"
    assert first.published_at == "2026-10-01T12:00:00Z"  # « il y a 1 jour »
    assert offers[2].contract == "Stage · Télétravail occasionnel · 3 mois"
    assert all(o.logo_url.startswith("https://") for o in offers)


def test_hellowork_detail_jsonld_with_escaped_type_attribute():
    # La vraie page écrit type="application/ld&#x2B;json" : l'entité doit être décodée.
    details = parse_generic_page(fixture_text("hellowork_detail.html"))
    assert details.title == "Stage Ressources Humaines H/F"
    assert details.company == "APEF"
    assert details.location == "Montpellier"
    assert details.contract == "Stage"
    assert details.published_at == "2026-09-28T12:36:23Z"
    assert "Les missions du poste" in details.description
    assert "- Sélection des candidatures" in details.description
    # Le profil est déjà dans la description : pas de doublon. Les compétences sont ajoutées.
    assert "Profil recherché :" not in details.description
    assert details.description.endswith("Compétences :\nPro-activité, Garde d'enfants, Autonomie")


# --------------------------------------------------------------------------- LinkedIn


def test_linkedin_search_page():
    offers = linkedin.parse_search_page(fixture_text("linkedin_search.html"))
    assert [o.source_id for o in offers] == ["4459695243", "4463925244", "4426808078"]
    first = offers[0]
    assert first.url == "https://www.linkedin.com/jobs/view/4459695243/"
    assert first.title == "Stage Level Designer H/F (Projet narratif non annoncé)"
    assert first.company == "Asobo Studio"
    assert first.location == "Bordeaux"
    assert first.published_at == "2026-09-04T12:00:00Z"  # date seule -> midi UTC
    assert first.logo_url.startswith("https://media.licdn.com/")
    assert "&amp;" not in first.logo_url


def test_linkedin_detail_page():
    details = linkedin.parse_detail_page(fixture_text("linkedin_detail.html"))
    assert details.title == "Stage Level Designer H/F (Projet narratif non annoncé)"
    assert details.company == "Asobo Studio"
    assert details.location == "Bordeaux"
    assert details.description.startswith("C’est quoi Asobo Studio ?")
    assert details.description.endswith("Type d’emploi : Stage / Alternance\nSecteurs : Jeux vidéo")


def test_linkedin_job_id_from_url():
    assert linkedin.job_id_from_url("https://fr.linkedin.com/jobs/view/stage-x-at-y-4459695243?position=1") == "4459695243"
    assert linkedin.job_id_from_url("https://www.linkedin.com/jobs/view/4459695243/") == "4459695243"
    assert linkedin.job_id_from_url("https://www.linkedin.com/jobs/search/?currentJobId=4459695243&geoId=1") == "4459695243"
    assert linkedin.job_id_from_url("https://www.linkedin.com/company/asobo-studio") is None


# --------------------------------------------------------------------------- Welcome to the Jungle


def test_wttj_hits():
    hits = json.loads(fixture_text("wttj_search.json"))["hits"]
    offers = [wttj.parse_hit(hit) for hit in hits]
    first = offers[0]
    assert first.source_id == "yuri-neil/stage-builder-ia-agentique-h-f_levallois-perret_YN_pz3DLqr"
    assert first.url == "https://www.welcometothejungle.com/fr/companies/yuri-neil/jobs/stage-builder-ia-agentique-h-f_levallois-perret_YN_pz3DLqr"
    assert first.title == "Stage Builder IA Agentique (H/F)"
    assert first.company == "YURI & NEIL"
    assert first.location == "Levallois-Perret"
    assert first.contract == "Stage · Télétravail partiel"
    assert first.published_at == "2026-10-01T08:38:39Z"
    assert wttj.parse_hit({"name": "Sans slug"}) is None


def test_wttj_hit_duration_and_offices():
    hit = {
        "name": "Stage", "slug": "s", "organization": {"slug": "o", "name": "Org"},
        "offices": [{"city": c} for c in ("Paris", "Lyon", "Paris", "Lille", "Nantes")],
        "contract_duration_minimum": 4, "contract_duration_maximum": 6, "remote": "fulltime",
        "published_at_timestamp": 1790843919,
    }
    offer = wttj.parse_hit(hit)
    assert offer.location == "Paris, Lyon, Lille +1"
    assert offer.contract == "Stage · 4 à 6 mois · Télétravail total"
    assert offer.published_at == "2026-10-01T08:38:39Z"


def test_wttj_query():
    params = SearchParams(keywords="data", location="Lyon", recency="week", max_results=50)
    query = wttj.build_query(params, now_ts=1_800_000_000)
    assert query["query"] == "data Lyon"
    assert query["hitsPerPage"] == 50
    assert query["filters"] == (
        "contract_type:internship AND offices.country_code:FR AND published_at_timestamp > 1799395200"
    )
    france = wttj.build_query(SearchParams(location="France", recency="all"), now_ts=0)
    assert france["query"] == "" and "published_at_timestamp" not in france["filters"]


def test_wttj_detail_page_uses_structured_sections():
    details = wttj.parse_detail_page(fixture_text("wttj_detail.html"))
    assert details.title == "Stage Builder IA Agentique (H/F)"
    assert details.company == "YURI & NEIL"
    assert details.description.startswith("Descriptif du poste")
    assert "Profil recherché" in details.description
    # Puces consécutives sans ligne vide entre elles
    assert "• Stage de fin d’études\n• Démarrage dès que possible" in details.description


def test_wttj_source_id_from_url():
    url = "https://www.welcometothejungle.com/fr/companies/yuri-neil/jobs/stage-x_paris?q=a&o=1"
    assert wttj.source_id_from_url(url) == "yuri-neil/stage-x_paris"


# --------------------------------------------------------------------------- outils


def test_identify_url_and_labels():
    assert identify_url("https://www.hellowork.com/fr-fr/emplois/83844515.html?utm=x") == (
        "hellowork", "83844515", "https://www.hellowork.com/fr-fr/emplois/83844515.html")
    assert identify_url("https://fr.linkedin.com/jobs/view/abc-4459695243") == (
        "linkedin", "4459695243", "https://www.linkedin.com/jobs/view/4459695243/")
    source, source_id, url = identify_url("HTTPS://FR.Indeed.com/viewjob?jk=abc#top")
    assert source == "manual" and url == "https://fr.indeed.com/viewjob?jk=abc" and source_id == url
    assert source_label_for_url("https://fr.indeed.com/viewjob?jk=1") == "Indeed"
    assert source_label_for_url("https://careers.airbus.com/job/1") == "Airbus"
    assert source_label_for_url("not a url") == "Manuel"


@pytest.mark.parametrize("text, expected", [
    ("il y a 3 jours", "2026-09-29T12:00:00Z"),
    ("il y a 20 heures", "2026-10-01T16:00:00Z"),
    ("il y a 45 minutes", "2026-10-02T11:15:00Z"),
    ("il y a 2 semaines", "2026-09-18T12:00:00Z"),
    ("il y a 1 mois", "2026-09-02T12:00:00Z"),
    ("il y a 30+ jours", "2026-09-02T12:00:00Z"),
    ("Aujourd’hui", "2026-10-02T12:00:00Z"),
    ("hier", "2026-10-01T12:00:00Z"),
    ("avant-hier", "2026-09-30T12:00:00Z"),
    ("bientôt", None),
    ("", None),
])
def test_parse_relative_fr(text, expected):
    assert parse_relative_fr(text, NOW) == expected


@pytest.mark.parametrize("value, expected", [
    ("2026-09-04", "2026-09-04T12:00:00Z"),
    ("2026-10-01T08:38:39Z", "2026-10-01T08:38:39Z"),
    ("2026-10-01T10:38:39+02:00", "2026-10-01T08:38:39Z"),
    ("2026-12-30T08:38:39.000Z", "2026-12-30T08:38:39Z"),
    (1790843919, "2026-10-01T08:38:39Z"),
    ("2026-13-45", None),
    ("n'importe quoi", None),
    (None, None),
    (True, None),
])
def test_normalize_date(value, expected):
    assert normalize_date(value) == expected


def test_html_to_text():
    html = (
        "<h2>Missions</h2><p>Analyser   les\n données.<br/>Construire des tableaux.</p>"
        "<ul><li>Python</li><li><p>SQL</p></li></ul><p>Fin &amp; contact</p>"
        "<!-- commentaire --><script>alert(1)</script>"
    )
    assert html_to_text(html) == (
        "Missions\n\nAnalyser les données.\nConstruire des tableaux.\n\n• Python\n• SQL\n\nFin & contact"
    )
    assert html_to_text("&lt;p&gt;Échappé deux fois&lt;/p&gt;") == "Échappé deux fois"
    assert html_to_text("") == "" and html_to_text(None) == ""


def test_extract_emails():
    text = (
        "Envoyez votre CV à RH@Entreprise.fr. Contact : jobs@entreprise.fr, "
        "noreply@entreprise.fr, aide@hellowork.com, logo@2x.png, rh@entreprise.fr"
    )
    assert extract_emails(text) == ["rh@entreprise.fr", "jobs@entreprise.fr"]


def test_clean():
    assert clean("  a \n\t b c  ") == "a b c"
    assert clean(None) == ""


# --------------------------------------------------------------------------- recherches (HTTP simulé)


def run(coro):
    return asyncio.run(coro)


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_hellowork_search_pagination_and_params(no_sleep):
    page_html = fixture_text("hellowork_search.html")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        page = request.url.params.get("p", "1")
        return httpx.Response(200, text=page_html if page == "1" else "<html><body><ul></ul></body></html>")

    async def go():
        async with mock_client(handler) as client:
            return await hellowork.search(client, SearchParams(keywords="data", location="Lyon", recency="month", max_results=60))

    offers = run(go())
    assert len(offers) == 3
    assert seen[0] == {"k": "data", "c": "Stage", "d": "m", "st": "date", "l": "Lyon"}
    assert seen[1]["p"] == "2" and len(seen) == 2  # page vide -> arrêt


def test_hellowork_search_error_keeps_partial_results(no_sleep):
    page_html = fixture_text("hellowork_search.html")

    def handler(request):
        return httpx.Response(200, text=page_html) if "p" not in request.url.params else httpx.Response(403)

    async def go():
        async with mock_client(handler) as client:
            await hellowork.search(client, SearchParams(location="France", max_results=100))

    with pytest.raises(SourceError) as info:
        run(go())
    assert "HTTP 403" in info.value.message
    assert len(info.value.partial) == 3


def test_linkedin_search_rate_limited(no_sleep):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    async def go():
        async with mock_client(handler) as client:
            await linkedin.search(client, SearchParams(keywords="data"))

    with pytest.raises(SourceError) as info:
        run(go())
    assert "429" in info.value.message
    assert len(calls) == 3  # 1 essai + 2 nouvelles tentatives


def test_linkedin_search_stops_when_no_new_offers(no_sleep):
    page_html = fixture_text("linkedin_search.html")
    starts = []

    def handler(request):
        starts.append(request.url.params["start"])
        assert request.url.params["f_JT"] == "I" and request.url.params["location"] == "France"
        return httpx.Response(200, text=page_html)  # renvoie toujours les mêmes offres

    async def go():
        async with mock_client(handler) as client:
            return await linkedin.search(client, SearchParams(max_results=60, recency="day"))

    offers = run(go())
    assert len(offers) == 3
    assert starts == ["0", "3"]


def test_wttj_search_rejected_key():
    async def go():
        async with mock_client(lambda request: httpx.Response(403, json={"message": "Invalid API key"})) as client:
            await wttj.search(client, SearchParams())

    with pytest.raises(SourceError) as info:
        run(go())
    assert "WTTJ_ALGOLIA_API_KEY" in info.value.message


def test_wttj_search_parses_hits():
    payload = json.loads(fixture_text("wttj_search.json"))
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=payload)

    async def go():
        async with mock_client(handler) as client:
            return await wttj.search(client, SearchParams(keywords="ia", max_results=2))

    offers = run(go())
    assert len(offers) == 2
    assert captured["headers"]["x-algolia-application-id"] == "CSEKHVMS53"
    assert captured["headers"]["referer"] == "https://www.welcometothejungle.com/"
    assert captured["body"]["query"] == "ia"


def test_request_with_retry_network_error(no_sleep):
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    async def go():
        async with mock_client(handler) as client:
            await request_with_retry(client, "GET", "https://exemple.fr/offre", retries=1)

    with pytest.raises(SourceError) as info:
        run(go())
    assert info.value.message == "exemple.fr : connexion impossible (réseau indisponible ?)"


def test_fixtures_are_small():
    # Les pages enregistrées sont réduites aux parties utiles.
    assert all(path.stat().st_size < 64_000 for path in FIXTURES.iterdir())
