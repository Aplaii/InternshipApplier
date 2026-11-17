"""Annuaire des établissements FLE (``app/data/institutions.json``).

Les offres de stage FLE sont rarement publiées sur les sites d'emploi : la plupart des
stages se trouvent par candidature spontanée auprès d'Alliances françaises, d'Instituts
français, d'universités ou de lycées français. L'annuaire liste ces établissements
(pays nordiques, Europe du Nord, Canada) ; chacun peut être importé dans la boîte de
réception comme une « candidature spontanée », source « directory », pour être suivi et
recevoir un brouillon d'email comme n'importe quelle offre.
"""

from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import db
from .scrapers import ScrapedOffer

SOURCE = "directory"
DATA_FILE = Path(__file__).resolve().parent / "data" / "institutions.json"
OFFER_TITLE = "Candidature spontanée – stage FLE"
CONTRACT = "Candidature spontanée"


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    with DATA_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


def items() -> list[dict[str, Any]]:
    return list(load()["institutions"])


def description_for(inst: dict[str, Any]) -> str:
    """Texte donné à l'IA à la place d'une description d'offre."""
    place = ", ".join(p for p in (inst.get("city"), inst.get("country")) if p)
    lines = [
        f"{inst['name']} ({inst.get('kind', '')}) – {place}.",
        inst.get("notes", ""),
        "",
        "Candidature spontanée pour un stage de FLE (français langue étrangère) : aucune "
        "offre n'est forcément publiée. Proposer observation de classes, prise en charge "
        "progressive de cours, ateliers de conversation, aide à la préparation du DELF et "
        "participation aux activités culturelles.",
        f"Site : {inst.get('website', '')}",
    ]
    return "\n".join(line for line in lines if line is not None).strip()


def to_offer(inst: dict[str, Any]) -> ScrapedOffer:
    place = ", ".join(p for p in (inst.get("city"), inst.get("country")) if p)
    return ScrapedOffer(
        source=SOURCE, source_id=inst["id"], url=inst.get("website") or "",
        title=OFFER_TITLE, company=inst["name"], location=place, contract=CONTRACT,
    )


def with_import_state(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Établissements de l'annuaire, avec l'identifiant de l'offre s'il est déjà importé."""
    imported = {
        r["source_id"]: r["id"]
        for r in conn.execute("SELECT id, source_id FROM offers WHERE source = ?", (SOURCE,))
    }
    return [{**inst, "offer_id": imported.get(inst["id"])} for inst in items()]


def import_ids(conn: sqlite3.Connection, ids: list[str]) -> dict[str, Any]:
    """Importe les établissements ``ids`` (les déjà importés sont ignorés)."""
    by_id = {inst["id"]: inst for inst in items()}
    unknown = [i for i in ids if i not in by_id]
    created: list[int] = []
    skipped = 0
    for inst_id in dict.fromkeys(i for i in ids if i in by_id):
        inst = by_id[inst_id]
        if db.find_offer_id(conn, SOURCE, inst_id) is not None:
            skipped += 1
            continue
        description = description_for(inst)
        offer_id = db.insert_manual_offer(conn, to_offer(inst), description, spontaneous=True)
        email = (inst.get("email") or "").strip()
        db.set_details(conn, offer_id, description, [email] if email else [])
        if email:
            db.update_offers(conn, [offer_id], {"draft_to": email})
        # Importé = à traiter : l'offre arrive non lue dans la boîte de réception.
        db.update_offers(conn, [offer_id], {"is_read": False})
        created.append(offer_id)
    return {"imported": len(created), "skipped": skipped, "unknown": unknown, "ids": created}
