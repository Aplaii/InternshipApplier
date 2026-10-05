"""Filtre « stages en IA », statistiques et recherche automatique."""

from __future__ import annotations

import sqlite3

import pytest

from app import ai_filter, automation, config, db
from app.scrapers import SEARCHERS, OfferDetails, ScrapedOffer
from conftest import MockLLM, sse_chunks


def offer(source_id: str, title: str, source: str = "hellowork", **kwargs) -> ScrapedOffer:
    return ScrapedOffer(source=source, source_id=source_id, url=f"https://example.org/{source}/{source_id}",
                        title=title, company=kwargs.pop("company", "ACME"), **kwargs)


# --------------------------------------------------------------------------- détecteur


@pytest.mark.parametrize("title, topics", [
    ("Stage Ingénieur IA générative (LLM) H/F", ["IA générative"]),
    ("Stage Machine Learning Engineer", ["Machine learning"]),
    ("Stagiaire I.A. – automatisation", ["IA"]),
    ("AI Research Intern", ["IA"]),
    ("Stage Data Scientist F/H", ["Data science"]),
    ("Stage Développeur Python - NLP", ["NLP"]),
    ("Stage vision par ordinateur / deep learning", ["Vision", "Deep learning"]),
    ("Stage MLOps", ["MLOps"]),
    ("Internship - GenAI agents", ["IA générative"]),
])
def test_detects_ai_offers(title, topics):
    assert ai_filter.detect_topics(title) == topics


@pytest.mark.parametrize("title", [
    "Stage Data Analyst H/F", "Stage Comptabilité", "Stage assistant RH", "Stage Média & Communication",
    "Stage développeur web", "Stage Marketing digital",
])
def test_rejects_other_offers(title):
    assert not ai_filter.is_ai_offer(title)


def test_description_refines_but_needs_insistence():
    # Une mention isolée ne suffit pas ; un sujet récurrent, si.
    assert ai_filter.detect_topics("Stage développeur", "Nous utilisons l'IA générative au quotidien.") == []
    text = "Vous entraînerez des modèles de deep learning. Deep learning avec PyTorch."
    assert ai_filter.detect_topics("Stage développeur", text) == ["Deep learning"]
    # Le titre est déjà en IA : la description ajoute ses thèmes même cités une fois.
    assert ai_filter.detect_topics("Stage Data Scientist", "Projet de NLP.") == ["NLP", "Data science"]
    # « j'ai » dans une description n'est pas de l'IA.
    assert ai_filter.detect_topics("Stage comptable", "Si j'ai le temps, ai je dit.") == []


def test_queries_for():
    assert automation.queries_for(" vision ", ai_only=True) == ["vision"]
    assert automation.queries_for("", ai_only=True) == list(ai_filter.DEFAULT_QUERIES)
    assert automation.queries_for("", ai_only=False) == [""]


# --------------------------------------------------------------------------- base de données


def test_ai_topics_stored_filtered_and_refined(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1", "Stage LLM"), offer("2", "Stage Comptable"), offer("3", "Stage Développeur")])
        db.insert_manual_offer(conn, offer("m", "Stage RH", source="manual"), None)
        get = lambda sid, source="hellowork": db.find_offer_id(conn, source, sid)
        assert db.get_offer(conn, get("1"))["ai_topics"] == ["IA générative"]
        assert db.get_offer(conn, get("2"))["ai_topics"] == []
        # Tout est visible sans filtre ; avec le filtre, l'offre manuelle reste visible.
        assert db.list_offers(conn)[1] == 4
        items, total = db.list_offers(conn, ai_only=True)
        assert total == 2 and {i["title"] for i in items} == {"Stage LLM", "Stage RH"}
        counts = db.counts(conn, ai_only=True)
        assert counts["total"] == 2 and counts["hidden_non_ai"] == 2
        # La description lue en ligne peut révéler une offre en IA.
        db.set_details(conn, get("3"), "Computer vision et computer vision embarquée.", [])
        assert db.get_offer(conn, get("3"))["ai_topics"] == ["Vision"]
        # Une mise à jour par la recherche ne perd pas les thèmes venus de la description.
        db.upsert_offers(conn, [offer("3", "Stage Développeur")])
        assert db.get_offer(conn, get("3"))["ai_topics"] == ["Vision"]


def test_migration_from_version_1(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path / "documents")
    old_schema = db.SCHEMA.replace("    ai_topics          TEXT    NOT NULL DEFAULT '',\n", "")
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO offers (source, source_id, url, title, first_seen_at, last_seen_at) "
        "VALUES ('hellowork', '1', 'https://x', 'Stage Deep Learning', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    db.init_db()
    with db.get_conn() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert db.list_offers(conn, ai_only=True)[0][0]["ai_topics"] == ["Deep learning"]


def test_stats(data_dir):
    with db.get_conn() as conn:
        assert db.stats(conn)["total"] == 0
        db.upsert_offers(conn, [
            offer("1", "Stage LLM", company="Mistral"), offer("2", "Stage NLP", company="mistral"),
            offer("3", "Stage Data Scientist", source="wttj", company="Beta"), offer("4", "Stage Comptable"),
        ])
        get = lambda sid, source="hellowork": db.find_offer_id(conn, source, sid)
        db.record_sent_email(conn, get("1"), to_addr="rh@m.ai", subject="s", body="b", attachments=[], message_id="x")
        db.update_offers(conn, [get("2")], {"status": "interview"})
        stats = db.stats(conn, ai_only=True)
    assert stats["total"] == 3 and stats["applied"] == 2 and stats["interviews"] == 1
    assert stats["replies"] == 1 and stats["reply_rate"] == 0.5
    assert stats["by_source"] == {"hellowork": 2, "wttj": 1}
    assert {t["topic"]: t["count"] for t in stats["topics"]} == {"IA générative": 1, "NLP": 1, "Data science": 1}
    assert len(stats["weekly"]) == 12
    assert stats["weekly"][-1]["found"] == 3 and stats["weekly"][-1]["applied"] == 1
    assert stats["companies"][0] == {"name": "Mistral", "offers": 2, "applied": 2}


# --------------------------------------------------------------------------- API


def fake_searchers(monkeypatch, results: dict[str, list[ScrapedOffer]], seen: list | None = None):
    for source in config.SEARCH_SOURCES:
        async def search(_client, params, source=source):
            if seen is not None:
                seen.append((source, params.keywords))
            return results.get(source, [])

        monkeypatch.setitem(SEARCHERS, source, search)


def test_fetch_keeps_only_ai_offers(client, monkeypatch):
    seen: list = []
    fake_searchers(monkeypatch, {"hellowork": [offer("1", "Stage IA"), offer("2", "Stage Vente")]}, seen)
    payload = {"sources": ["hellowork"], "max_per_source": 20}
    result = client.post("/api/fetch", json=payload).json()
    # Sans mot-clé, plusieurs requêtes couvrent le domaine de l'IA (et les doublons sont fusionnés).
    assert [kw for _, kw in seen] == list(ai_filter.DEFAULT_QUERIES)
    assert result["results"][0] == {"source": "hellowork", "label": "HelloWork", "found": 2, "kept": 1, "new": 1, "error": None}
    assert result["filtered_total"] == 1 and result["ai_only"] is True
    assert [o["title"] for o in client.get("/api/offers").json()["items"]] == ["Stage IA"]

    # Filtre désactivé (choix enregistré) : l'offre hors IA est ajoutée et visible.
    result = client.post("/api/fetch", json={**payload, "ai_only": False}).json()
    assert result["new_total"] == 1 and result["filtered_total"] == 0
    assert client.get("/api/settings").json()["ai_only"] is False
    assert client.get("/api/counts").json()["total"] == 2
    client.put("/api/settings", json={"ai_only": True})
    counts = client.get("/api/counts").json()
    assert counts["total"] == 1 and counts["hidden_non_ai"] == 1 and counts["ai_only"] is True
    assert client.get("/api/stats").json()["total"] == 1


def test_fetch_reads_details_of_new_offers(client, monkeypatch, no_sleep):
    fake_searchers(monkeypatch, {"hellowork": [offer("1", "Stage IA")]})
    calls = []

    async def details(_client, source, source_id, url):
        calls.append(source_id)
        return OfferDetails(description="Stage en NLP. Contact : rh@acme.fr", emails=["rh@acme.fr"])

    monkeypatch.setattr(automation, "fetch_details", details)
    result = client.post("/api/fetch", json={"sources": ["hellowork"], "keywords": "IA"}).json()
    assert result["details"] == 1 and calls == ["1"]
    item = client.get("/api/offers").json()["items"][0]
    assert item["ai_topics"] == ["NLP"]
    assert client.get(f"/api/offers/{item['id']}").json()["contact_emails"] == ["rh@acme.fr"]
    # Une offre déjà connue n'est pas relue.
    client.post("/api/fetch", json={"sources": ["hellowork"], "keywords": "IA"})
    assert calls == ["1"]


def test_automation_settings_validation(client):
    assert client.put("/api/settings", json={"auto_interval_hours": 0.5}).status_code == 422
    assert client.put("/api/settings", json={"auto_draft_max": 100}).status_code == 422
    assert client.put("/api/settings", json={"auto_last_run": "x"}).status_code == 422  # réservé à l'application
    status = client.get("/api/automation").json()
    assert status["enabled"] is False and status["next_run"] is None and status["last_run"] is None
    status = client.put("/api/settings", json={"auto_enabled": True, "auto_interval_hours": 12}).json()
    assert status["auto_enabled"] is True and status["auto_interval_hours"] == 12


@pytest.mark.anyio
async def test_run_once_searches_reads_and_drafts(data_dir, monkeypatch, no_sleep):
    fake_searchers(monkeypatch, {
        "linkedin": [offer("1", "Stage LLM", source="linkedin"), offer("2", "Stage Vente", source="linkedin"),
                     offer("3", "Stage Computer Vision", source="linkedin")],
    })

    async def details(_client, source, source_id, url):
        return OfferDetails(description="Agents LLM.", emails=[f"rh{source_id}@acme.fr"])

    monkeypatch.setattr(automation, "fetch_details", details)
    email = "Objet : Candidature stage IA\n\nMadame, Monsieur,\n\nJe suis motivé.\n\nCordialement"
    with MockLLM([("sse", sse_chunks(email))]) as mock:
        with db.get_conn() as conn:
            db.save_settings(conn, {
                "search_sources": ["linkedin"], "auto_enabled": True, "auto_draft": True, "auto_draft_max": 1,
                "llm_base_url": mock.url, "full_name": "Clément Test",
            })
        auto = automation.Automation()
        summary = await auto.run_once()
    assert summary["ok"] and summary["new_total"] == 2 and summary["filtered_total"] == 1
    assert summary["details"] == 2 and summary["drafted"] == 1 and summary["draft_error"] is None
    with db.get_conn() as conn:
        items, _ = db.list_offers(conn, ai_only=True)
        drafted = [db.get_offer(conn, i["id"]) for i in items if i["has_draft"]]
        settings = db.load_settings(conn)
    assert len(drafted) == 1 and drafted[0]["status"] == "new"  # brouillon préparé, jamais envoyé
    assert drafted[0]["draft_subject"] == "Candidature stage IA"
    assert drafted[0]["draft_to"] == drafted[0]["contact_emails"][0]
    assert settings["auto_last_summary"]["drafted"] == 1 and settings["auto_last_run"]
    status = await auto.status()
    assert status["next_run"] > status["last_run"]  # prochaine recherche dans 6 heures


@pytest.mark.anyio
async def test_run_once_reports_llm_down(data_dir, monkeypatch, no_sleep):
    fake_searchers(monkeypatch, {"wttj": [offer("1", "Stage IA", source="wttj")]})

    async def details(_client, source, source_id, url):
        return OfferDetails(description="")

    monkeypatch.setattr(automation, "fetch_details", details)
    with db.get_conn() as conn:
        db.save_settings(conn, {"search_sources": ["wttj"], "auto_draft": True, "llm_base_url": "http://127.0.0.1:9"})
    summary = await automation.Automation().run_once()
    assert summary["new_total"] == 1 and summary["drafted"] == 0 and summary["draft_error"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
