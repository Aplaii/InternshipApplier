"""LinkedIn : API publique « jobs-guest » (sans compte), filtre type d'emploi = stage."""

from __future__ import annotations

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from .base import (
    OfferDetails, ScrapedOffer, SearchParams, SourceError, clean, extract_emails, html_to_text,
    normalize_date, parse_relative_fr, request_with_retry,
)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
RECENCY = {"day": "r86400", "3days": "r259200", "week": "r604800", "month": "r2592000"}
PAGE_DELAY = 1.2
RATE_LIMIT_MESSAGE = (
    "LinkedIn limite temporairement les requêtes (HTTP 429). Réessayez dans quelques minutes."
)
_ID_IN_URL = re.compile(r"(?:/jobs/view/(?:[^/?#]*?-)?|currentJobId=)(\d{6,})")


def offer_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def job_id_from_url(url: str) -> str | None:
    m = _ID_IN_URL.search(url)
    return m.group(1) if m else None


def parse_search_page(html: str) -> list[ScrapedOffer]:
    soup = BeautifulSoup(html, "html.parser")
    offers: list[ScrapedOffer] = []
    for card in soup.select('[data-entity-urn^="urn:li:jobPosting:"]'):
        job_id = card["data-entity-urn"].rsplit(":", 1)[-1]
        if not job_id.isdigit():
            continue
        title_el = card.select_one(".base-search-card__title") or card.select_one(".sr-only")
        title = clean(title_el.get_text()) if title_el else ""
        if not title:
            continue
        company_el = card.select_one(".base-search-card__subtitle")
        location_el = card.select_one(".job-search-card__location")
        time_el = card.select_one("time")
        published = None
        if time_el is not None:
            published = normalize_date(time_el.get("datetime")) or parse_relative_fr(
                time_el.get_text()
            )
        logo = card.select_one("img")
        logo_url = ""
        if logo is not None:
            logo_url = logo.get("data-delayed-url") or logo.get("src") or ""
        offers.append(
            ScrapedOffer(
                source="linkedin",
                source_id=job_id,
                url=offer_url(job_id),
                title=title,
                company=clean(company_el.get_text()) if company_el else "",
                location=clean(location_el.get_text()) if location_el else "",
                contract="Stage",
                logo_url=logo_url if logo_url.startswith("https://media.licdn.com/") else "",
                published_at=published,
            )
        )
    return offers


def parse_detail_page(html: str) -> OfferDetails:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    description = html_to_text(body.decode_contents()) if body is not None else ""
    criteria = []
    for item in soup.select("li.description__job-criteria-item"):
        name = item.select_one(".description__job-criteria-subheader")
        value = item.select_one(".description__job-criteria-text")
        if name is not None and value is not None:
            criteria.append(f"{clean(name.get_text())} : {clean(value.get_text())}")
    if criteria:
        description = f"{description}\n\n" + "\n".join(criteria)

    def text_of(selector: str) -> str:
        el = soup.select_one(selector)
        return clean(el.get_text()) if el is not None else ""

    return OfferDetails(
        description=description.strip(),
        emails=extract_emails(description),
        title=text_of(".top-card-layout__title"),
        company=text_of(".topcard__org-name-link") or text_of(".topcard__flavor"),
        location=text_of(".topcard__flavor--bullet"),
        contract="Stage",
    )


async def search(client: httpx.AsyncClient, params: SearchParams) -> list[ScrapedOffer]:
    query: dict[str, str | int] = {
        "keywords": params.keywords.strip(),
        "location": params.location.strip() or "France",
        "f_JT": "I",  # type d'emploi : stage
        "sortBy": "DD",  # les plus récentes d'abord
    }
    if params.recency in RECENCY:
        query["f_TPR"] = RECENCY[params.recency]

    results: dict[str, ScrapedOffer] = {}
    start = 0
    max_pages = min(30, params.max_results // 10 + 1)
    for page in range(max_pages):
        if page:
            await asyncio.sleep(PAGE_DELAY)
        query["start"] = start
        try:
            resp = await request_with_retry(
                client, "GET", SEARCH_URL, params=query, retries=2, backoff=3.0
            )
        except SourceError as exc:
            raise SourceError(exc.message, partial=list(results.values())) from exc
        if resp.status_code == 429:
            raise SourceError(RATE_LIMIT_MESSAGE, partial=list(results.values()))
        if resp.status_code == 400 and start > 0:
            break  # au-delà de la dernière page
        if resp.status_code != 200:
            raise SourceError(
                f"LinkedIn a répondu HTTP {resp.status_code}.", partial=list(results.values())
            )
        page_offers = await asyncio.to_thread(parse_search_page, resp.text)
        if not page_offers:
            break
        added = 0
        for offer in page_offers:
            if offer.source_id not in results:
                results[offer.source_id] = offer
                added += 1
        if added == 0 or len(results) >= params.max_results:
            break
        start += len(page_offers)
    return list(results.values())[: params.max_results]


async def fetch_details(client: httpx.AsyncClient, job_id: str) -> OfferDetails:
    resp = await request_with_retry(client, "GET", DETAIL_URL.format(job_id=job_id), retries=2, backoff=3.0)
    if resp.status_code == 429:
        raise SourceError(RATE_LIMIT_MESSAGE)
    if resp.status_code in (404, 410):
        raise SourceError("L'offre n'est plus disponible sur LinkedIn.")
    if resp.status_code != 200:
        raise SourceError(f"LinkedIn a répondu HTTP {resp.status_code}.")
    return await asyncio.to_thread(parse_detail_page, resp.text)
