"""Documents joints aux candidatures (CV, lettre…) stockés dans data/documents."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from . import config

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".png", ".jpg", ".jpeg"}
MAX_SIZE = 10 * 1024 * 1024  # 10 Mo


class DocumentError(Exception):
    """Erreur affichable à l'utilisateur."""


def safe_name(name: str) -> str:
    name = unicodedata.normalize("NFC", Path(str(name).replace("\\", "/")).name).strip()
    name = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "_", name)
    if not name or name.startswith("."):
        raise DocumentError("Nom de fichier invalide.")
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise DocumentError(f"Type de fichier non accepté. Formats possibles : {allowed}")
    if len(name) > 150:
        stem, suffix = Path(name).stem, Path(name).suffix
        name = stem[: 150 - len(suffix)] + suffix
    return name


def resolve(name: str) -> Path:
    """Chemin d'un document existant (protégé contre « ../ »)."""
    path = (config.DOCUMENTS_DIR / safe_name(name)).resolve()
    if path.parent != config.DOCUMENTS_DIR.resolve() or not path.is_file():
        raise DocumentError(f"Document introuvable : {name}")
    return path


def list_documents() -> list[dict[str, Any]]:
    config.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    docs = []
    for path in sorted(config.DOCUMENTS_DIR.iterdir(), key=lambda p: p.name.lower()):
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS and not path.name.startswith("."):
            docs.append({"name": path.name, "size": path.stat().st_size})
    return docs


def save_document(name: str, data: bytes) -> dict[str, Any]:
    name = safe_name(name)
    if not data:
        raise DocumentError("Le fichier est vide.")
    if len(data) > MAX_SIZE:
        raise DocumentError("Fichier trop volumineux (10 Mo maximum).")
    config.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.DOCUMENTS_DIR / name
    tmp = path.with_name(f".{name}.part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return {"name": name, "size": len(data)}


def delete_document(name: str) -> None:
    resolve(name).unlink()


def extract_text(name: str) -> str:
    """Texte d'un CV (PDF ou .txt) pour aider l'IA à personnaliser les emails."""
    path = resolve(name)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace").strip()
    if suffix != ".pdf":
        raise DocumentError("Extraction possible uniquement depuis un PDF ou un fichier .txt.")
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise DocumentError(f"Impossible de lire ce PDF : {exc}") from exc
    text = "\n".join(pages)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise DocumentError("Aucun texte trouvé dans ce PDF (document scanné ?). Saisissez le texte à la main.")
    return text
