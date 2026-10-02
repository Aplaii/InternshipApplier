"""Stockage SQLite : offres, emails envoyés et paramètres.

Une connexion est ouverte par opération (``get_conn``) : c'est simple, sûr avec
les threads de FastAPI, et fonctionne aussi sur un répertoire personnel réseau
(NFS), où le mode WAL de SQLite est à éviter.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from . import config

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source             TEXT    NOT NULL,
    source_id          TEXT    NOT NULL,
    url                TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    company            TEXT    NOT NULL DEFAULT '',
    location           TEXT    NOT NULL DEFAULT '',
    contract           TEXT    NOT NULL DEFAULT '',
    logo_url           TEXT    NOT NULL DEFAULT '',
    published_at       TEXT,
    first_seen_at      TEXT    NOT NULL,
    last_seen_at       TEXT    NOT NULL,
    description        TEXT,
    details_fetched_at TEXT,
    contact_emails     TEXT    NOT NULL DEFAULT '[]',
    status             TEXT    NOT NULL DEFAULT 'new',
    status_changed_at  TEXT,
    is_read            INTEGER NOT NULL DEFAULT 0,
    is_starred         INTEGER NOT NULL DEFAULT 0,
    is_archived        INTEGER NOT NULL DEFAULT 0,
    notes              TEXT    NOT NULL DEFAULT '',
    draft_to           TEXT    NOT NULL DEFAULT '',
    draft_subject      TEXT    NOT NULL DEFAULT '',
    draft_body         TEXT    NOT NULL DEFAULT '',
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_offers_status ON offers (status);
CREATE INDEX IF NOT EXISTS idx_offers_archived ON offers (is_archived);

CREATE TABLE IF NOT EXISTS emails (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id    INTEGER NOT NULL REFERENCES offers (id) ON DELETE CASCADE,
    to_addr     TEXT    NOT NULL,
    subject     TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    attachments TEXT    NOT NULL DEFAULT '[]',
    message_id  TEXT    NOT NULL DEFAULT '',
    sent_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emails_offer ON emails (offer_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

FOLDERS = ("inbox", "unread", "starred", "archived", "all")

# Colonnes renvoyées pour la liste (sans la description ni le texte des brouillons).
LIST_COLUMNS = """
    id, source, source_id, url, title, company, location, contract, logo_url,
    published_at, first_seen_at, status, status_changed_at,
    is_read, is_starred, is_archived,
    (draft_body != '') AS has_draft,
    (notes != '') AS has_notes,
    (SELECT COUNT(*) FROM emails e WHERE e.offer_id = offers.id) AS emails_count
"""

SORT_ORDER = "ORDER BY COALESCE(published_at, first_seen_at) DESC, id DESC"

_BOOL_FIELDS = ("is_read", "is_starred", "is_archived", "has_draft", "has_notes")
_PATCHABLE = (
    "status", "is_read", "is_starred", "is_archived", "notes",
    "draft_to", "draft_subject", "draft_body",
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss"})


def normalize(text: Any) -> str:
    """Minuscules sans accents, pour une recherche tolérante (« ingenieur » trouve « Ingénieur »)."""
    if text is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text).translate(_LIGATURES))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("norm", 1, normalize, deterministic=True)
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# --------------------------------------------------------------------------- offres


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in _BOOL_FIELDS:
        if key in data:
            data[key] = bool(data[key])
    if "contact_emails" in data:
        try:
            data["contact_emails"] = json.loads(data["contact_emails"] or "[]")
        except ValueError:
            data["contact_emails"] = []
    return data


def upsert_offers(conn: sqlite3.Connection, offers: Iterable[Any]) -> int:
    """Insère les nouvelles offres et rafraîchit les autres. Renvoie le nombre de nouvelles.

    Les champs gérés par l'utilisateur (statut, lu, favori, archivé, notes,
    brouillon) ne sont jamais modifiés ici.
    """
    now = utcnow_iso()
    new_count = 0
    for offer in offers:
        row = conn.execute(
            "SELECT id FROM offers WHERE source = ? AND source_id = ?",
            (offer.source, offer.source_id),
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO offers (source, source_id, url, title, company, location, contract,
                                       logo_url, published_at, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (offer.source, offer.source_id, offer.url, offer.title, offer.company,
                 offer.location, offer.contract, offer.logo_url, offer.published_at, now, now),
            )
            new_count += 1
        else:
            conn.execute(
                """UPDATE offers SET url = ?, title = ?, company = ?, location = ?, contract = ?,
                          logo_url = ?, published_at = COALESCE(?, published_at), last_seen_at = ?
                   WHERE id = ?""",
                (offer.url, offer.title, offer.company, offer.location, offer.contract,
                 offer.logo_url, offer.published_at, now, row["id"]),
            )
    return new_count


def list_offers(
    conn: sqlite3.Connection,
    *,
    folder: str = "inbox",
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = []
    args: list[Any] = []
    if folder == "inbox":
        where.append("is_archived = 0")
    elif folder == "unread":
        where.append("is_archived = 0 AND is_read = 0")
    elif folder == "starred":
        where.append("is_starred = 1")
    elif folder == "archived":
        where.append("is_archived = 1")
    if status:
        where.append("status = ?")
        args.append(status)
    if source:
        where.append("source = ?")
        args.append(source)
    for term in normalize(q or "").split():
        where.append(
            "norm(title || ' ' || company || ' ' || location || ' ' || contract) LIKE ? ESCAPE '\\'"
        )
        args.append(f"%{_escape_like(term)}%")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM offers {clause}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT {LIST_COLUMNS} FROM offers {clause} {SORT_ORDER} LIMIT ? OFFSET ?",
        [*args, page_size, (page - 1) * page_size],
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def counts(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(is_archived = 0), 0) AS inbox,
                  COALESCE(SUM(is_archived = 0 AND is_read = 0), 0) AS unread,
                  COALESCE(SUM(is_starred = 1), 0) AS starred,
                  COALESCE(SUM(is_archived = 1), 0) AS archived
           FROM offers"""
    ).fetchone()
    by_status = {s: 0 for s in config.STATUSES}
    for r in conn.execute("SELECT status, COUNT(*) AS n FROM offers GROUP BY status"):
        by_status[r["status"]] = r["n"]
    by_source: dict[str, dict[str, int]] = {}
    for r in conn.execute(
        """SELECT source, COUNT(*) AS n, COALESCE(SUM(is_read = 0), 0) AS unread
           FROM offers WHERE is_archived = 0 GROUP BY source"""
    ):
        by_source[r["source"]] = {"total": r["n"], "unread": r["unread"]}
    return {**dict(row), "by_status": by_status, "by_source": by_source}


def get_offer(conn: sqlite3.Connection, offer_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
    if row is None:
        return None
    data = _row_to_dict(row)
    data["has_draft"] = bool(data["draft_body"])
    data["has_notes"] = bool(data["notes"])
    data["emails"] = list_emails(conn, offer_id)
    data["emails_count"] = len(data["emails"])
    return data


def find_offer_id(conn: sqlite3.Connection, source: str, source_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM offers WHERE source = ? AND source_id = ?", (source, source_id)
    ).fetchone()
    return row["id"] if row else None


def update_offers(conn: sqlite3.Connection, ids: list[int], changes: dict[str, Any]) -> int:
    """Applique ``changes`` (champs autorisés uniquement) aux offres ``ids``."""
    fields = {k: v for k, v in changes.items() if k in _PATCHABLE and v is not None}
    if not fields or not ids:
        return 0
    sets = [f"{k} = ?" for k in fields]
    args: list[Any] = [int(v) if isinstance(v, bool) else v for v in fields.values()]
    if "status" in fields:
        # SQLite évalue tout le SET avec les valeurs d'avant la mise à jour : la date
        # n'est donc changée que si le statut change réellement.
        sets.append("status_changed_at = CASE WHEN status = ? THEN status_changed_at ELSE ? END")
        args += [fields["status"], utcnow_iso()]
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE offers SET {', '.join(sets)} WHERE id IN ({placeholders})", [*args, *ids]
    )
    return cur.rowcount


def insert_manual_offer(conn: sqlite3.Connection, offer: Any, description: str | None) -> int:
    now = utcnow_iso()
    cur = conn.execute(
        """INSERT INTO offers (source, source_id, url, title, company, location, contract, logo_url,
                               published_at, first_seen_at, last_seen_at, description,
                               details_fetched_at, is_read)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (offer.source, offer.source_id, offer.url, offer.title, offer.company, offer.location,
         offer.contract, offer.logo_url, offer.published_at, now, now,
         description or None, now if description else None),
    )
    return int(cur.lastrowid)


def delete_offer(conn: sqlite3.Connection, offer_id: int) -> bool:
    return conn.execute("DELETE FROM offers WHERE id = ?", (offer_id,)).rowcount > 0


def set_details(
    conn: sqlite3.Connection, offer_id: int, description: str, emails: list[str]
) -> None:
    conn.execute(
        """UPDATE offers SET description = ?, contact_emails = ?, details_fetched_at = ?
           WHERE id = ?""",
        (description, json.dumps(emails), utcnow_iso(), offer_id),
    )


def save_draft(conn: sqlite3.Connection, offer_id: int, subject: str, body: str) -> None:
    conn.execute(
        "UPDATE offers SET draft_subject = ?, draft_body = ? WHERE id = ?",
        (subject, body, offer_id),
    )


# --------------------------------------------------------------------------- emails


def list_emails(conn: sqlite3.Connection, offer_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM emails WHERE offer_id = ? ORDER BY sent_at DESC, id DESC", (offer_id,)
    ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        try:
            item["attachments"] = json.loads(item["attachments"] or "[]")
        except ValueError:
            item["attachments"] = []
        result.append(item)
    return result


def record_sent_email(
    conn: sqlite3.Connection,
    offer_id: int,
    *,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[str],
    message_id: str,
) -> None:
    """Archive l'email envoyé, vide le brouillon et passe l'offre en « envoyée »."""
    now = utcnow_iso()
    conn.execute(
        """INSERT INTO emails (offer_id, to_addr, subject, body, attachments, message_id, sent_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (offer_id, to_addr, subject, body, json.dumps(attachments, ensure_ascii=False),
         message_id, now),
    )
    # Un email de relance ne doit pas faire régresser un statut « réponse » ou « entretien ».
    conn.execute(
        """UPDATE offers
           SET status_changed_at = CASE WHEN status = 'new' THEN ? ELSE status_changed_at END,
               status = CASE WHEN status = 'new' THEN 'sent' ELSE status END,
               draft_to = ?, draft_subject = '', draft_body = '', is_read = 1
           WHERE id = ?""",
        (now, to_addr, offer_id),
    )


# --------------------------------------------------------------------------- paramètres


def load_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    settings = json.loads(json.dumps(config.DEFAULT_SETTINGS))  # copie profonde
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in settings:
            try:
                settings[row["key"]] = json.loads(row["value"])
            except ValueError:
                pass
    return settings


def save_settings(conn: sqlite3.Connection, changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        if key not in config.DEFAULT_SETTINGS:
            continue
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
