"""Outils communs aux sources d'offres : modèles, client HTTP, dates et texte."""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup, NavigableString

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
}
RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class ScrapedOffer:
    source: str
    source_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    contract: str = ""
    logo_url: str = ""
    published_at: str | None = None  # ISO 8601 UTC, ex. 2026-10-01T08:38:39Z


@dataclass
class SearchParams:
    keywords: str = ""
    location: str = ""
    recency: str = "week"  # day | 3days | week | month | all
    max_results: int = 60


@dataclass
class OfferDetails:
    description: str = ""
    emails: list[str] = field(default_factory=list)
    title: str = ""
    company: str = ""
    location: str = ""
    contract: str = ""
    published_at: str | None = None


class SourceError(Exception):
    """Erreur affichable à l'utilisateur. ``partial`` contient les offres déjà récupérées."""

    def __init__(self, message: str, partial: list[ScrapedOffer] | None = None):
        super().__init__(message)
        self.message = message
        self.partial = partial or []


# --------------------------------------------------------------------------- HTTP


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(25.0, connect=10.0),
        follow_redirects=True,
    )


def describe_http_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "délai de réponse dépassé"
    if isinstance(exc, httpx.ConnectError):
        return "connexion impossible (réseau indisponible ?)"
    if isinstance(exc, httpx.TooManyRedirects):
        return "trop de redirections"
    return str(exc) or type(exc).__name__


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retries: int = 2,
    backoff: float = 2.0,
    **kwargs: Any,
) -> httpx.Response:
    """Requête avec quelques nouvelles tentatives (erreurs réseau, 429, 5xx).

    Renvoie la dernière réponse obtenue (à l'appelant de vérifier le code HTTP) ou
    lève ``SourceError`` si aucune réponse n'a pu être obtenue.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        delay = backoff * (attempt + 1)
        try:
            resp = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last_exc = exc
        else:
            if resp.status_code not in RETRY_STATUSES or attempt == retries:
                return resp
            retry_after = resp.headers.get("retry-after", "")
            if retry_after.isdigit():
                delay = min(float(retry_after), 15.0)
        if attempt < retries:
            await asyncio.sleep(delay)
    host = urlsplit(url).hostname or url
    raise SourceError(f"{host} : {describe_http_error(last_exc)}") from last_exc


# --------------------------------------------------------------------------- dates


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_date(value: Any) -> str | None:
    """Date ISO, date seule ou timestamp -> ISO UTC. Une date seule est placée à midi UTC
    pour rester le même jour quel que soit le fuseau horaire d'affichage."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return iso_utc(datetime.fromtimestamp(float(value), timezone.utc))
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return f"{text}T12:00:00Z"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return iso_utc(dt)


_RELATIVE_RE = re.compile(
    r"(\d+)\s*\+?\s*(minutes?|min|heures?|h|jours?|j|semaines?|mois|ans?|années?)\b"
)


def parse_relative_fr(text: str | None, now: datetime | None = None) -> str | None:
    """« il y a 3 jours », « hier », « aujourd'hui »… -> ISO UTC (approximatif)."""
    if not text:
        return None
    now = now or now_utc()
    t = text.strip().lower().replace("’", "'")
    if "instant" in t or "aujourd'hui" in t or "maintenant" in t:
        return iso_utc(now)
    if "avant-hier" in t:
        return iso_utc(now - timedelta(days=2))
    if "hier" in t:
        return iso_utc(now - timedelta(days=1))
    m = _RELATIVE_RE.search(t)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith("min"):
        delta = timedelta(minutes=n)
    elif unit.startswith("h"):
        delta = timedelta(hours=n)
    elif unit.startswith("j"):
        delta = timedelta(days=n)
    elif unit.startswith("sem"):
        delta = timedelta(weeks=n)
    elif unit == "mois":
        delta = timedelta(days=30 * n)
    else:
        delta = timedelta(days=365 * n)
    return iso_utc(now - delta)


# --------------------------------------------------------------------------- texte


def clean(text: Any) -> str:
    """Texte sur une ligne, espaces normalisés."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


_BLOCK_TAGS = [
    "p", "div", "section", "article", "header", "footer", "ul", "ol", "li", "table", "tr",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "dl", "dt", "dd",
]


def html_to_text(fragment: str | None) -> str:
    """HTML -> texte lisible (paragraphes et puces conservés)."""
    if not fragment:
        return ""
    if "<" not in fragment and "&lt;" in fragment:  # HTML échappé deux fois
        fragment = html_lib.unescape(fragment)
    soup = BeautifulSoup(fragment, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    for node in soup.find_all(string=True):
        if type(node) is not NavigableString:  # commentaires, doctype…
            node.extract()
        elif node.parent is not None and node.parent.name != "pre":
            collapsed = re.sub(r"\s+", " ", str(node))
            if collapsed != node:
                node.replace_with(collapsed)
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "• ")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    lines: list[str] = []
    pending_bullet = False
    for line in soup.get_text().split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line == "•":  # <li><p>texte</p></li> : la puce est seule sur sa ligne
            pending_bullet = True
            continue
        if pending_bullet and line:
            line, pending_bullet = f"• {line}", False
        lines.append(line)

    # Pas de ligne vide entre deux puces consécutives.
    next_text = [""] * len(lines)
    upcoming = ""
    for i in range(len(lines) - 1, -1, -1):
        next_text[i] = upcoming
        if lines[i]:
            upcoming = lines[i]
    kept: list[str] = []
    for i, line in enumerate(lines):
        if not line and kept and kept[-1].startswith("• ") and next_text[i].startswith("• "):
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])"
)
_IGNORED_EMAIL_DOMAINS = (
    "hellowork.com", "linkedin.com", "welcometothejungle.com", "wttj.co", "example.com",
    "example.org", "sentry.io", "indeed.com", "jobteaser.com",
)
_IGNORED_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
_IGNORED_EMAIL_PREFIXES = ("noreply", "no-reply", "no_reply", "donotreply", "ne-pas-repondre")


def extract_emails(*texts: str | None, limit: int = 5) -> list[str]:
    """Adresses email plausibles d'un recruteur trouvées dans les textes."""
    found: list[str] = []
    for text in texts:
        for match in _EMAIL_RE.finditer(text or ""):
            email = match.group(0).lower()
            domain = email.rsplit("@", 1)[1]
            if email.endswith(_IGNORED_EMAIL_SUFFIXES) or email.startswith(_IGNORED_EMAIL_PREFIXES):
                continue
            if any(domain == d or domain.endswith("." + d) for d in _IGNORED_EMAIL_DOMAINS):
                continue
            if email not in found:
                found.append(email)
    return found[:limit]


# --------------------------------------------------------------------------- JSON-LD


def _iter_jsonld(data: Any):
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            yield item
            if "@graph" in item:
                stack.append(item["@graph"])


def find_job_posting(soup: BeautifulSoup) -> dict[str, Any] | None:
    """Premier objet schema.org ``JobPosting`` des balises JSON-LD de la page."""
    for script in soup.find_all("script"):
        if (script.get("type") or "").strip().lower() != "application/ld+json":
            continue
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            try:
                data = json.loads(html_lib.unescape(raw))
            except ValueError:
                continue
        for item in _iter_jsonld(data):
            kind = item.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return item
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return html_to_text(value)
    if isinstance(value, list):
        return ", ".join(clean(v) for v in value if isinstance(v, (str, int, float)) and clean(v))
    return ""


def _squeeze(text: str) -> str:
    """Texte réduit à ses lettres/chiffres : sert à repérer un passage déjà présent
    même si les sauts de ligne ou la ponctuation diffèrent."""
    return re.sub(r"\W+", "", text).casefold()


def details_from_job_posting(job: dict[str, Any]) -> OfferDetails:
    description = _as_text(job.get("description"))
    flat = _squeeze(description)
    for key, label in (
        ("responsibilities", "Missions"),
        ("qualifications", "Profil recherché"),
        ("skills", "Compétences"),
    ):
        extra = _as_text(job.get(key))
        if not extra:
            continue
        probe = _squeeze(extra)[:60]
        if probe and probe in flat:
            continue
        description = f"{description}\n\n{label} :\n{extra}".strip()
        flat = _squeeze(description)

    org = job.get("hiringOrganization")
    company = clean(org.get("name")) if isinstance(org, dict) else clean(org if isinstance(org, str) else "")

    places = job.get("jobLocation")
    places = places if isinstance(places, list) else [places]
    cities: list[str] = []
    for place in places:
        address = place.get("address") if isinstance(place, dict) else None
        city = ""
        if isinstance(address, dict):
            city = clean(address.get("addressLocality") or address.get("addressRegion"))
        elif isinstance(address, str):
            city = clean(address)
        if city and city not in cities:
            cities.append(city)

    employment = job.get("employmentType")
    employment = employment if isinstance(employment, list) else [employment]
    contract = "Stage" if any(isinstance(e, str) and e.upper() == "INTERN" for e in employment) else ""

    return OfferDetails(
        description=description,
        title=clean(job.get("title")),
        company=company,
        location=", ".join(cities[:3]),
        contract=contract,
        published_at=normalize_date(job.get("datePosted")),
    )


def check_page_status(resp: httpx.Response) -> None:
    if resp.status_code in (404, 410):
        raise SourceError("L'offre n'est plus en ligne (page introuvable).")
    if resp.status_code in (401, 403):
        raise SourceError(
            f"Le site refuse la lecture automatique de cette page (HTTP {resp.status_code}). "
            "Copiez la description à la main si besoin."
        )
    if resp.status_code >= 400:
        raise SourceError(f"La page de l'offre a répondu HTTP {resp.status_code}.")


def mailto_addresses(soup: BeautifulSoup) -> str:
    return " ".join(
        (a.get("href") or "")[len("mailto:"):].split("?")[0]
        for a in soup.select('a[href^="mailto:"]')
    )


def parse_generic_page(html: str) -> OfferDetails:
    """Détails d'une offre : JSON-LD JobPosting, sinon titre et texte principal de la page."""
    soup = BeautifulSoup(html, "html.parser")
    job = find_job_posting(soup)
    if job is not None:
        details = details_from_job_posting(job)
    else:
        details = OfferDetails()
        og_title = soup.find("meta", attrs={"property": "og:title"})
        title_tag = soup.find("title")
        details.title = clean(og_title.get("content") if og_title else "") or clean(
            title_tag.get_text() if title_tag else ""
        )
        main = soup.find("main") or soup.find("article") or soup.body
        details.description = html_to_text(main.decode_contents())[:15000] if main else ""
    details.emails = extract_emails(details.description, mailto_addresses(soup))
    return details


async def fetch_generic_details(client: httpx.AsyncClient, url: str) -> OfferDetails:
    resp = await request_with_retry(client, "GET", url)
    check_page_status(resp)
    return await asyncio.to_thread(parse_generic_page, resp.text)
