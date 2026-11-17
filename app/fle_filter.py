"""Repérage des offres de stage en FLE (français langue étrangère).

Chaque offre reçoit une liste de « thèmes FLE » (enseignement, assistanat de langue,
réseau Alliance / Institut français…) déduite de son titre et, quand elle est connue, de
sa description. Une offre sans thème n'est pas une offre FLE : elle est écartée de la
recherche automatique et masquée de la liste quand le filtre « stages FLE uniquement »
est actif.

Les offres visées sont souvent publiées en anglais ou dans la langue du pays (Suède,
Norvège, Danemark, Finlande, Islande, Pays-Bas, Canada…) : les motifs couvrent donc aussi
les formulations anglaises et quelques formulations nordiques et néerlandaises.
"""

from __future__ import annotations

import re
import unicodedata

# Requêtes lancées sur HelloWork et Welcome to the Jungle quand le filtre FLE est actif et
# qu'aucun mot-clé n'est saisi (ces sites ne font qu'une recherche plein texte, sans « OR »).
DEFAULT_QUERIES = (
    "FLE",
    "français langue étrangère",
    "enseignant de français",
)

# LinkedIn comprend « OR » et les guillemets : une seule requête par lieu suffit, ce qui
# divise par quatre le nombre d'appels (LinkedIn limite vite les requêtes sans compte).
# « FLE » seul est exclu : LinkedIn y associe « Fleet » (gestion de flotte).
LINKEDIN_QUERY = (
    '"French teacher" OR "French tutor" OR "French instructor" OR "French language assistant" '
    'OR "français langue étrangère" OR "enseignant de français" OR "professeur de français"'
)

# Lieux proposés par défaut : pays nordiques, Europe du Nord, Canada. Séparés par « ; »
# dans le réglage « Lieu » (les noms anglais sont ceux que LinkedIn reconnaît le mieux).
DEFAULT_LOCATIONS = (
    "Sweden", "Norway", "Denmark", "Finland", "Iceland",
    "Estonia", "Netherlands", "Ireland", "Canada",
)

_FRENCH = r"(french|francais|fransk|franska|franske|ranska[n]?|frans|franse|franzosisch)"
_TEACH = (
    r"(teacher|teaching|tutor|tutoring|instructor|lecturer|educator|tuteur|tutrice|"
    r"enseignant|enseignante|professeur|professeure|formateur|formatrice|animateur|animatrice|"
    r"larare|laerer|laerar|opettaja|docent|leraar|lehrer)"
)

# (thème, motifs) sur du texte en minuscules sans accents. L'ordre est celui d'affichage :
# du plus spécifique au plus général.
TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("FLE / FLS", (
        r"\bfle\b", r"\bfls\b", r"\bflsco\b", r"\bfsl\b",
        r"francais langue (etrangere|seconde)", r"francais sur objectifs? specifiques?",
        r"french as a (foreign|second) language",
    )),
    ("Enseignement du français", (
        rf"\b{_TEACH} (of |de |d'|i |av |in |en |du )?{_FRENCH}\b",
        rf"\b{_FRENCH} (language )?{_TEACH}",
        rf"\b{_FRENCH}(larare|laerer|laerar|leraar|lehrer|opettaja)\b",
        r"enseignement du francais", r"cours de francais", r"teach(ing)? french",
        r"french (language )?(courses?|classes?|lessons?|tuition)",
        r"\bfranskundervisning\b", r"\bundervisning i franska\b",
    )),
    ("Assistanat de langue", (
        r"assistant(e)? de (langue|francais)", r"(french )?language assistant",
        rf"\b{_FRENCH} (language )?(teaching )?assistant", r"lect(eur|rice) de francais",
        r"\bfrench lector\b", r"\bteaching assistant\b.{0,40}\bfrench\b",
    )),
    ("Alliance / Institut français", (
        r"alliances? francaises?", r"institut francais", r"lycee francais", r"ecole francaise",
        r"french (international )?school", r"french institute", r"\baefe\b", r"\bmlf\b",
    )),
    ("Certifications", (
        r"\bdelf\b", r"\bdalf\b", r"\btcf\b", r"\btef\b", r"examinat(eur|rice|or) delf",
    )),
    ("Didactique & pédagogie", (
        r"didactique", r"ingenierie (pedagogique|de formation)", r"conception pedagogique",
        r"materiel pedagogique", r"pedagog(ie|ique) (du |en )?(fle|francais)",
        r"(curriculum|instructional) design.{0,40}french", r"french.{0,40}(curriculum|instructional) design",
    )),
    ("Immersion", (
        r"immersion (francaise|en francais)", r"french immersion", r"camp (de|en) francais",
        r"french (summer )?camp", r"sejours? linguistiques?",
    )),
    ("Langue française", (
        r"langue francaise", r"french language", r"francophon", r"\bfrancais\b", r"\bfrench\b",
    )),
)

_COMPILED = [(topic, re.compile("|".join(patterns))) for topic, patterns in TOPICS]

# « Langue française » est un thème large : dans un titre, « French » ou « français »
# seuls ne suffisent pas (« Customer service French speaker » n'est pas un stage FLE) ;
# il n'est retenu qu'avec un mot d'enseignement à proximité, et sert seulement quand
# aucun thème plus précis n'a été trouvé.
_LOOSE_TOPIC = "Langue française"
_STRICT_LOOSE = re.compile(
    rf"({_FRENCH}|langue francaise|francophon).{{0,40}}{_TEACH}|{_TEACH}.{{0,40}}({_FRENCH}|langue francaise)"
)

_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "’": "'", "ø": "o", "Ø": "O"})


def _fold(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.translate(_LIGATURES))
    folded = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", folded)


def _topics_in(text: str) -> list[str]:
    found = []
    for topic, regex in _COMPILED:
        if topic == _LOOSE_TOPIC:
            if _STRICT_LOOSE.search(text):
                found.append(topic)
        elif regex.search(text):
            found.append(topic)
    return found


def _hits(topic: str, text: str) -> int:
    regex = _STRICT_LOOSE if topic == _LOOSE_TOPIC else dict(_COMPILED)[topic]
    return len(regex.findall(text))


def detect_topics(title: str | None, description: str | None = None) -> list[str]:
    """Thèmes FLE d'une offre (liste vide = pas une offre FLE).

    Le titre fait foi ; la description n'ajoute des thèmes que si elle en parle au moins
    deux fois, pour ne pas retenir une offre de commerce qui demande « le français » en passant.
    """
    topics = _topics_in(_fold(title))
    body = _fold(description)
    if body:
        for topic in _topics_in(body):
            if topic not in topics and (topics or _hits(topic, body) >= 2):
                topics.append(topic)
    order = [t for t, _ in TOPICS]
    topics = sorted(set(topics), key=order.index)
    # « Langue française » seul ne sert que si aucun thème plus précis n'a été trouvé.
    return [t for t in topics if t != _LOOSE_TOPIC] or topics


def is_fle_offer(title: str | None, description: str | None = None) -> bool:
    return bool(detect_topics(title, description))


def split_locations(value: str | None) -> list[str]:
    """« Sweden; Norway ; Canada » -> ["Sweden", "Norway", "Canada"] (sans doublons)."""
    parts = [p.strip() for p in re.split(r"[;\n]", value or "")]
    return list(dict.fromkeys(p for p in parts if p)) or [""]
