"""Zone géographique du profil FLE : Europe ou Canada.

``region_of`` classe le lieu d'une offre (« Montréal, QC », « Berlin, Germany »,
« Lyon - 69 », « New York, NY »…). Un lieu inconnu (« Télétravail », simple nom de ville
française) est accepté : les recherches sont déjà limitées à l'Europe et au Canada, ce
filtre écarte seulement les offres manifestement situées ailleurs.
"""

from __future__ import annotations

import re

from app.ai_filter import fold

# Pays des bureaux interrogés sur Welcome to the Jungle (ISO 3166-1 alpha-2).
EUROPE_COUNTRY_CODES = (
    "FR", "BE", "CH", "LU", "MC", "DE", "ES", "IT", "PT", "GB", "IE", "NL", "AT", "PL", "CZ", "SK",
    "HU", "RO", "BG", "GR", "HR", "SI", "DK", "SE", "NO", "FI", "IS", "EE", "LV", "LT", "MT", "CY",
    "AD", "LI", "RS", "BA", "AL", "MK", "ME", "UA", "MD",
)
WTTJ_COUNTRIES = (*EUROPE_COUNTRY_CODES, "CA")

# Lieux interrogés sur LinkedIn quand le champ « Lieu » est vide.
LINKEDIN_LOCATIONS = ("European Union", "Switzerland", "United Kingdom", "Canada")

_EUROPE = (
    "europe", "union europeenne", "european union", "france", "belgique", "belgium", "belgie",
    "suisse", "switzerland", "schweiz", "svizzera", "luxembourg", "monaco", "allemagne", "germany",
    "deutschland", "espagne", "spain", "espana", "italie", "italy", "italia", "portugal",
    "royaume-uni", "royaume uni", "united kingdom", "angleterre", "england", "ecosse", "scotland",
    "pays de galles", "wales", "irlande", "ireland", "pays-bas", "pays bas", "netherlands",
    "nederland", "autriche", "austria", "osterreich", "pologne", "poland", "polska",
    "republique tcheque", "tchequie", "czech", "czechia", "slovaquie", "slovakia", "hongrie",
    "hungary", "roumanie", "romania", "bulgarie", "bulgaria", "grece", "greece", "croatie",
    "croatia", "slovenie", "slovenia", "danemark", "denmark", "suede", "sweden", "norvege",
    "norway", "finlande", "finland", "islande", "iceland", "estonie", "estonia", "lettonie",
    "latvia", "lituanie", "lithuania", "malte", "malta", "chypre", "cyprus", "andorre", "andorra",
    "liechtenstein", "serbie", "serbia", "bosnie", "bosnia", "albanie", "albania", "macedoine",
    "macedonia", "montenegro", "ukraine", "moldavie", "moldova",
    # grandes villes européennes souvent citées sans le pays
    "paris", "bruxelles", "brussels", "geneve", "geneva", "lausanne", "zurich", "berlin",
    "munich", "madrid", "barcelone", "barcelona", "rome", "milan", "lisbonne", "lisbon", "londres",
    "london", "dublin", "amsterdam", "vienne", "vienna", "varsovie", "warsaw", "prague",
    "budapest", "bucarest", "bucharest", "athenes", "athens", "copenhague", "copenhagen",
    "stockholm", "oslo", "helsinki",
)
_CANADA = (
    "canada", "quebec", "ontario", "colombie-britannique", "colombie britannique",
    "british columbia", "alberta", "manitoba", "saskatchewan", "nouveau-brunswick",
    "new brunswick", "nouvelle-ecosse", "nova scotia", "terre-neuve", "newfoundland",
    "ile-du-prince-edouard", "prince edward island", "yukon", "nunavut", "territoires du nord-ouest",
    "northwest territories", "montreal", "toronto", "vancouver", "ottawa", "gatineau", "calgary",
    "edmonton", "winnipeg", "halifax", "moncton", "sherbrooke", "trois-rivieres", "saguenay",
    "longueuil", "regina", "saskatoon",
)
_ELSEWHERE = (
    "etats-unis", "etats unis", "united states", "usa", "u.s.", "amerique", "america", "mexique",
    "mexico", "bresil", "brazil", "argentine", "argentina", "chili", "chile", "colombie", "colombia",
    "perou", "peru", "chine", "china", "japon", "japan", "coree", "korea", "inde", "india",
    "australie", "australia", "nouvelle-zelande", "new zealand", "singapour", "singapore",
    "hong kong", "vietnam", "viet nam", "thailande", "thailand", "indonesie", "indonesia",
    "philippines", "maroc", "morocco", "tunisie", "tunisia", "algerie", "algeria", "senegal",
    "cote d'ivoire", "ivory coast", "cameroun", "cameroon", "madagascar", "egypte", "egypt",
    "liban", "lebanon", "emirats", "emirates", "dubai", "qatar", "arabie", "saudi", "israel",
    "afrique du sud", "south africa", "nigeria", "kenya", "maurice", "mauritius",
    "la reunion", "reunion", "guadeloupe", "martinique", "guyane", "mayotte", "nouvelle-caledonie",
    "polynesie", "tahiti", "saint-pierre-et-miquelon",
)

_CANADA_CODES = r"QC|ON|BC|AB|MB|SK|NB|NS|NL|PE|YT|NT|NU"
_US_CODES = (
    r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ"
    r"|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
_CANADA_CODE_RE = re.compile(rf",\s*({_CANADA_CODES})\b")
_US_CODE_RE = re.compile(rf",\s*({_US_CODES})(\s*,|\s*$)")
_OVERSEAS_DEPT_RE = re.compile(r"\b97[1-8]\b")  # départements et collectivités d'outre-mer


def _words(names: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in names) + r")(?![\w-])")


_EUROPE_RE, _CANADA_RE, _ELSEWHERE_RE = _words(_EUROPE), _words(_CANADA), _words(_ELSEWHERE)


def region_of(location: str | None) -> str | None:
    """« canada », « europe », « other » (ailleurs) ou None (lieu inconnu)."""
    raw = (location or "").strip()
    if not raw:
        return None
    text = fold(raw)
    if _CANADA_RE.search(text) or _CANADA_CODE_RE.search(raw):
        return "canada"
    if _OVERSEAS_DEPT_RE.search(text) or _US_CODE_RE.search(raw):
        return "other"
    if _EUROPE_RE.search(text):
        return "europe"
    if _ELSEWHERE_RE.search(text):
        return "other"
    return None


def region_ok(location: str | None) -> bool:
    return region_of(location) != "other"
