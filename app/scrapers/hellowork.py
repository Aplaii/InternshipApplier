"""HelloWork : pages de résultats HTML (30 offres par page, contrat « Stage »)."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from .base import (
    ScrapedOffer, SearchParams, SourceError, clean, parse_relative_fr, request_with_retry,
)

BASE_URL = "https://www.hellowork.com"
SEARCH_URL = f"{BASE_URL}/fr-fr/emploi/recherche.html"
RECENCY = {"day": "h", "3days": "d", "week": "w", "month": "m", "all": "all"}
PAGE_DELAY = 0.8
_DATE_RE = re.compile(r"^(il y a \d+|aujourd|hier\b|avant-hier|à l['’]instant)", re.IGNORECASE)
_ID_IN_URL = re.compile(r"/emplois/(\d+)\.html")


def offer_url(offer_id: str) -> str:
    return f"{BASE_URL}/fr-fr/emplois/{offer_id}.html"


def parse_search_page(html: str, now: datetime | None = None) -> list[ScrapedOffer]:
    soup = BeautifulSoup(html, "html.parser")
    offers: list[ScrapedOffer] = []
    for card in soup.select("li[data-id-storage-item-id]"):
        link = card.select_one('a[data-cy="offerTitle"]') or card.select_one('a[href*="/emplois/"]')
        offer_id = clean(card.get("data-id-storage-item-id"))
        if not offer_id.isdigit() and link is not None:
            m = _ID_IN_URL.search(link.get("href") or "")
            offer_id = m.group(1) if m else ""
        if not offer_id.isdigit():
            continue

        paragraphs = link.select("h3 p") if link is not None else []
        title = clean(paragraphs[0].get_text()) if paragraphs else ""
        company = clean(paragraphs[1].get_text()) if len(paragraphs) > 1 else ""
        if not title:
            field = card.select_one('input[name="title"]')
            title = clean(field.get("value")) if field else ""
        if not company:
            field = card.select_one('input[name="company"]')
            company = clean(field.get("value")) if field else ""
        if not title:
            continue

        location_el = card.select_one('[data-cy="localisationCard"]')
        bits = [clean(el.get_text()) for el in card.select('[data-cy="contractCard"], [data-cy="contractTag"]')]
        date_text = next(
            (s for s in card.stripped_strings if len(s) < 40 and _DATE_RE.match(s)), ""
        )
        logo = card.select_one("header img")
        logo_url = (logo.get("src") or "") if logo is not None else ""

        offers.append(
            ScrapedOffer(
                source="hellowork",
                source_id=offer_id,
                url=offer_url(offer_id),
                title=title,
                company=company,
                location=clean(location_el.get_text()) if location_el else "",
                contract=" · ".join(dict.fromkeys(b for b in bits if b)),
                logo_url=logo_url if logo_url.startswith("https://") else "",
                published_at=parse_relative_fr(date_text, now),
            )
        )
    return offers


async def search(client: httpx.AsyncClient, params: SearchParams) -> list[ScrapedOffer]:
    query: dict[str, str | int] = {
        "k": params.keywords.strip(),
        "c": "Stage",
        "d": RECENCY.get(params.recency, "all"),
        "st": "date",  # les plus récentes d'abord
    }
    location = params.location.strip()
    if location and location.casefold() != "france":
        query["l"] = location

    results: dict[str, ScrapedOffer] = {}
    max_pages = min(15, params.max_results // 20 + 1)
    for page in range(1, max_pages + 1):
        if page > 1:
            query["p"] = page
            await asyncio.sleep(PAGE_DELAY)
        try:
            resp = await request_with_retry(client, "GET", SEARCH_URL, params=query)
        except SourceError as exc:
            raise SourceError(exc.message, partial=list(results.values())) from exc
        if resp.status_code != 200:
            raise SourceError(
                f"HelloWork a répondu HTTP {resp.status_code}.", partial=list(results.values())
            )
        page_offers = await asyncio.to_thread(parse_search_page, resp.text)
        added = 0
        for offer in page_offers:
            if offer.source_id not in results:
                results[offer.source_id] = offer
                added += 1
        if added == 0 or len(results) >= params.max_results:
            break
    return list(results.values())[: params.max_results]
