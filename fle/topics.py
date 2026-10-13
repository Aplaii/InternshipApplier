"""Repérage des offres de stage en FLE / DIFLES (français langue étrangère et seconde).

Même principe que ``app/ai_filter.py`` : le titre fait foi ; la description n'ajoute des
thèmes que si le titre en avait déjà, ou si elle insiste (au moins deux mentions).
"""

from __future__ import annotations

import re

from app.ai_filter import fold

# Requêtes lancées sur chaque site quand aucun mot-clé n'est saisi.
DEFAULT_QUERIES = ("FLE", "français langue étrangère", "professeur de français", "French teacher")

_TEACHER = (
    r"profs?|professeure?s?|enseignante?s?|format(?:eur|rice)s?|animat(?:eur|rice)s?|tut(?:eur|rice)s?"
    r"|assistante?s?|lect(?:eur|rice)s?|charge(?:e)?s? de cours|intervenante?s?"
)

# (thème, motifs, qualifiant) sur du texte en minuscules sans accents. Un thème
# « non qualifiant » enrichit les étiquettes mais ne suffit pas à retenir l'offre.
TOPICS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("FLE", (
        r"\bfle\b", r"francais langue etrangere", r"french as a foreign language", r"\bdifles?\b",
        r"didactique du (francais|fle)\b", r"\bfrancais (pour|aux) (etrangers|allophones)\b",
        r"frances (como )?lengua extranjera", r"francese (come )?lingua straniera",
        r"franzosisch als fremdsprache",
    ), True),
    ("FLS / immersion", (
        r"\bfls\b", r"francais langue seconde", r"french as an? (second|additional) language", r"\bfsl\b",
        r"\bfrench immersion\b", r"\bimmersion (francaise|en francais)\b", r"\bfrancisation\b",
    ), True),
    ("FOS", (
        r"francais (sur|a) objectifs? specifiques?", r"\bfos\b", r"francais (des affaires|professionnel)",
    ), True),
    ("Intégration / alphabétisation", (
        r"francais langue d'?integration", r"\bfli\b", r"\balphabetisation\b",
        r"ateliers? sociolinguistiques?", r"\bfrancais (pour|aux) (migrants|refugies|primo[- ]arrivants)\b",
    ), True),
    ("Enseignement du français", (
        rf"\b({_TEACHER}) (de |d'|en )?(la )?(langue )?francaise?\b",
        r"\bfrench (language )?(teacher|tutor|instructor|teaching assistant|language assistant|lecturer)s?\b",
        r"\bteach(ing|er)? (of )?french\b", r"enseignement du francais", r"\bcours de francais\b",
        r"\bassistante? de langue\b",
        # offres rédigées dans d'autres langues européennes
        r"\bprofesora? de frances\b", r"\b(insegnante|docente) di francese\b",
        r"\bfranzosisch ?lehrer(in)?\b", r"\blehrer(in)? (fur )?franzosisch\b", r"\bdocent frans\b",
    ), True),
    ("Didactique des langues", (
        r"didactique (des|de la|en) langues?", r"\bdidactique\b", r"pedagogie (des|du) langues?",
        r"\bsciences du langage\b", r"\bplurilinguisme\b",
    ), True),
    ("Alliance / Institut français", (
        r"\balliances? francaises?\b", r"\binstituts? francais\b", r"\bcentres? culturels? francais\b",
    ), True),
    ("Ingénierie pédagogique", (
        r"ingenierie (pedagogique|de formation)", r"\bconcept(eur|rice) pedagogique\b",
        r"\bressources pedagogiques\b", r"\be-?learning\b",
    ), False),
)

_COMPILED = [(topic, re.compile("|".join(patterns)), qualifying) for topic, patterns, qualifying in TOPICS]
TOPIC_ORDER = tuple(topic for topic, _, _ in TOPICS)


def _find(text: str) -> dict[str, int]:
    return {topic: len(regex.findall(text)) for topic, regex, _ in _COMPILED if regex.search(text)}


def detect_topics(title: str | None, description: str | None = None) -> list[str]:
    """Thèmes FLE d'une offre (liste vide = pas une offre FLE / DIFLES)."""
    found = _find(fold(title))
    body = _find(fold(description))
    qualifying = {topic for topic, _, q in _COMPILED if q}
    title_ok = bool(found.keys() & qualifying)
    for topic, hits in body.items():
        if topic not in found and (title_ok or (topic in qualifying and hits >= 2)):
            found[topic] = hits
    if not found.keys() & qualifying:
        return []
    return [topic for topic in TOPIC_ORDER if topic in found]


def is_fle_offer(title: str | None, description: str | None = None) -> bool:
    return bool(detect_topics(title, description))
