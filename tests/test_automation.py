"""Filtre « stages FLE », statistiques et recherche automatique."""

from __future__ import annotations

import sqlite3

import pytest

from app import fle_filter, automation, config, db
from app.scrapers import SEARCHERS, OfferDetails, ScrapedOffer
from conftest import MockLLM, sse_chunks


def offer(source_id: str, title: str, source: str = "hellowork", **kwargs) -> ScrapedOffer:
    return ScrapedOffer(source=source, source_id=source_id, url=f"https://example.org/{source}/{source_id}",
                        title=title, company=kwargs.pop("company", "ACME"), **kwargs)


# --------------------------------------------------------------------------- détecteur


@pytest.mark.parametrize("title, topics", [
    ("Stage FLE H/F", ["FLE / FLS"]),
    ("Stagiaire en français langue étrangère – Alliance française d'Ottawa", ["FLE / FLS", "Alliance / Institut français"]),
    ("French Teacher Intern", ["Enseignement du français"]),
    ("Fransklärare praktik", ["Enseignement du français"]),
    ("Ranskan opettaja harjoittelija", ["Enseignement du français"]),
    ("Stage assistant de langue française", ["Assistanat de langue"]),
    ("French Language Assistant – Dublin", ["Assistanat de langue"]),
    ("Stage Institut français de Suède", ["Alliance / Institut français"]),
    ("Examinateur DELF / DALF", ["Certifications"]),
    ("Stage en ingénierie pédagogique", ["Didactique & pédagogie"]),
    ("French immersion camp counsellor", ["Immersion"]),
    ("FSL teacher internship", ["FLE / FLS"]),
])
def test_detects_fle_offers(title, topics):
    assert fle_filter.detect_topics(title) == topics


@pytest.mark.parametrize("title", [
    "Stage Data Analyst H/F", "Stage Comptabilité", "Stage assistant RH", "Stage Média & Communication",
    "Customer Service Agent – French speaker", "French-speaking Sales Intern", "Stage Marketing digital",
    "Fleet manager intern", "Stage développeur web",
])
def test_rejects_other_offers(title):
    assert not fle_filter.is_fle_offer(title)


def test_description_refines_but_needs_insistence():
    # Une mention isolée ne suffit pas ; un sujet récurrent, si.
    assert fle_filter.detect_topics("Stage animation", "Vous préparerez des élèves au DELF.") == []
    text = "Vous donnerez des cours de français aux adultes. Cours de français du soir."
    assert fle_filter.detect_topics("Stage animation", text) == ["Enseignement du français"]
    # Le titre est déjà FLE : la description ajoute ses thèmes même cités une fois.
    assert fle_filter.detect_topics("Stage FLE", "Préparation au DELF.") == ["FLE / FLS", "Certifications"]
    # Parler français pour un poste commercial n'en fait pas un stage FLE.
    assert fle_filter.detect_topics("Sales intern", "French speaker, fluent French and English.") == []
    # « Langue française » seul sert quand rien de plus précis n'apparaît.
    assert fle_filter.detect_topics("Tuteur / tutrice en langue française") == ["Langue française"]


def test_queries_for():
    assert automation.queries_for(" Alliance française ", fle_only=True) == ["Alliance française"]
    assert automation.queries_for("", fle_only=True) == list(fle_filter.DEFAULT_QUERIES)
    assert automation.queries_for("", fle_only=False) == [""]
    # LinkedIn : une seule requête « OR », sans « FLE » seul (qui ramène « Fleet manager »).
    [query] = automation.queries_for("", fle_only=True, source="linkedin")
    assert " OR " in query and '"French teacher"' in query and not query.startswith("FLE")


def test_split_locations():
    assert fle_filter.split_locations("Sweden; Norway ;\nCanada; Sweden") == ["Sweden", "Norway", "Canada"]
    assert fle_filter.split_locations("Montréal, QC") == ["Montréal, QC"]
    assert fle_filter.split_locations("") == [""]
    assert config.DEFAULT_SETTINGS["search_location"].startswith("Sweden; Norway")


# --------------------------------------------------------------------------- base de données


def test_fle_topics_stored_filtered_and_refined(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1", "Stage FLE"), offer("2", "Stage Comptable"), offer("3", "Stage Animation")])
        db.insert_manual_offer(conn, offer("m", "Stage RH", source="manual"), None)
        get = lambda sid, source="hellowork": db.find_offer_id(conn, source, sid)
        assert db.get_offer(conn, get("1"))["fle_topics"] == ["FLE / FLS"]
        assert db.get_offer(conn, get("2"))["fle_topics"] == []
        # Tout est visible sans filtre ; avec le filtre, l'offre manuelle reste visible.
        assert db.list_offers(conn)[1] == 4
        items, total = db.list_offers(conn, fle_only=True)
        assert total == 2 and {i["title"] for i in items} == {"Stage FLE", "Stage RH"}
        counts = db.counts(conn, fle_only=True)
        assert counts["total"] == 2 and counts["hidden_non_fle"] == 2
        # La description lue en ligne peut révéler une offre FLE.
        db.set_details(conn, get("3"), "Cours de français pour enfants et cours de français pour adultes.", [])
        assert db.get_offer(conn, get("3"))["fle_topics"] == ["Enseignement du français"]
        # Une mise à jour par la recherche ne perd pas les thèmes venus de la description.
        db.upsert_offers(conn, [offer("3", "Stage Animation")])
        assert db.get_offer(conn, get("3"))["fle_topics"] == ["Enseignement du français"]


def _old_database(tmp_path, monkeypatch, schema: str, version: int) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path / "documents")
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO offers (source, source_id, url, title, first_seen_at, last_seen_at) "
        "VALUES ('hellowork', '1', 'https://x', 'Stage Alliance française', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    db.init_db()


def test_migration_from_version_1(tmp_path, monkeypatch):
    old_schema = db.SCHEMA.replace("    fle_topics          TEXT    NOT NULL DEFAULT '',\n", "")
    assert old_schema != db.SCHEMA
    _old_database(tmp_path, monkeypatch, old_schema, 1)
    with db.get_conn() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert db.list_offers(conn, fle_only=True)[0][0]["fle_topics"] == ["Alliance / Institut français"]


def test_migration_from_version_2_renames_ai_topics(tmp_path, monkeypatch):
    old_schema = db.SCHEMA.replace("    fle_topics ", "    ai_topics  ")
    assert "ai_topics" in old_schema
    _old_database(tmp_path, monkeypatch, old_schema, 2)
    with db.get_conn() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(offers)")}
        assert "fle_topics" in columns and "ai_topics" not in columns
        assert db.list_offers(conn, fle_only=True)[0][0]["fle_topics"] == ["Alliance / Institut français"]


def test_stats(data_dir):
    with db.get_conn() as conn:
        assert db.stats(conn)["total"] == 0
        db.upsert_offers(conn, [
            offer("1", "Stage FLE", company="Alliance française"), offer("2", "Stage enseignant de français", company="alliance française"),
            offer("3", "Stage assistant de langue", source="wttj", company="Beta"), offer("4", "Stage Comptable"),
        ])
        get = lambda sid, source="hellowork": db.find_offer_id(conn, source, sid)
        db.record_sent_email(conn, get("1"), to_addr="rh@m.ai", subject="s", body="b", attachments=[], message_id="x")
        db.update_offers(conn, [get("2")], {"status": "interview"})
        stats = db.stats(conn, fle_only=True)
    assert stats["total"] == 3 and stats["applied"] == 2 and stats["interviews"] == 1
    assert stats["replies"] == 1 and stats["reply_rate"] == 0.5
    assert stats["by_source"] == {"hellowork": 2, "wttj": 1}
    assert {t["topic"]: t["count"] for t in stats["topics"]} == {"FLE / FLS": 1, "Enseignement du français": 1, "Assistanat de langue": 1}
    assert len(stats["weekly"]) == 12
    assert stats["weekly"][-1]["found"] == 3 and stats["weekly"][-1]["applied"] == 1
    assert stats["companies"][0] == {"name": "Alliance française", "offers": 2, "applied": 2}


# --------------------------------------------------------------------------- API


def fake_searchers(monkeypatch, results: dict[str, list[ScrapedOffer]], seen: list | None = None):
    for source in config.SEARCH_SOURCES:
        async def search(_client, params, source=source):
            if seen is not None:
                seen.append((source, params.keywords))
            return results.get(source, [])

        monkeypatch.setitem(SEARCHERS, source, search)


def test_fetch_keeps_only_fle_offers(client, monkeypatch):
    seen: list = []
    fake_searchers(monkeypatch, {"hellowork": [offer("1", "Stage FLE"), offer("2", "Stage Vente")]}, seen)
    payload = {"sources": ["hellowork"], "max_per_source": 20}
    result = client.post("/api/fetch", json=payload).json()
    # Sans mot-clé, plusieurs requêtes couvrent le domaine du FLE (et les doublons sont fusionnés).
    assert [kw for _, kw in seen] == list(fle_filter.DEFAULT_QUERIES)
    assert result["results"][0] == {"source": "hellowork", "label": "HelloWork", "found": 2, "kept": 1, "new": 1, "error": None, "skipped": False}
    assert result["filtered_total"] == 1 and result["fle_only"] is True
    assert [o["title"] for o in client.get("/api/offers").json()["items"]] == ["Stage FLE"]

    # Filtre désactivé (choix enregistré) : l'offre hors FLE est ajoutée et visible.
    result = client.post("/api/fetch", json={**payload, "fle_only": False}).json()
    assert result["new_total"] == 1 and result["filtered_total"] == 0
    assert client.get("/api/settings").json()["fle_only"] is False
    assert client.get("/api/counts").json()["total"] == 2
    client.put("/api/settings", json={"fle_only": True})
    counts = client.get("/api/counts").json()
    assert counts["total"] == 1 and counts["hidden_non_fle"] == 1 and counts["fle_only"] is True
    assert client.get("/api/stats").json()["total"] == 1


def test_fetch_reads_details_of_new_offers(client, monkeypatch, no_sleep):
    fake_searchers(monkeypatch, {"hellowork": [offer("1", "Stage FLE")]})
    calls = []

    async def details(_client, source, source_id, url):
        calls.append(source_id)
        return OfferDetails(description="Préparation au DELF. Contact : rh@acme.fr", emails=["rh@acme.fr"])

    monkeypatch.setattr(automation, "fetch_details", details)
    result = client.post("/api/fetch", json={"sources": ["hellowork"], "keywords": "FLE"}).json()
    assert result["details"] == 1 and calls == ["1"]
    item = client.get("/api/offers").json()["items"][0]
    assert item["fle_topics"] == ["FLE / FLS", "Certifications"]
    assert client.get(f"/api/offers/{item['id']}").json()["contact_emails"] == ["rh@acme.fr"]
    # Une offre déjà connue n'est pas relue.
    client.post("/api/fetch", json={"sources": ["hellowork"], "keywords": "FLE"})
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
        "linkedin": [offer("1", "Stage FLE", source="linkedin"), offer("2", "Stage Vente", source="linkedin"),
                     offer("3", "French Teacher Intern", source="linkedin")],
    })

    async def details(_client, source, source_id, url):
        return OfferDetails(description="Cours de FLE.", emails=[f"rh{source_id}@acme.fr"])

    monkeypatch.setattr(automation, "fetch_details", details)
    email = "Objet : Candidature stage FLE\n\nMadame, Monsieur,\n\nJe suis motivé.\n\nCordialement"
    with MockLLM([("sse", sse_chunks(email))]) as mock:
        with db.get_conn() as conn:
            db.save_settings(conn, {
                "search_sources": ["linkedin"], "auto_enabled": True, "auto_draft": True, "auto_draft_max": 1, "auto_translate": False,
                "llm_base_url": mock.url, "full_name": "Clément Test",
            })
        auto = automation.Automation()
        summary = await auto.run_once()
    assert summary["ok"] and summary["new_total"] == 2 and summary["filtered_total"] == 1
    assert summary["details"] == 2 and summary["drafted"] == 1 and summary["draft_error"] is None
    with db.get_conn() as conn:
        items, _ = db.list_offers(conn, fle_only=True)
        drafted = [db.get_offer(conn, i["id"]) for i in items if i["has_draft"]]
        settings = db.load_settings(conn)
    assert len(drafted) == 1 and drafted[0]["status"] == "new"  # brouillon préparé, jamais envoyé
    assert drafted[0]["draft_subject"] == "Candidature stage FLE"
    assert drafted[0]["draft_to"] == drafted[0]["contact_emails"][0]
    assert settings["auto_last_summary"]["drafted"] == 1 and settings["auto_last_run"]
    status = await auto.status()
    assert status["next_run"] > status["last_run"]  # prochaine recherche dans 6 heures


@pytest.mark.anyio
async def test_run_once_reports_llm_down(data_dir, monkeypatch, no_sleep):
    fake_searchers(monkeypatch, {"wttj": [offer("1", "Stage FLE", source="wttj")]})

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


def test_migration_to_version_5_clears_fake_linkedin_internships(tmp_path, monkeypatch):
    old_schema = db.SCHEMA
    for column in ("title_fr           TEXT,", "description_fr     TEXT,",
                   "is_spontaneous     INTEGER NOT NULL DEFAULT 0,", "is_internship      INTEGER NOT NULL DEFAULT 0,"):
        old_schema = old_schema.replace(f"    {column}\n", "")
    assert "is_internship" not in old_schema
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(config, "DOCUMENTS_DIR", tmp_path / "documents")
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(old_schema)
    rows = [("linkedin", "1", "Docent Frans", "Stage"), ("linkedin", "2", "French Teacher Intern", "Stage"),
            ("hellowork", "3", "Animation atelier FLE", "Stage · 2 mois"), ("directory", "d", "Candidature spontanée – stage FLE", "")]
    conn.executemany(
        "INSERT INTO offers (source, source_id, url, title, contract, first_seen_at, last_seen_at) "
        "VALUES (?, ?, 'https://x', ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')", rows)
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    db.init_db()
    with db.get_conn() as conn:
        got = {r["source_id"]: dict(r) for r in conn.execute("SELECT * FROM offers")}
    assert got["1"]["is_internship"] == 0 and got["1"]["contract"] == ""
    assert got["2"]["is_internship"] == 1
    assert got["3"]["is_internship"] == 1 and got["3"]["contract"] == "Stage · 2 mois"
    assert got["d"]["is_spontaneous"] == 1
    assert got["3"]["title_fr"] is None and got["1"]["title_fr"] is None
