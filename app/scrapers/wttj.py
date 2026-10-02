"""Welcome to the Jungle : index de recherche Algolia public utilisé par le site."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .. import config
from .base import (
    OfferDetails, ScrapedOffer, SearchParams, SourceError, check_page_status, clean,
    details_from_job_posting, extract_emails, find_job_posting, html_to_text, mailto_addresses,
    normalize_date, parse_generic_page, request_with_retry,
)

BASE_URL = "https://www.welcometothejungle.com"
INDEX = "wttj_jobs_production_fr_published_at_desc"
RECENCY_SECONDS = {"day": 86400, "3days": 3 * 86400, "week": 7 * 86400, "month": 30 * 86400}
REMOTE_LABELS = {
    "fulltime": "Télétravail total",
    "partial": "Télétravail partiel",
    "punctual": "Télétravail ponctuel",
}
ATTRIBUTES = [
    "name", "slug", "organization.name", "organization.slug", "organization.logo",
    "offices", "published_at", "published_at_timestamp", "contract_duration_minimum",
    "contract_duration_maximum", "remote",
]
_URL_RE = re.compile(r"welcometothejungle\.com/[a-z]{2}/companies/([^/?#]+)/jobs/([^/?#]+)")


def offer_url(org_slug: str, job_slug: str) -> str:
    return f"{BASE_URL}/fr/companies/{org_slug}/jobs/{job_slug}"


def source_id_from_url(url: str) -> str | None:
    m = _URL_RE.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def build_query(params: SearchParams, now_ts: float | None = None) -> dict[str, Any]:
    filters = ["contract_type:internship", "offices.country_code:FR"]
    seconds = RECENCY_SECONDS.get(params.recency)
    if seconds:
        filters.append(f"published_at_timestamp > {int((now_ts or time.time()) - seconds)}")
    query = params.keywords.strip()
    location = params.location.strip()
    if location and location.casefold() != "france":
        # Le lieu est cherché dans le texte indexé (ville, département, région du bureau).
        query = f"{query} {location}".strip()
    return {
        "query": query,
        "hitsPerPage": max(1, min(params.max_results, 1000)),
        "page": 0,
        "filters": " AND ".join(filters),
        "attributesToRetrieve": ATTRIBUTES,
        "attributesToHighlight": [],
    }


def _duration(hit: dict[str, Any]) -> str:
    low, high = hit.get("contract_duration_minimum"), hit.get("contract_duration_maximum")
    if isinstance(low, int) and isinstance(high, int) and low != high:
        return f"{low} à {high} mois"
    months = low if isinstance(low, int) else high
    return f"{months} mois" if isinstance(months, int) and months > 0 else ""


def parse_hit(hit: dict[str, Any]) -> ScrapedOffer | None:
    org = hit.get("organization") if isinstance(hit.get("organization"), dict) else {}
    org_slug, job_slug, title = org.get("slug"), hit.get("slug"), clean(hit.get("name"))
    if not org_slug or not job_slug or not title:
        return None
    cities: list[str] = []
    for office in hit.get("offices") or []:
        city = clean(office.get("city")) if isinstance(office, dict) else ""
        if city and city not in cities:
            cities.append(city)
    location = ", ".join(cities[:3]) + (f" +{len(cities) - 3}" if len(cities) > 3 else "")
    bits = ["Stage", _duration(hit), REMOTE_LABELS.get(hit.get("remote") or "", "")]
    logo = org.get("logo") if isinstance(org.get("logo"), dict) else {}
    thumb = logo.get("thumb") if isinstance(logo.get("thumb"), dict) else {}
    logo_url = thumb.get("url") or logo.get("url") or ""
    return ScrapedOffer(
        source="wttj",
        source_id=f"{org_slug}/{job_slug}",
        url=offer_url(org_slug, job_slug),
        title=title,
        company=clean(org.get("name")),
        location=location,
        contract=" · ".join(b for b in bits if b),
        logo_url=logo_url if isinstance(logo_url, str) and logo_url.startswith("https://") else "",
        published_at=normalize_date(hit.get("published_at"))
        or normalize_date(hit.get("published_at_timestamp")),
    )


def parse_detail_page(html: str) -> OfferDetails:
    """Sections « Descriptif du poste » et « Profil recherché » de la page de l'offre
    (mieux structurées que le JSON-LD, dont le profil est aplati sans retours à la ligne)."""
    soup = BeautifulSoup(html, "html.parser")
    sections = [
        html_to_text(el.decode_contents())
        for el in soup.select(
            '[data-testid="job-section-description"], [data-testid="job-section-experience"]'
        )
    ]
    description = "\n\n".join(s for s in sections if s)
    if not description:
        return parse_generic_page(html)
    job = find_job_posting(soup)
    details = details_from_job_posting(job) if job is not None else OfferDetails()
    details.description = description
    details.emails = extract_emails(description, mailto_addresses(soup))
    return details


async def fetch_details(client: httpx.AsyncClient, url: str) -> OfferDetails:
    resp = await request_with_retry(client, "GET", url)
    check_page_status(resp)
    return await asyncio.to_thread(parse_detail_page, resp.text)


async def search(client: httpx.AsyncClient, params: SearchParams) -> list[ScrapedOffer]:
    app_id = config.WTTJ_ALGOLIA_APP_ID
    url = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{INDEX}/query"
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": config.WTTJ_ALGOLIA_API_KEY,
        "Referer": f"{BASE_URL}/",
        "Origin": BASE_URL,
        "Accept": "application/json",
    }
    resp = await request_with_retry(client, "POST", url, json=build_query(params), headers=headers)
    if resp.status_code in (401, 403):
        raise SourceError(
            "Welcome to the Jungle a refusé la clé de recherche (HTTP "
            f"{resp.status_code}). Le site a peut-être changé : voir le README (WTTJ_ALGOLIA_API_KEY)."
        )
    if resp.status_code != 200:
        raise SourceError(f"Welcome to the Jungle a répondu HTTP {resp.status_code}.")
    try:
        hits = resp.json().get("hits") or []
    except ValueError as exc:
        raise SourceError("Réponse illisible de Welcome to the Jungle.") from exc
    offers: dict[str, ScrapedOffer] = {}
    for hit in hits:
        offer = parse_hit(hit) if isinstance(hit, dict) else None
        if offer is not None:
            offers.setdefault(offer.source_id, offer)
    return list(offers.values())[: params.max_results]
