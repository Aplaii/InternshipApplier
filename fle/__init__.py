"""Deuxième modèle : stages en FLE / DIFLES (français langue étrangère et seconde),
en Europe ou au Canada. Lancement : ``fle/run.sh`` (voir fle/README.md)."""

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
    search_locations={"linkedin": regions.LINKEDIN_LOCATIONS},
    wttj_countries=regions.WTTJ_COUNTRIES,
    default_settings={"search_location": "", "ai_only": True},
    favicon=str(Path(__file__).with_name("favicon.svg")),
)
