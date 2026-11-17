"""Stages contre offres d'emploi, traduction en français, candidatures spontanées."""

from __future__ import annotations

import pytest

from app import db, fle_filter, translate
from app.scrapers import SEARCHERS, ScrapedOffer
from app.scrapers.linkedin import employment_type
from conftest import MockLLM, sse_chunks


def offer(source_id: str, title: str, source: str = "linkedin", contract: str = "") -> ScrapedOffer:
    return ScrapedOffer(source=source, source_id=source_id, url=f"https://example.org/{source_id}",
                        title=title, company="ACME", contract=contract)


# --------------------------------------------------------------------------- stage ou emploi


@pytest.mark.parametrize("title, contract", [
    ("Stage - Animation Atelier FLE H/F", "Stage · 2 mois"),
    ("French Teacher Intern", ""),
    ("Stagiaire FLE – Alliance française", ""),
    ("Praktikant fransklärare", ""),
    ("Ranskan opettaja harjoittelija", ""),
    ("Stagiair docent Frans", ""),
    ("French tutor (Co-op, Summer 2027)", ""),
    ("Enseignant de français", "Stage"),
])
def test_detects_internships(title, contract):
    assert fle_filter.is_internship(title, contract)


@pytest.mark.parametrize("title", [
    "Docent Frans eerstegraads", "Lärare i franska och engelska", "French Teacher",
    "LTO - Grade 5/6 French Immersion (1.00 FTE)", "Stage manager (French speaking)",
    "French teacher for an early-stage startup", "Internal communications – French",
])
def test_rejects_job_offers(title):
    assert not fle_filter.is_internship(title, "")


def test_internship_from_description():
    assert fle_filter.is_internship("French Teacher", "", "Type d’emploi : Stage\nSecteurs : Enseignement")
    assert fle_filter.is_internship("Assistant pédagogique", "", "Stage de 6 mois, convention de stage obligatoire.")
    assert not fle_filter.is_internship("French Teacher", "", "Permanent position, full time.")
    # LinkedIn classe parfois un remplacement en « Stage » : le titre d'emploi l'emporte.
    linkedin_label = "Niveau hiérarchique : Non pertinent\nType d’emploi : Stage / Alternance"
    assert not fle_filter.is_internship("0.33 FTE - French Long Term Occasional Teacher", "", linkedin_label)
    assert not fle_filter.is_internship("Lärare i franska, tillsvidare", "", linkedin_label)
    assert fle_filter.is_internship("French Teacher Intern (part-time)", "")


def test_linkedin_employment_type():
    assert employment_type(["Niveau hiérarchique : Stage", "Type d’emploi : Stage"]) == "Stage"
    assert employment_type(["Employment type : Full-time"]) == "Full-time"
    assert employment_type([]) == ""
    assert " AND (internship OR intern" in fle_filter.LINKEDIN_QUERY


def test_search_drops_job_offers(client, monkeypatch):
    async def search(_client, params):
        return [offer("1", "French Teacher Intern"), offer("2", "Docent Frans"), offer("3", "French Teacher")]

    monkeypatch.setitem(SEARCHERS, "linkedin", search)
    result = client.post("/api/fetch", json={"sources": ["linkedin"], "location": "Netherlands"}).json()
    assert result["results"][0]["found"] == 3 and result["results"][0]["kept"] == 1
    assert [o["title"] for o in client.get("/api/offers").json()["items"]] == ["French Teacher Intern"]


def test_job_offer_hidden_until_description_says_internship(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1", "French Teacher")])
        offer_id = db.find_offer_id(conn, "linkedin", "1")
        assert db.list_offers(conn, fle_only=True)[1] == 0
        db.set_details(conn, offer_id, "Cours de FLE.\nType d'emploi : Stage", [])
        assert db.get_offer(conn, offer_id)["is_internship"] is True
        assert db.list_offers(conn, fle_only=True)[1] == 1


# --------------------------------------------------------------------------- traduction


def test_looks_french():
    assert translate.looks_french("Stage FLE – Alliance française H/F")
    assert translate.looks_french("Professeur des écoles")
    assert not translate.looks_french("Docent Frans eerstegraads")
    assert not translate.looks_french("Lärare i franska och engelska")
    assert not translate.looks_french("French Teacher Intern")


def test_parse_numbered():
    text = "Voici :\n1. Enseignant de français\n2) **Stagiaire FLE**\n4. hors liste"
    assert translate.parse_numbered(text, 3) == ["Enseignant de français", "Stagiaire FLE", None]


def test_french_titles_stored_without_model(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1", "Stage FLE H/F"), offer("2", "Praktikant fransklärare")])
        get = lambda sid: db.get_offer(conn, db.find_offer_id(conn, "linkedin", sid))
        assert get("1")["title_fr"] == "Stage FLE H/F"
        assert get("2")["title_fr"] is None
        assert db.count_untranslated(conn) == 1
        db.set_translation(conn, get("2")["id"], title_fr="Stagiaire professeur de français")
        # La traduction survit à une nouvelle recherche, pas à un changement de titre.
        db.upsert_offers(conn, [offer("2", "Praktikant fransklärare")])
        assert get("2")["title_fr"] == "Stagiaire professeur de français"
        db.upsert_offers(conn, [offer("2", "Praktikant fransklärare (vår 2027)")])
        assert get("2")["title_fr"] is None


def test_translate_titles_endpoint(client):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1", "Praktikant fransklärare"), offer("2", "French tutor intern")])
    reply = "1. Stagiaire professeur de français\n2. Stagiaire tuteur de français"
    with MockLLM([("sse", sse_chunks(reply))]) as mock:
        client.put("/api/settings", json={"llm_base_url": mock.url})
        result = client.post("/api/translate/titles").json()
        prompt = mock.requests[0]["messages"][1]["content"]
    assert result == {"translated": 2, "error": None, "remaining": 0}
    assert "1. French tutor intern" in prompt or "1. Praktikant fransklärare" in prompt
    titles = {o["title"]: o["title_fr"] for o in client.get("/api/offers").json()["items"]}
    assert set(titles.values()) == {"Stagiaire professeur de français", "Stagiaire tuteur de français"}


def test_translate_offer_and_llm_down(client):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1", "Praktikant fransklärare")])
        offer_id = db.find_offer_id(conn, "linkedin", "1")
        db.set_details(conn, offer_id, "Vi söker en praktikant som undervisar i franska.", [])
    client.put("/api/settings", json={"llm_base_url": "http://127.0.0.1:9"})
    response = client.post(f"/api/offers/{offer_id}/translate")
    assert response.status_code == 503 and "llama.cpp" in response.json()["detail"]

    with MockLLM([("sse", sse_chunks("1. Stagiaire professeur de français")),
                  ("sse", sse_chunks("Nous cherchons un stagiaire qui enseigne le français."))]) as mock:
        client.put("/api/settings", json={"llm_base_url": mock.url})
        data = client.post(f"/api/offers/{offer_id}/translate").json()
    assert data["title_fr"] == "Stagiaire professeur de français"
    assert data["description_fr"] == "Nous cherchons un stagiaire qui enseigne le français."
    # Une nouvelle description efface l'ancienne traduction.
    with db.get_conn() as conn:
        db.set_details(conn, offer_id, "Ny text.", [])
        assert db.get_offer(conn, offer_id)["description_fr"] is None


# --------------------------------------------------------------------------- candidatures spontanées


def test_create_and_track_spontaneous(client):
    payload = {"company": "Alliance française de Tromsø", "email": "post@af-tromso.no",
               "website": "https://af-tromso.no", "location": "Tromsø, Norvège", "notes": "Cours du soir."}
    created = client.post("/api/spontaneous", json=payload)
    assert created.status_code == 201
    data = created.json()
    assert data["is_spontaneous"] is True and data["draft_to"] == "post@af-tromso.no"
    assert data["contract"] == "Candidature spontanée" and "Cours du soir." in data["description"]
    assert client.post("/api/spontaneous", json=payload).status_code == 409
    assert client.post("/api/spontaneous", json={**payload, "email": "pas-un-email", "website": ""}).status_code == 422
    assert client.post("/api/spontaneous", json={"company": "École test", "email": "test@example.org"}).status_code == 201

    # Une offre trouvée en ligne peut aussi devenir une candidature spontanée.
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("9", "Stage FLE", source="hellowork", contract="Stage")])
        other = db.find_offer_id(conn, "hellowork", "9")
    assert client.patch(f"/api/offers/{other}", json={"is_spontaneous": True}).json()["is_spontaneous"] is True

    listing = client.get("/api/offers", params={"folder": "spontaneous"}).json()
    assert listing["total"] == 3
    assert client.get("/api/counts").json()["spontaneous"] == 3

    client.patch(f"/api/offers/{data['id']}", json={"status": "sent"})
    kind = client.get("/api/stats").json()["by_kind"]
    assert kind["spontaneous"]["total"] == 3 and kind["spontaneous"]["applied"] == 1
    assert kind["offers"]["total"] == 0


def test_directory_imports_are_spontaneous(client):
    client.post("/api/institutions/import", json={"ids": ["ca-af-ottawa"]})
    [item] = client.get("/api/offers", params={"folder": "spontaneous"}).json()["items"]
    assert item["company"] == "Alliance française d'Ottawa" and item["is_spontaneous"] is True
