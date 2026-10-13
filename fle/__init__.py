"""Deuxième modèle : stages en FLE / DIFLES (français langue étrangère et seconde),
en Europe ou au Canada. Lancement : ``fle/run.sh`` (voir fle/README.md)."""

import json
from pathlib import Path

from app.profiles import Profile

from . import regions, topics

PROFILE = Profile(
    id="fle",
    brand="Stages FLE",
    domain_label="stages en FLE / DIFLES",
    domain_short="FLE",
    filter_label=(
        "Uniquement les stages en FLE / DIFLES (français langue étrangère et seconde, FOS, "
        "didactique des langues, enseignement du français…)"
    ),
    keywords_placeholder="ex. FLE, professeur de français, francisation (vide = tous les stages FLE)",
    location_placeholder="ex. Bruxelles, Montréal, Espagne (vide = toute l'Europe et le Canada)",
    detect_topics=topics.detect_topics,
    topics=topics.TOPIC_ORDER,
    default_queries=topics.DEFAULT_QUERIES,
    region_label="Europe et Canada",
    region_ok=regions.region_ok,
    excluded=topics.excluded_offer,
    institutions=tuple(json.loads(Path(__file__).with_name("universites.json").read_text(encoding="utf-8"))),
    institutions_label="Universités",
    institutions_intro=(
        "Universités d'Europe et du Canada où l'on apprend le français comme langue étrangère ou "
        "seconde, sans suivre le cursus français (centres universitaires de FLE, centres de langues, "
        "départements de français). Les établissements à programme français (lycées français, AEFE, "
        "Mission laïque) n'y figurent pas. « Suivre » ajoute l'établissement à vos offres pour "
        "préparer une candidature spontanée."
    ),
    search_locations={"linkedin": regions.LINKEDIN_LOCATIONS},
    wttj_countries=regions.WTTJ_COUNTRIES,
    default_settings={"search_location": "", "ai_only": True},
    favicon=str(Path(__file__).with_name("favicon.svg")),
)
