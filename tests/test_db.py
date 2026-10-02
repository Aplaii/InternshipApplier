"""Base de données : déduplication, filtres, suivi des statuts, paramètres."""

from __future__ import annotations

from app import config, db
from app.scrapers.base import ScrapedOffer


def offer(source_id: str, **kwargs) -> ScrapedOffer:
    data = {
        "source": "hellowork", "source_id": source_id, "url": f"https://exemple.fr/{source_id}",
        "title": f"Stage {source_id}", "company": "ACME", "location": "Lyon", "contract": "Stage",
        "published_at": f"2026-09-{int(source_id):02d}T10:00:00Z" if source_id.isdigit() else None,
    }
    data.update(kwargs)
    return ScrapedOffer(**data)


def ids(items):
    return [item["source_id"] for item in items]


def test_upsert_new_and_existing_keeps_user_fields(data_dir):
    with db.get_conn() as conn:
        assert db.upsert_offers(conn, [offer("1"), offer("2")]) == 2
        offer_id = db.find_offer_id(conn, "hellowork", "1")
        db.update_offers(conn, [offer_id], {"status": "sent", "is_starred": True, "is_read": True, "notes": "relancer"})
        # Nouvelle recherche : titre mis à jour, date absente -> conservée, champs utilisateur intacts.
        assert db.upsert_offers(conn, [offer("1", title="Stage 1 (modifié)", published_at=None), offer("3")]) == 1
        row = db.get_offer(conn, offer_id)
    assert row["title"] == "Stage 1 (modifié)"
    assert row["published_at"] == "2026-09-01T10:00:00Z"
    assert row["status"] == "sent" and row["is_starred"] is True and row["is_read"] is True
    assert row["notes"] == "relancer"


def test_same_id_on_two_sources_are_distinct(data_dir):
    with db.get_conn() as conn:
        assert db.upsert_offers(conn, [offer("1"), offer("1", source="linkedin")]) == 2


def test_list_filters_and_sort(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [
            offer("1", title="Ingénieur données", company="Société Œnologie"),
            offer("2", source="linkedin", location="Paris"),
            offer("3", title="Stage 100% télétravail"),
            offer("4", published_at=None),
        ])
        get = lambda name: db.find_offer_id(conn, "hellowork", name)
        db.update_offers(conn, [get("3")], {"is_archived": True})
        db.update_offers(conn, [get("1")], {"is_starred": True, "status": "interview", "is_read": True})

        items, total = db.list_offers(conn)
        # Sans date de publication, l'offre 4 est classée à sa date d'ajout (aujourd'hui) : en premier.
        assert ids(items) == ["4", "2", "1"] and total == 3
        assert ids(db.list_offers(conn, folder="archived")[0]) == ["3"]
        assert ids(db.list_offers(conn, folder="starred")[0]) == ["1"]
        assert ids(db.list_offers(conn, folder="unread")[0]) == ["4", "2"]
        assert db.list_offers(conn, folder="all")[1] == 4
        assert ids(db.list_offers(conn, folder="all", status="interview")[0]) == ["1"]
        assert ids(db.list_offers(conn, source="linkedin")[0]) == ["2"]
        # Recherche sans accents ni majuscules, ligatures comprises, sur plusieurs mots.
        assert ids(db.list_offers(conn, q="INGENIEUR")[0]) == ["1"]
        assert ids(db.list_offers(conn, q="oenologie ingénieur")[0]) == ["1"]
        assert ids(db.list_offers(conn, q="paris")[0]) == ["2"]
        # Les caractères spéciaux de LIKE sont pris littéralement.
        assert ids(db.list_offers(conn, folder="all", q="100%")[0]) == ["3"]
        assert db.list_offers(conn, folder="all", q="_")[1] == 0
        page2, total = db.list_offers(conn, folder="all", page=2, page_size=3)
        assert total == 4 and len(page2) == 1


def test_list_flags(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1")])
        offer_id = db.find_offer_id(conn, "hellowork", "1")
        db.update_offers(conn, [offer_id], {"draft_body": "Bonjour", "notes": "x"})
        item = db.list_offers(conn)[0][0]
    assert item["has_draft"] is True and item["has_notes"] is True and item["emails_count"] == 0
    assert "description" not in item and "draft_body" not in item


def test_status_date_changes_only_on_real_change(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1")])
        offer_id = db.find_offer_id(conn, "hellowork", "1")
        assert db.get_offer(conn, offer_id)["status_changed_at"] is None
        db.update_offers(conn, [offer_id], {"status": "sent"})
        conn.execute("UPDATE offers SET status_changed_at = '2026-01-01T00:00:00Z' WHERE id = ?", (offer_id,))
        db.update_offers(conn, [offer_id], {"status": "sent", "is_read": True})
        assert db.get_offer(conn, offer_id)["status_changed_at"] == "2026-01-01T00:00:00Z"
        db.update_offers(conn, [offer_id], {"status": "interview"})
        assert db.get_offer(conn, offer_id)["status_changed_at"] != "2026-01-01T00:00:00Z"


def test_update_ignores_unknown_fields(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1")])
        offer_id = db.find_offer_id(conn, "hellowork", "1")
        assert db.update_offers(conn, [offer_id], {"title": "piraté", "id": 99}) == 0
        assert db.get_offer(conn, offer_id)["title"] == "Stage 1"


def test_record_sent_email(data_dir):
    with db.get_conn() as conn:
        db.upsert_offers(conn, [offer("1"), offer("2")])
        first, second = db.find_offer_id(conn, "hellowork", "1"), db.find_offer_id(conn, "hellowork", "2")
        db.update_offers(conn, [first], {"draft_subject": "Objet", "draft_body": "Texte"})
        db.update_offers(conn, [second], {"status": "interview"})
        for offer_id in (first, second):
            db.record_sent_email(conn, offer_id, to_addr="rh@acme.fr", subject="Candidature", body="Bonjour",
                                 attachments=["CV.pdf"], message_id="<id@x>")
        sent, interview = db.get_offer(conn, first), db.get_offer(conn, second)
    assert sent["status"] == "sent" and sent["status_changed_at"]
    assert sent["draft_body"] == "" and sent["draft_subject"] == "" and sent["draft_to"] == "rh@acme.fr"
    assert sent["emails"][0]["attachments"] == ["CV.pdf"] and sent["emails_count"] == 1
    assert interview["status"] == "interview"  # une relance ne fait pas régresser le statut


def test_counts(data_dir):
    with db.get_conn() as conn:
        assert db.counts(conn)["total"] == 0
        db.upsert_offers(conn, [offer("1"), offer("2"), offer("3", source="wttj")])
        get = lambda name, source="hellowork": db.find_offer_id(conn, source, name)
        db.update_offers(conn, [get("1")], {"is_archived": True, "status": "refused"})
        db.update_offers(conn, [get("2")], {"is_read": True, "is_starred": True})
        counts = db.counts(conn)
    assert counts["total"] == 3 and counts["inbox"] == 2 and counts["unread"] == 1
    assert counts["starred"] == 1 and counts["archived"] == 1
    assert counts["by_status"] == {"new": 2, "sent": 0, "answered": 0, "refused": 1, "interview": 0}
    assert counts["by_source"] == {"hellowork": {"total": 1, "unread": 0}, "wttj": {"total": 1, "unread": 1}}


def test_settings_roundtrip(data_dir):
    with db.get_conn() as conn:
        settings = db.load_settings(conn)
        assert settings == config.DEFAULT_SETTINGS
        db.save_settings(conn, {"full_name": "Clément", "search_sources": ["wttj"], "inconnu": 1})
        settings = db.load_settings(conn)
    assert settings["full_name"] == "Clément" and settings["search_sources"] == ["wttj"]
    assert "inconnu" not in settings
    assert config.DEFAULT_SETTINGS["search_sources"] == ["hellowork", "linkedin", "wttj"]  # non modifié


def test_normalize():
    assert db.normalize("Ingénieur Œnologue ÉTÉ") == "ingenieur oenologue ete"
    assert db.normalize(None) == ""
