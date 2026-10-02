"""Chemins et valeurs par défaut de l'application."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("IA_DATA_DIR", BASE_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "app.db"
DOCUMENTS_DIR = DATA_DIR / "documents"

HOST = os.environ.get("IA_HOST", "127.0.0.1")
PORT = int(os.environ.get("IA_PORT", "8000"))

# Statuts de suivi d'une candidature (l'ordre est celui affiché dans l'interface).
STATUSES = ("new", "sent", "answered", "refused", "interview")

# Sources interrogées automatiquement. "manual" = offre ajoutée à la main.
SEARCH_SOURCES = ("hellowork", "linkedin", "wttj")
SOURCE_LABELS = {
    "hellowork": "HelloWork",
    "linkedin": "LinkedIn",
    "wttj": "Welcome to the Jungle",
    "manual": "Manuel",
}

RECENCY_CHOICES = ("day", "3days", "week", "month", "all")

# Paramètres modifiables depuis l'interface (stockés en base, table settings).
DEFAULT_SETTINGS: dict[str, object] = {
    # --- Profil du candidat (utilisé par l'IA) ---
    "full_name": "",
    "email": "",
    "phone": "",
    "links": "",
    "education": "",
    "internship": "",
    "cv_text": "",
    "signature": "",
    # --- IA (serveur llama.cpp, API compatible OpenAI) ---
    "llm_base_url": "http://127.0.0.1:8080",
    "llm_api_key": "",
    "llm_model": "",
    "llm_temperature": 0.7,
    "llm_max_tokens": 1200,
    "llm_disable_thinking": True,
    "llm_language": "fr",
    "llm_extra_instructions": "",
    # --- Envoi des emails (SMTP) ---
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from_name": "",
    "smtp_from_email": "",
    "smtp_bcc_self": False,
    # --- Recherche d'offres ---
    "search_keywords": "",
    "search_location": "France",
    "search_sources": list(SEARCH_SOURCES),
    "search_recency": "week",
    "search_max_per_source": 60,
}

# Jamais renvoyés au navigateur : l'API indique seulement s'ils sont renseignés.
SECRET_SETTINGS = ("smtp_password", "llm_api_key")

# Identifiants publics (lecture seule) utilisés par le site Welcome to the Jungle
# pour sa recherche Algolia. Surchargeables si le site les change.
WTTJ_ALGOLIA_APP_ID = os.environ.get("WTTJ_ALGOLIA_APP_ID", "CSEKHVMS53")
WTTJ_ALGOLIA_API_KEY = os.environ.get("WTTJ_ALGOLIA_API_KEY", "4bd8f6215d0cc52b26430765769e65a0")
