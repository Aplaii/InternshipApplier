"""Profils de recherche : domaine de stage, zone géographique et textes de l'interface.

Le profil par défaut cherche des stages en intelligence artificielle en France. Un autre
profil se choisit avec la variable d'environnement ``IA_PROFILE`` : c'est le nom d'un
module Python qui expose ``PROFILE`` (ex. ``IA_PROFILE=fle`` pour le dossier ``fle/``).
Chaque profil a sa propre base de données (variable ``IA_DATA_DIR``, voir ``fle/run.sh``).

Le filtre de domaine réutilise les champs ``ai_topics`` (base) et ``ai_only`` (paramètre),
nommés d'après le premier profil : ils désignent les thèmes du domaine et le filtre
« stages du domaine uniquement », quel que soit le profil.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import ai_filter


def _anywhere(_location: str) -> bool:
    return True


@dataclass(frozen=True)
class Profile:
    id: str
    # Textes de l'interface
    brand: str                      # nom affiché en haut à gauche et dans l'onglet
    domain_label: str               # « stages en IA »
    domain_short: str               # « IA » (thèmes IA, hors IA…)
    filter_label: str               # case à cocher du filtre
    keywords_placeholder: str
    location_placeholder: str
    # Détection du domaine
    detect_topics: Callable[[str | None, str | None], list[str]]
    topics: tuple[str, ...]         # ordre d'affichage des thèmes
    default_queries: tuple[str, ...]  # requêtes lancées quand aucun mot-clé n'est saisi
    # Zone géographique
    region_label: str = ""          # « Europe et Canada » (vide = pas de restriction)
    region_ok: Callable[[str], bool] = _anywhere
    # Lieux interrogés quand le champ « Lieu » est vide, par site (vide = lieu saisi).
    search_locations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Pays des bureaux pour Welcome to the Jungle (codes ISO 3166-1).
    wttj_countries: tuple[str, ...] = ("FR",)
    # Valeurs par défaut des paramètres propres au profil.
    default_settings: dict[str, Any] = field(default_factory=dict)
    favicon: str = ""               # chemin d'un favicon propre au profil (vide = celui de l'application)

    def public(self) -> dict[str, Any]:
        """Ce que l'interface doit savoir du profil."""
        return {
            "id": self.id,
            "brand": self.brand,
            "domain_label": self.domain_label,
            "domain_short": self.domain_short,
            "filter_label": self.filter_label,
            "keywords_placeholder": self.keywords_placeholder,
            "location_placeholder": self.location_placeholder,
            "region_label": self.region_label,
            "default_queries": list(self.default_queries),
        }


AI_PROFILE = Profile(
    id="ia",
    brand="Stages",
    domain_label="stages en IA",
    domain_short="IA",
    filter_label="Uniquement les stages en intelligence artificielle (IA, machine learning, data science, LLM, vision, NLP…)",
    keywords_placeholder="ex. LLM, vision par ordinateur, NLP (vide = tous les stages en IA)",
    location_placeholder="ex. Lyon, Paris, Bretagne (vide = toute la France)",
    detect_topics=ai_filter.detect_topics,
    topics=tuple(t for t, _ in ai_filter.TOPICS),
    default_queries=ai_filter.DEFAULT_QUERIES,
)

_current: Profile | None = None


def load(name: str) -> Profile:
    """Profil ``name`` (« » ou « ia » = profil IA par défaut, sinon module exposant PROFILE)."""
    if not name or name == AI_PROFILE.id:
        return AI_PROFILE
    module = importlib.import_module(name)
    profile = getattr(module, "PROFILE", None)
    if not isinstance(profile, Profile):
        raise RuntimeError(f"Le module {name!r} (IA_PROFILE) doit définir PROFILE = Profile(...).")
    return profile


def current() -> Profile:
    global _current
    if _current is None:
        _current = load(os.environ.get("IA_PROFILE", "").strip())
    return _current


def use(profile: Profile | None) -> None:
    """Change le profil actif (tests ; ``None`` = relire IA_PROFILE)."""
    global _current
    _current = profile
