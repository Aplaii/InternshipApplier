"""Plateformes nationales et européennes : EURES, Platsbanken, Arbeidsplassen, Job Bank."""

from __future__ import annotations

import httpx
import pytest

from app import automation, fle_filter, institutions
from app.scrapers import SEARCHERS, ScrapedOffer, SearchParams, eures, jobbank, jobtech, nav

EURES_PAGE = {
    "numberRecords": 2,
    "jvs": [
        {"id": "ABC123", "title": "Praktikant – franskundervisning", "description": "<p>Stage FLE à Stockholm.</p><p>Contact : rh@skola.se</p>",
         "creationDate": 1794643800575, "locationMap": {"SE": ["SE110"]}, "positionOfferingCode": "temporary",
         "employer": {"name": "Franska Skolan"}},
        {"id": "DEF456", "title": "Sales Manager", "description": "Permanent role.", "creationDate": 1794643800575,
         "locationMap": {"NO": [None]}, "positionOfferingCode": "directhire", "employer": {"name": "ACME"}},
    ],
}

JOBTECH_PAGE = {
    "total": {"value": 1},
    "hits": [{
        "id": "31735548", "headline": "Praktik inom franskundervisning", "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/31735548",
        "logo_url": "", "publication_date": "2026-11-10T08:00:00", "description": {"text": "Vi söker en praktikant i franska."},
        "employment_type": {"label": "Praktik"}, "duration": {"label": "3 – 6 månader"},
        "employer": {"name": "Lycée Français"}, "workplace_address": {"municipality": "Stockholm"},
    }],
}

NAV_PAGE = {"hits": {"total": {"value": 1}, "hits": [{"_source": {
    "uuid": "c982e296", "title": "Praktikant fransk", "businessName": "Alliance française Bergen",
    "published": "2026-11-10T00:00:00+01:00", "engagementtype": "Trainee",
    "locationList": [{"city": "BERGEN"}], "generatedSearchMetadata": {"shortSummary": "Stage FLE de 6 mois."},
}}]}}

JOBBANK_HTML = """<html><body>
<article class="action-buttons" id="article-50704223">
 <a class="resultJobItem" href="/jobsearch/jobposting/50704223?source=searchresults">
  <h3 class="title"><span class="flag"><span class="new">New</span><span class="jobstudentflag">Student job</span></span>
   <span class="noctitle">stagiaire en enseignement du français</span></h3>
  <ul><li class="date">November 10, 2026</li><li class="business">Alliance française d'Ottawa</li>
   <li class="location"><span class="wb-inv">Location</span>Ottawa (ON)</li></ul>
 </a>
</article>
<article id="article-x"><span class="noctitle">ignoré</span></article>
</body></html>"""


def test_parse_eures():
    offers, total = eures.parse_search(EURES_PAGE)
    assert total == 2 and len(offers) == 2
    first = offers[0]
    assert first.source == "eures" and first.source_id == "ABC123"
    assert first.url == "https://europa.eu/eures/portal/jv-se/jv-details/ABC123?lang=fr"
    assert first.company == "Franska Skolan" and first.location == "Suède" and first.contract == "Contrat temporaire"
    assert first.published_at.startswith("2026-") and "Stage FLE à Stockholm." in first.description
    assert fle_filter.is_internship(first.title, first.contract, first.description)
    assert not fle_filter.is_internship(offers[1].title, offers[1].contract, offers[1].description)


def test_eures_request_body():
    body = eures.search_body(SearchParams(keywords="intern french", location="se,no", recency="month"), 2)
    assert body["locationCodes"] == ["se", "no"] and body["page"] == 2
    assert body["keywords"] == [{"keyword": "intern", "specificSearchCode": "EVERYWHERE"},
                                {"keyword": "french", "specificSearchCode": "EVERYWHERE"}]
    assert body["publicationPeriod"] == "LAST_MONTH"


def test_parse_jobtech():
    offer = jobtech.parse_hit(JOBTECH_PAGE["hits"][0])
    assert offer.source == "jobtech" and offer.location == "Stockholm, Suède"
    assert offer.contract == "Praktik · 3 – 6 månader" and offer.description == "Vi söker en praktikant i franska."
    assert offer.published_at == "2026-11-10T08:00:00Z"


def test_parse_nav():
    offer = nav.parse_hit(NAV_PAGE["hits"]["hits"][0])
    assert offer.url == "https://arbeidsplassen.nav.no/stillinger/stilling/c982e296"
    assert offer.location == "Bergen, Norvège" and offer.contract == "Trainee"
    assert offer.description == "Stage FLE de 6 mois."


def test_parse_jobbank():
    [offer] = jobbank.parse_search_page(JOBBANK_HTML)
    assert offer.source_id == "50704223" and offer.title == "stagiaire en enseignement du français"
    assert offer.company == "Alliance française d'Ottawa" and offer.location == "Ottawa (ON), Canada"
    assert offer.contract == "Student job" and offer.published_at == "2026-11-10T12:00:00Z"


@pytest.mark.anyio
async def test_scrapers_against_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "europa.eu":
            return httpx.Response(200, json=EURES_PAGE)
        if host == "jobsearch.api.jobtechdev.se":
            assert request.url.params["q"] == "praktik franska" and "published-after" in request.url.params
            return httpx.Response(200, json=JOBTECH_PAGE)
        if host == "arbeidsplassen.nav.no":
            assert request.url.params["published"] == "now/d-30d"
            return httpx.Response(200, json=NAV_PAGE)
        if host == "www.jobbank.gc.ca":
            return httpx.Response(200, text=JOBBANK_HTML if request.url.params["page"] == "1" else "<html></html>")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        params = SearchParams(keywords="praktik franska", recency="month", max_results=20)
        assert len(await eures.search(client, SearchParams(keywords="intern", location="se", max_results=20))) == 2
        assert await eures.search(client, SearchParams(keywords="intern", location="")) == []
        assert [o.source_id for o in await jobtech.search(client, params)] == ["31735548"]
        assert [o.source_id for o in await nav.search(client, params)] == ["c982e296"]
        assert [o.source_id for o in await jobbank.search(client, SearchParams(keywords="stagiaire", recency="all"))] == ["50704223"]


def test_locations_for_platforms():
    places = "Sweden; Norway; Denmark; Finland; Iceland; Estonia; Netherlands; Ireland; Canada"
    assert fle_filter.locations_for("eures", places) == ["se,no,dk,fi,is,ee,nl,ie"]
    assert fle_filter.locations_for("jobtech", places) == ["Sverige"]
    assert fle_filter.locations_for("nav", places) == ["Norge"]
    assert fle_filter.locations_for("jobbank", places) == ["Canada"]
    assert fle_filter.locations_for("jobtech", "Canada; Dublin") == []
    assert fle_filter.locations_for("eures", "Canada") == []
    assert fle_filter.locations_for("eures", "Nordics") == ["se,no,dk,fi,is"]
    assert fle_filter.locations_for("jobbank", "Montréal, QC") == ["Canada"]
    assert fle_filter.locations_for("nav", "") == ["Norge"]


def test_search_skips_platforms_without_matching_country(client, monkeypatch):
    calls = []

    async def search(_client, params):
        calls.append(params.location)
        return [ScrapedOffer(source="jobtech", source_id="1", url="https://x/1", title="Praktikant fransklärare", description="FLE")]

    monkeypatch.setitem(SEARCHERS, "jobtech", search)
    result = client.post("/api/fetch", json={"sources": ["jobtech"], "keywords": "FLE", "location": "Canada"}).json()
    assert calls == [] and result["results"][0]["skipped"] is True
    result = client.post("/api/fetch", json={"sources": ["jobtech"], "keywords": "FLE", "location": "Sweden"}).json()
    assert calls == ["Sverige"] and result["results"][0]["new"] == 1
    # La description fournie par la recherche est enregistrée (pas de lecture supplémentaire).
    item = client.get("/api/offers").json()["items"][0]
    detail = client.get(f"/api/offers/{item['id']}").json()
    assert detail["description"] == "FLE" and detail["details_fetched_at"]


def test_platform_queries_and_directory():
    for source in ("eures", "jobtech", "nav", "jobbank"):
        assert automation.queries_for("", fle_only=True, source=source) == list(fle_filter.SOURCE_QUERIES[source])
    data = institutions.platforms()
    ids = [p["id"] for p in data["platforms"]]
    assert len(ids) == len(set(ids)) >= 25
    integrated = {p["source"] for p in data["platforms"] if p["integrated"]}
    assert integrated == set(SEARCHERS)


def test_platforms_endpoint(client):
    data = client.get("/api/platforms").json()
    assert any(p["id"] == "finn" and not p["integrated"] for p in data["platforms"])


@pytest.fixture
def anyio_backend():
    return "asyncio"
