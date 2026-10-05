"""API HTTP : sécurité, paramètres, offres, recherche, documents, génération et envoi."""

from __future__ import annotations

import email
import email.policy
import socket

import pytest
from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient

from app import db, main
from app.scrapers import SEARCHERS, OfferDetails, ScrapedOffer, SourceError
from conftest import FIXTURES, MockLLM, read_ndjson, sse_chunks


def add_offers(*offers: ScrapedOffer) -> list[int]:
    with db.get_conn() as conn:
        db.upsert_offers(conn, offers)
        return [db.find_offer_id(conn, o.source, o.source_id) for o in offers]


def sample(source_id="1", **kwargs) -> ScrapedOffer:
    data = {"source": "hellowork", "source_id": source_id, "url": f"https://www.hellowork.com/fr-fr/emplois/{source_id}.html",
            "title": "Stage Data Scientist IA H/F", "company": "ACME", "location": "Lyon", "contract": "Stage · 6 mois",
            "published_at": "2026-09-30T10:00:00Z"}
    data.update(kwargs)
    return ScrapedOffer(**data)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------- sécurité


def test_index_and_static(client):
    page = client.get("/")
    assert page.status_code == 200 and "Chercher des offres" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/favicon.ico").headers["content-type"].startswith("image/svg+xml")


def test_mutations_require_app_header(client):
    response = client.put("/api/settings", json={"full_name": "X"}, headers={"X-Requested-With": ""})
    assert response.status_code == 403
    assert client.get("/api/offers", headers={"X-Requested-With": ""}).status_code == 200  # lecture libre


def test_unknown_host_rejected(data_dir):
    with TestClient(main.create_app(), base_url="http://evil.example") as other:
        assert other.get("/api/meta").status_code == 400


# --------------------------------------------------------------------------- paramètres


def test_settings_secrets_are_never_returned(client):
    data = client.put("/api/settings", json={"smtp_password": "secret", "llm_api_key": "k", "full_name": " Clément "}).json()
    assert "smtp_password" not in data and "llm_api_key" not in data
    assert data["has_smtp_password"] is True and data["has_llm_api_key"] is True
    assert data["full_name"] == "Clément"
    assert data["effective_signature"] == "Clément"
    # Champ absent : le mot de passe est conservé ; chaîne vide : il est effacé.
    assert client.put("/api/settings", json={"phone": "06"}).json()["has_smtp_password"] is True
    assert client.put("/api/settings", json={"smtp_password": ""}).json()["has_smtp_password"] is False


def test_settings_validation(client):
    assert client.put("/api/settings", json={"email": "pas-un-email"}).status_code == 422
    assert client.put("/api/settings", json={"champ_inconnu": 1}).status_code == 422
    assert client.put("/api/settings", json={"llm_max_tokens": 10}).status_code == 422
    assert client.put("/api/settings", json={"smtp_security": "tls"}).status_code == 422
    assert client.put("/api/settings", json={"search_sources": ["indeed"]}).status_code == 422


# --------------------------------------------------------------------------- offres


def test_list_patch_bulk_delete(client):
    first, second = add_offers(sample("1"), sample("2", source="linkedin", url="https://www.linkedin.com/jobs/view/2/"))
    listing = client.get("/api/offers").json()
    assert listing["total"] == 2
    assert {item["source_label"] for item in listing["items"]} == {"HelloWork", "LinkedIn"}

    patched = client.patch(f"/api/offers/{first}", json={"status": "interview", "notes": "RDV mardi"}).json()
    assert patched["status"] == "interview" and patched["notes"] == "RDV mardi"
    assert client.patch(f"/api/offers/{first}", json={"status": "inconnu"}).status_code == 422
    assert client.patch(f"/api/offers/{first}", json={"title": "x"}).status_code == 422
    assert client.patch("/api/offers/999", json={"is_read": True}).status_code == 404

    assert client.post("/api/offers/bulk", json={"ids": [first, second], "is_archived": True}).json() == {"updated": 2}
    assert client.post("/api/offers/bulk", json={"ids": [first]}).status_code == 422
    assert client.get("/api/counts").json()["archived"] == 2

    assert client.delete(f"/api/offers/{second}").json() == {"ok": True}
    assert client.delete(f"/api/offers/{second}").status_code == 404


def test_manual_description_updates_contact_emails(client):
    (offer_id,) = add_offers(sample("1"))
    data = client.patch(f"/api/offers/{offer_id}", json={"description": " CV à jobs@acme.fr "}).json()
    assert data["description"] == "CV à jobs@acme.fr"
    assert data["contact_emails"] == ["jobs@acme.fr"]
    assert data["details_fetched_at"]


def test_create_manual_offer(client):
    payload = {"url": "https://fr.indeed.com/viewjob?jk=1", "title": "Stage BI", "company": "Beta",
               "description": "Écrire à rh@beta.fr"}
    created = client.post("/api/offers", json=payload)
    assert created.status_code == 201
    data = created.json()
    assert data["source"] == "manual" and data["source_label"] == "Indeed"
    assert data["contact_emails"] == ["rh@beta.fr"] and data["is_read"] is True
    duplicate = client.post("/api/offers", json=payload)
    assert duplicate.status_code == 409 and duplicate.json()["detail"]["id"] == data["id"]
    assert client.post("/api/offers", json={**payload, "url": "javascript:alert(1)"}).status_code == 422
    # Un lien LinkedIn est reconnu : pas de doublon avec la recherche automatique.
    linkedin = client.post("/api/offers", json={"url": "https://fr.linkedin.com/jobs/view/stage-x-4459695243?trk=a", "title": "T"}).json()
    assert (linkedin["source"], linkedin["source_id"]) == ("linkedin", "4459695243")


def test_details_fetched_once(client, monkeypatch):
    (offer_id,) = add_offers(sample("1"))
    calls = []

    async def fake_fetch(_client, source, source_id, url):
        calls.append((source, source_id, url))
        return OfferDetails(description="Missions : SQL. Contact : rh@acme.fr", emails=["rh@acme.fr"])

    monkeypatch.setattr(main, "fetch_details", fake_fetch)
    data = client.post(f"/api/offers/{offer_id}/details").json()
    assert data["description"].startswith("Missions") and data["contact_emails"] == ["rh@acme.fr"]
    client.post(f"/api/offers/{offer_id}/details")
    assert len(calls) == 1
    client.post(f"/api/offers/{offer_id}/details?refresh=true")
    assert len(calls) == 2


def test_details_error(client, monkeypatch):
    (offer_id,) = add_offers(sample("1"))

    async def failing(*_args):
        raise SourceError("L'offre n'est plus en ligne (page introuvable).")

    monkeypatch.setattr(main, "fetch_details", failing)
    response = client.post(f"/api/offers/{offer_id}/details")
    assert response.status_code == 502 and "plus en ligne" in response.json()["detail"]


def test_import_offer(client, monkeypatch):
    async def fake_fetch(_client, source, source_id, url):
        assert (source, source_id) == ("hellowork", "123")
        return OfferDetails(title="Stage RH", company="APEF", location="Montpellier", contract="Stage", description="D")

    monkeypatch.setattr(main, "fetch_details", fake_fetch)
    data = client.post("/api/offers/import", json={"url": "https://www.hellowork.com/fr-fr/emplois/123.html"}).json()
    assert data["title"] == "Stage RH" and data["existing_id"] is None and data["error"] is None
    assert data["url"] == "https://www.hellowork.com/fr-fr/emplois/123.html"
    assert client.post("/api/offers/import", json={"url": "ftp://exemple.fr/x"}).status_code == 422


# --------------------------------------------------------------------------- recherche


def test_fetch_saves_offers_and_reports_errors(client, monkeypatch):
    async def hellowork(_client, params):
        assert params.keywords == "data" and params.max_results == 20
        return [sample("1"), sample("2")]

    async def linkedin(_client, params):
        raise SourceError("LinkedIn limite temporairement les requêtes (HTTP 429).", partial=[sample("9", source="linkedin")])

    async def wttj(_client, params):
        raise ValueError("structure inattendue")

    monkeypatch.setitem(SEARCHERS, "hellowork", hellowork)
    monkeypatch.setitem(SEARCHERS, "linkedin", linkedin)
    monkeypatch.setitem(SEARCHERS, "wttj", wttj)
    payload = {"keywords": " data ", "location": "Lyon", "sources": ["hellowork", "linkedin", "wttj"],
               "recency": "week", "max_per_source": 20}
    result = client.post("/api/fetch", json=payload).json()
    by_source = {r["source"]: r for r in result["results"]}
    assert by_source["hellowork"] == {"source": "hellowork", "label": "HelloWork", "found": 2, "kept": 2, "new": 2, "error": None}
    assert by_source["linkedin"]["new"] == 1 and "429" in by_source["linkedin"]["error"]
    assert by_source["wttj"]["found"] == 0 and "ValueError" in by_source["wttj"]["error"]
    assert result["new_total"] == 3
    assert client.post("/api/fetch", json=payload).json()["new_total"] == 0  # déjà connues
    settings = client.get("/api/settings").json()
    assert settings["search_keywords"] == "data" and settings["search_max_per_source"] == 20


def test_fetch_validation(client):
    assert client.post("/api/fetch", json={"sources": []}).status_code == 422
    assert client.post("/api/fetch", json={"sources": ["indeed"]}).status_code == 422
    assert client.post("/api/fetch", json={"sources": ["wttj"], "max_per_source": 5000}).status_code == 422


# --------------------------------------------------------------------------- documents


def test_documents(client):
    pdf = (FIXTURES / "cv_test.pdf").read_bytes()
    assert client.put("/api/documents/CV Clément.pdf", content=pdf).json() == {"name": "CV Clément.pdf", "size": len(pdf)}
    assert client.get("/api/documents").json() == [{"name": "CV Clément.pdf", "size": len(pdf)}]
    text = client.post("/api/documents/CV Clément.pdf/text").json()["text"]
    assert "scikit-learn" in text and "Clément Test" in text
    assert client.put("/api/documents/virus.exe", content=b"x").status_code == 422
    assert client.put("/api/documents/vide.pdf", content=b"").status_code == 422
    assert client.put("/api/documents/gros.pdf", content=b"x" * (10 * 1024 * 1024 + 1)).status_code == 413
    assert client.put("/api/documents/..%2F..%2Fpasswd.txt", content=b"x").status_code in (404, 422)
    assert client.post("/api/documents/absent.pdf/text").status_code == 422
    assert client.delete("/api/documents/CV Clément.pdf").json() == {"ok": True}
    assert client.delete("/api/documents/CV Clément.pdf").status_code == 404


def test_document_name_traversal_is_neutralised(client, data_dir):
    assert client.put("/api/documents/..\\..\\note.txt", content=b"hello").json()["name"] == "note.txt"
    assert (data_dir / "documents" / "note.txt").read_bytes() == b"hello"
    assert not (data_dir / "note.txt").exists()


# --------------------------------------------------------------------------- envoi


class Sink:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture
def smtp(client):
    sink = Sink()
    port = free_port()
    controller = Controller(sink, hostname="127.0.0.1", port=port)
    controller.start()
    client.put("/api/settings", json={
        "full_name": "Clément Test", "email": "clement@example.org", "smtp_host": "127.0.0.1",
        "smtp_port": port, "smtp_security": "none", "smtp_username": "",
    })
    yield sink
    controller.stop()


def test_send_application(client, smtp):
    (offer_id,) = add_offers(sample("1"))
    client.patch(f"/api/offers/{offer_id}", json={"draft_subject": "S", "draft_body": "B"})
    client.put("/api/documents/CV.pdf", content=b"%PDF-1.4 cv")
    response = client.post(f"/api/offers/{offer_id}/send", json={
        "to": "rh@acme.fr", "subject": "Candidature – Stage Data", "body": "Madame, Monsieur,\n\nBonjour.",
        "attachments": ["CV.pdf"],
    })
    assert response.status_code == 200, response.text
    offer = response.json()["offer"]
    assert offer["status"] == "sent" and offer["draft_body"] == "" and offer["emails_count"] == 1
    assert offer["emails"][0]["attachments"] == ["CV.pdf"]
    message = email.message_from_bytes(smtp.messages[0].original_content, policy=email.policy.default)
    assert message["Subject"] == "Candidature – Stage Data"
    assert [part.get_filename() for part in message.iter_attachments()] == ["CV.pdf"]


def test_send_validation_and_smtp_failure(client, smtp):
    (offer_id,) = add_offers(sample("1"))
    base = {"to": "rh@acme.fr", "subject": "S", "body": "B"}
    assert client.post(f"/api/offers/{offer_id}/send", json={**base, "to": "nope"}).status_code == 422
    assert client.post(f"/api/offers/{offer_id}/send", json={**base, "attachments": ["absent.pdf"]}).status_code == 422
    assert client.post("/api/offers/999/send", json=base).status_code == 404
    client.put("/api/settings", json={"smtp_port": free_port()})
    failed = client.post(f"/api/offers/{offer_id}/send", json=base)
    assert failed.status_code == 502 and "SMTP" in failed.json()["detail"]
    assert client.get(f"/api/offers/{offer_id}").json()["status"] == "new"  # rien n'a été envoyé
    assert not smtp.messages


def test_test_smtp_endpoint(client, smtp):
    assert client.post("/api/settings/test-smtp").json()["ok"] is True


# --------------------------------------------------------------------------- génération (faux llama-server)


EMAIL = "Objet : Candidature au stage Data Analyst\n\nMadame, Monsieur,\n\nJe souhaite rejoindre ACME.\n\nCordialement,\nClément Test"


def setup_llm(client, url, **extra):
    client.put("/api/settings", json={"llm_base_url": url, "full_name": "Clément Test", "phone": "06 12 34 56 78",
                                      "email": "clement@example.org", "cv_text": "Python, SQL", **extra})


def test_generate_streams_and_saves_draft(client):
    (offer_id,) = add_offers(sample("1"))
    with db.get_conn() as conn:
        db.set_details(conn, offer_id, "Missions : analyses SQL.", [])
    with MockLLM([("sse", sse_chunks("<think>hmm</think>" + EMAIL))]) as llm:
        setup_llm(client, llm.url + "/v1")
        events = read_ndjson(client.post(f"/api/offers/{offer_id}/generate", json={"instructions": "Sois bref", "attachments": ["CV.pdf"]}))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and kinds[-1] == "result" and "delta" in kinds
    streamed = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "<think>" not in streamed and streamed.startswith("Objet :")
    result = events[-1]
    assert result["subject"] == "Candidature au stage Data Analyst"
    assert result["body"] == (
        "Madame, Monsieur,\n\nJe souhaite rejoindre ACME.\n\nCordialement,\n\n"
        "Clément Test\n06 12 34 56 78\nclement@example.org"
    )
    assert result["warning"] is None and result["model"] == "mock-model"
    request = llm.requests[0]
    assert request["stream"] is True and request["model"] == "mock-model"
    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    assert "Missions : analyses SQL." in request["messages"][1]["content"]
    assert "Sois bref" in request["messages"][1]["content"]
    assert "CV.pdf" in request["messages"][1]["content"]
    offer = client.get(f"/api/offers/{offer_id}").json()
    assert offer["draft_subject"] == result["subject"] and offer["draft_body"] == result["body"]


def test_generate_retries_with_shorter_prompt_on_context_error(client):
    (offer_id,) = add_offers(sample("1"))
    with db.get_conn() as conn:
        db.set_details(conn, offer_id, "Mission. " * 3000, [])
    error = {"error": {"code": 400, "message": "request (9000 tokens) exceeds the available context size (4096 tokens)",
                       "type": "exceed_context_size_error"}}
    with MockLLM([("error", 400, error), ("sse", sse_chunks(EMAIL))]) as llm:
        setup_llm(client, llm.url)
        events = read_ndjson(client.post(f"/api/offers/{offer_id}/generate", json={}))
    assert any("nouvel essai" in e.get("message", "") for e in events if e["type"] == "status")
    assert events[-1]["type"] == "result"
    first, second = (len(r["messages"][1]["content"]) for r in llm.requests)
    assert second < first


def test_generate_reports_thinking_exhaustion(client):
    (offer_id,) = add_offers(sample("1"))
    with db.get_conn() as conn:
        db.set_details(conn, offer_id, "", [])
    with MockLLM([("sse", sse_chunks("", reasoning="je réfléchis longuement", finish="length"))]) as llm:
        setup_llm(client, llm.url, llm_disable_thinking=False)
        events = read_ndjson(client.post(f"/api/offers/{offer_id}/generate", json={}))
    assert "reasoning" in [e["type"] for e in events]
    assert events[-1]["type"] == "error" and "réfléchir" in events[-1]["message"]
    assert "chat_template_kwargs" not in llm.requests[0]


def test_generate_warns_on_truncation_and_placeholders(client):
    (offer_id,) = add_offers(sample("1"))
    with db.get_conn() as conn:
        db.set_details(conn, offer_id, "", [])
    with MockLLM([("sse", sse_chunks("Objet : A\n\nBonjour [Nom],\nTexte coupé", finish="length"))]) as llm:
        setup_llm(client, llm.url)
        result = read_ndjson(client.post(f"/api/offers/{offer_id}/generate", json={"language": "en"}))[-1]
    assert result["type"] == "result"
    assert "coupé" in result["warning"] and "[Nom]" in result["warning"]
    assert "Subject:" in llm.requests[0]["messages"][0]["content"]


def test_generate_when_server_is_down(client):
    (offer_id,) = add_offers(sample("1"))
    with db.get_conn() as conn:
        db.set_details(conn, offer_id, "", [])
    setup_llm(client, f"http://127.0.0.1:{free_port()}")
    events = read_ndjson(client.post(f"/api/offers/{offer_id}/generate", json={}))
    assert events[-1]["type"] == "error"
    assert "Impossible de joindre le serveur llama.cpp" in events[-1]["message"]
    assert "llama-server" in events[-1]["message"]


def test_generate_fetches_missing_details_but_continues_on_failure(client, monkeypatch):
    (offer_id,) = add_offers(sample("1"))

    async def failing(*_args):
        raise SourceError("Le site refuse la lecture automatique de cette page (HTTP 403).")

    monkeypatch.setattr(main, "fetch_details", failing)
    with MockLLM([("sse", sse_chunks(EMAIL))]) as llm:
        setup_llm(client, llm.url)
        events = read_ndjson(client.post(f"/api/offers/{offer_id}/generate", json={}))
    assert any(e["type"] == "notice" and "HTTP 403" in e["message"] for e in events)
    assert events[-1]["type"] == "result"
    assert "description non disponible" in llm.requests[0]["messages"][1]["content"]


def test_generate_unknown_offer(client):
    assert client.post("/api/offers/999/generate", json={}).status_code == 404


def test_test_llm_endpoint(client):
    with MockLLM([], n_ctx=8192) as llm:
        client.put("/api/settings", json={"llm_base_url": llm.url})
        data = client.post("/api/settings/test-llm").json()
    assert data["ok"] is True and data["n_ctx"] == 8192 and data["models"] == ["mock-model"]
    client.put("/api/settings", json={"llm_base_url": f"http://127.0.0.1:{free_port()}"})
    assert client.post("/api/settings/test-llm").json()["ok"] is False
