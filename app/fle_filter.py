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
# LinkedIn ignore son propre filtre « stage » (f_JT=I) : la requête exige donc un mot de
# stage, sinon elle ne ramène que des postes d'enseignant.
LINKEDIN_QUERY = (
    '("French teacher" OR "French teaching" OR "French tutor" OR "French language" OR FLE '
    'OR "français langue étrangère" OR "enseignement du français" OR "Alliance Française") '
    'AND (internship OR intern OR stage OR stagiaire OR praktik OR praktikant OR harjoittelu '
    'OR stagiair OR trainee OR "co-op")'
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


# Mots qui signent un titre français (« de », « en » sont aussi néerlandais ou suédois).
_FRENCH_MARKERS = re.compile(
    r"\b(stage|stagiaire|enseignant|enseignante|professeur|professeure|formateur|formatrice|"
    r"francais|francaise|ecole|lycee|universite|candidature|spontanee|h/f|f/h|alternance|"
    r"assistant de langue|responsable|coordinateur|coordinatrice)\b"
)
_FRENCH_WORDS = re.compile(r"\b(des|du|la|le|les|et|pour|au|aux|une|avec|dans)\b")


def looks_french(text: str | None) -> bool:
    folded = _fold(text)
    if not folded:
        return True
    return bool(_FRENCH_MARKERS.search(folded)) or len(_FRENCH_WORDS.findall(folded)) >= 2


# --------------------------------------------------------------------------- stage ou emploi

# Un stage se reconnaît à son intitulé ou à son type de contrat, dans les langues des pays
# visés. « stage » anglais (« early stage », « stage manager ») est écarté.
_INTERNSHIP = re.compile(
    r"\bstages?\b(?! manager| technician| hand)|\bstagiaires?\b|\b(w|s)?internships?\b|\bintern\b|"
    r"\binterns\b|\btrainees?(ship)?\b|\bco-?op\b|\bpraktikant(er|en)?\b|\bpraktikplats(er)?\b|"
    r"\bpraksisplass(er)?\b|\bpraksisstudent\b|\bharjoittel(u|ija|ijat)\b|\bharjoitteluun\b|"
    r"\bstagiair(e|s)?\b|\bpraktikum\b|\bwork placement\b|\bplacement (etudiant|student)\b"
)
# « praktik » (suédois, danois) et « praksis » (norvégien) veulent aussi dire « cabinet » ou
# « pratique » : indice faible, annulé par un contrat d'emploi.
_WEAK_INTERNSHIP = re.compile(r"\bpraktik\b|\bpraksis\b")
# Contrat d'emploi annoncé par la plateforme (Platsbanken, NAV, EURES…).
_JOB_CONTRACT = re.compile(
    r"tillsvidare|vanlig anstallning|provanstallning|\bvikariat\b|\bfast( stilling| anstallning)?\b|"
    r"emploi permanent|\bremplacement\b|\bemploi\b(?! etudiant)|\bpermanent\b|\bcdi\b|\bcdd\b|"
    r"full[- ]time|\bcontrat( temporaire| puis embauche)?\b|\bsaisonnier\b|\bsur appel\b"
)
_NOT_INTERNSHIP = re.compile(r"\b(early|late|growth|seed|multi|back|front|next)[- ]stages?\b")
# Dans une description : type de contrat annoncé, ou convention de stage.
_INTERNSHIP_BODY = re.compile(
    r"(type d.emploi|employment type|type de contrat|contract type) ?: ?(stage|internship)|"
    r"convention de stage|internship agreement|stage de fin d.etudes|stage de \d+ ?(a \d+ )?mois"
)


# Titre d'une offre d'emploi : un « Type d'emploi : Stage » dans la description (que
# LinkedIn attribue parfois à des remplacements) ne suffit pas à en faire un stage.
_JOB_TITLE = re.compile(
    r"\b\d+([.,]\d+)? ?fte\b|\bfte\b|\blto\b|long[- ]term occasional|\boccasional teacher\b|"
    r"\bpermanent\b|\bfull[- ]time\b|\bpart[- ]time\b|\bcdi\b|\bcdd\b|\btillsvidare\b|"
    r"\bfast anstallning\b|\bvikariat\b|\bfast stilling\b|\bvaste? (baan|aanstelling)\b|"
    r"\bvakituinen\b|\bsijaisuus\b|\bterm (position|assignment)\b|\b100% term\b"
)


def _internship_words(text: str) -> int:
    return len(_INTERNSHIP.findall(_NOT_INTERNSHIP.sub(" ", text)))


def is_internship(title: str | None, contract: str | None = "", description: str | None = None) -> bool:
    """Vrai pour un stage, faux pour une offre d'emploi (CDI, CDD, poste d'enseignant…)."""
    folded_title = _fold(title)
    head = f"{folded_title} | {_fold(contract)}"
    if _internship_words(head):
        return True
    if _JOB_TITLE.search(folded_title) or _JOB_CONTRACT.search(_fold(contract)):
        return False
    if _WEAK_INTERNSHIP.search(folded_title):
        return True
    body = _fold(description)
    return bool(body) and (bool(_INTERNSHIP_BODY.search(body)) or _internship_words(body) >= 3)


# --------------------------------------------------------------------------- lieux

# HelloWork et Welcome to the Jungle ne publient que des offres en France : un lieu
# étranger y est remplacé par « toute la France » au lieu de ne rien trouver.
FRANCE_ONLY_SOURCES = ("hellowork", "wttj")
_FOREIGN_PLACES = {
    _fold(name) for name in (
        "Sweden", "Suède", "Sverige", "Norway", "Norvège", "Norge", "Denmark", "Danemark", "Danmark",
        "Finland", "Finlande", "Suomi", "Iceland", "Islande", "Ísland", "Estonia", "Estonie", "Latvia",
        "Lettonie", "Lithuania", "Lituanie", "Netherlands", "Pays-Bas", "Nederland", "Belgium", "Belgique",
        "Ireland", "Irlande", "United Kingdom", "Royaume-Uni", "UK", "Scotland", "Écosse", "Canada",
        "Québec", "Quebec", "Ontario", "Germany", "Allemagne", "Nordics", "Scandinavia", "Scandinavie",
        "Europe", "Stockholm", "Oslo", "Copenhagen", "Copenhague", "Helsinki", "Reykjavik", "Tallinn",
        "Riga", "Vilnius", "Amsterdam", "Dublin", "London", "Londres", "Montréal", "Montreal", "Toronto",
        "Vancouver", "Ottawa",
    )
}


def is_foreign_place(location: str) -> bool:
    first = _fold(location).split(",")[0].strip()
    return first in _FOREIGN_PLACES


def locations_for(source: str, value: str | None) -> list[str]:
    """Lieux à interroger sur ``source`` ([] = la plateforme ne couvre aucun lieu demandé).

    Les sites français ignorent les lieux étrangers, EURES reçoit en une fois les codes
    de tous les pays européens demandés, et les plateformes nationales ne sont
    interrogées que si leur pays figure dans la liste (lieu vide = tous les pays).
    """
    locations = split_locations(value)
    anywhere = locations == [""]
    if source == "eures":
        codes = list(dict.fromkeys(c for loc in locations for c in country_codes(loc)))
        if anywhere:
            codes = list(NORDICS + ("ee", "lv", "lt", "nl", "ie"))
        return [",".join(codes)] if codes else []
    if source == "jobtech":
        return ["Sverige"] if anywhere or any("se" in country_codes(loc) for loc in locations) else []
    if source == "nav":
        return ["Norge"] if anywhere or any("no" in country_codes(loc) for loc in locations) else []
    if source == "jobbank":
        return ["Canada"] if anywhere or any(is_canada(loc) for loc in locations) else []
    if source not in FRANCE_ONLY_SOURCES:
        return locations
    french = [loc for loc in locations if loc and not is_foreign_place(loc)]
    return french or ["France"]


# --------------------------------------------------------------------------- plateformes nationales

# Requêtes propres à chaque plateforme (filtre FLE actif, aucun mot-clé saisi). EURES
# exige tous les mots d'une requête ; les autres font une recherche plein texte.
SOURCE_QUERIES: dict[str, tuple[str, ...]] = {
    "eures": (
        "intern french", "internship french", "trainee french", "stagiaire français", "stage FLE",
        "praktikant franska", "praktik franska", "praksis fransk", "praktikant fransk",
        "stagiair frans", "harjoittelija ranska", "harjoittelu ranska",
    ),
    "jobtech": ("praktik franska", "praktikant franska", "praktik franskundervisning", "French intern"),
    "nav": ("praksis fransk", "praktikant fransk", "trainee fransk", "French intern"),
    "jobbank": ("stagiaire français", "stagiaire FLE", "french intern", "french tutor intern", "stagiaire enseignement"),
}

# Codes pays EURES (UE + EEE). Le Canada et le Royaume-Uni n'y sont pas.
EURES_COUNTRIES = {
    "se": ("sweden", "suede", "sverige", "stockholm", "goteborg", "gothenburg", "malmo", "uppsala", "lund"),
    "no": ("norway", "norvege", "norge", "oslo", "bergen", "trondheim", "stavanger", "tromso"),
    "dk": ("denmark", "danemark", "danmark", "copenhagen", "copenhague", "kobenhavn", "aarhus", "odense"),
    "fi": ("finland", "finlande", "suomi", "helsinki", "tampere", "turku", "oulu", "jyvaskyla"),
    "is": ("iceland", "islande", "island", "reykjavik"),
    "ee": ("estonia", "estonie", "eesti", "tallinn", "tartu"),
    "lv": ("latvia", "lettonie", "latvija", "riga"),
    "lt": ("lithuania", "lituanie", "lietuva", "vilnius", "kaunas"),
    "nl": ("netherlands", "pays-bas", "nederland", "holland", "amsterdam", "rotterdam", "utrecht", "la haye", "den haag", "the hague"),
    "ie": ("ireland", "irlande", "eire", "dublin", "cork", "galway"),
    "be": ("belgium", "belgique", "belgie", "bruxelles", "brussels", "anvers", "antwerpen", "gand", "gent"),
    "de": ("germany", "allemagne", "deutschland", "berlin", "hambourg", "hamburg"),
    "lu": ("luxembourg",),
}
NORDICS = ("se", "no", "dk", "fi", "is")
_CANADA = ("canada", "quebec", "ontario", "montreal", "toronto", "vancouver", "ottawa", "calgary", "edmonton",
           "winnipeg", "halifax", "moncton", "victoria", "colombie-britannique", "british columbia", "alberta",
           "manitoba", "nouvelle-ecosse", "nova scotia", "nouveau-brunswick", "new brunswick")
_GROUPS = {"nordics": NORDICS, "scandinavia": ("se", "no", "dk"), "scandinavie": ("se", "no", "dk"),
           "pays nordiques": NORDICS, "baltics": ("ee", "lv", "lt"), "pays baltes": ("ee", "lv", "lt"),
           "europe": tuple(EURES_COUNTRIES), "europe du nord": NORDICS + ("ee", "lv", "lt", "nl", "ie")}


def country_codes(location: str) -> tuple[str, ...]:
    """Codes pays EURES d'un lieu (« Sweden » -> ("se",), « Nordics » -> 5 codes)."""
    first = _fold(location).split(",")[0].strip()
    if first in _GROUPS:
        return _GROUPS[first]
    return tuple(code for code, names in EURES_COUNTRIES.items() if first in names)


def is_canada(location: str) -> bool:
    return any(part.strip() in _CANADA for part in _fold(location).split(","))
