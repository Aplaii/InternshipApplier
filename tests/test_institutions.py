"""Annuaire des établissements FLE et recherche sur plusieurs lieux."""

from __future__ import annotations

from urllib.parse import urlsplit

from app import config, fle_filter, institutions
from app.scrapers import SEARCHERS, ScrapedOffer


def test_directory_file_is_well_formed():
    items = institutions.items()
    assert len(items) >= 40
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    for inst in items:
        assert inst["name"] and inst["country"] and inst["kind"]
        assert urlsplit(inst["website"]).scheme in ("http", "https"), inst["id"]
        assert inst["email"] == "" or "@" in inst["email"]
        assert isinstance(inst["verified"], bool)
    countries = {i["country"] for i in items}
    assert {"Suède", "Norvège", "Danemark", "Finlande", "Islande", "Canada"} <= countries


def test_directory_offers_are_fle():
    for inst in institutions.items():
        offer = institutions.to_offer(inst)
        assert fle_filter.is_fle_offer(offer.title, institutions.description_for(inst))


def test_list_and_import_institutions(client):
    data = client.get("/api/institutions").json()
    assert data["updated"] and all(i["offer_id"] is None for i in data["items"])

    payload = {"ids": ["dk-af-copenhague", "ca-af-ottawa", "dk-af-copenhague", "inconnu"]}
    result = client.post("/api/institutions/import", json=payload).json()
    assert result["imported"] == 2 and result["skipped"] == 0 and result["unknown"] == ["inconnu"]
    # Un second import ne crée pas de doublon.
    again = client.post("/api/institutions/import", json={"ids": ["ca-af-ottawa"]}).json()
    assert again["imported"] == 0 and again["skipped"] == 1

    listing = client.get("/api/offers", params={"source": "directory"}).json()
    assert listing["total"] == 2
    by_company = {o["company"]: o for o in listing["items"]}
    dk = by_company["Alliance française de Copenhague"]
    assert dk["source_label"] == "Annuaire FLE" and dk["title"] == institutions.OFFER_TITLE
    assert dk["location"] == "Copenhague, Danemark" and not dk["is_read"]
    detail = client.get(f"/api/offers/{dk['id']}").json()
    assert detail["draft_to"] == "kontakt@alliancefrancaise.dk"
    assert detail["contact_emails"] == ["kontakt@alliancefrancaise.dk"]
    assert "Candidature spontanée" in detail["description"]
    # Visible même avec le filtre FLE, et marqué importé dans l'annuaire.
    assert client.get("/api/counts").json()["by_source"]["directory"]["total"] == 2
    items = {i["id"]: i for i in client.get("/api/institutions").json()["items"]}
    assert items["ca-af-ottawa"]["offer_id"] is not None

    assert client.post("/api/institutions/import", json={"ids": []}).status_code == 422


def test_search_runs_once_per_location_and_query(client, monkeypatch, no_sleep):
    seen = []

    async def search(_client, params):
        seen.append((params.location, params.keywords, params.max_results))
        return [ScrapedOffer(source="linkedin", source_id=f"{params.location}-{params.keywords}",
                             url="https://www.linkedin.com/jobs/view/1/", title="French Teacher Intern")]

    monkeypatch.setitem(SEARCHERS, "linkedin", search)
    result = client.post("/api/fetch", json={
        "sources": ["linkedin"], "keywords": "FLE", "location": "Sweden; Canada",
    }).json()
    # « Offres max. par site » (60 par défaut) est réparti entre les deux lieux.
    assert seen == [("Sweden", "FLE", 30), ("Canada", "FLE", 30)]
    assert result["new_total"] == 2
    assert client.get("/api/settings").json()["search_location"] == "Sweden; Canada"
    assert config.DEFAULT_SETTINGS["search_sources"] == list(config.SEARCH_SOURCES)

    # Sans mot-clé : une seule requête LinkedIn par lieu.
    seen.clear()
    client.post("/api/fetch", json={"sources": ["linkedin"], "location": "Sweden; Norway; Canada"})
    assert [(loc, kw) for loc, kw, _ in seen] == [(loc, fle_filter.LINKEDIN_QUERY) for loc in ("Sweden", "Norway", "Canada")]
    assert {n for *_, n in seen} == {20}


def test_french_boards_search_france_for_foreign_places():
    assert fle_filter.locations_for("hellowork", "Sweden; Norway; Canada") == ["France"]
    assert fle_filter.locations_for("wttj", "Sweden; Lyon") == ["Lyon"]
    assert fle_filter.locations_for("linkedin", "Sweden; Lyon") == ["Sweden", "Lyon"]
    assert fle_filter.locations_for("hellowork", "") == ["France"]
    assert fle_filter.locations_for("hellowork", "Montréal, QC") == ["France"]


def test_search_hellowork_in_france_when_location_is_abroad(client, monkeypatch):
    seen = []

    async def search(_client, params):
        seen.append(params.location)
        return []

    monkeypatch.setitem(SEARCHERS, "hellowork", search)
    client.post("/api/fetch", json={"sources": ["hellowork"], "keywords": "FLE", "location": "Sweden; Canada"})
    assert seen == ["France"]
