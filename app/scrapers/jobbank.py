"""Job Bank / Guichet-Emplois (gouvernement du Canada).

Lit la page de résultats publique de jobbank.gc.ca (le site ne propose pas d'API de
recherche ouverte). Les offres « Student job » (emploi étudiant) sont signalées dans le
contrat ; le filtre « stage » de l'application décide ensuite s'il s'agit d'un stage.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from .base import (
    ScrapedOffer, SearchParams, SourceError, clean, iso_utc, now_utc, request_with_retry,
)

SEARCH_URL = "https://www.jobbank.gc.ca/jobsearch/jobsearch"
RECENCY_DAYS = {"day": 1, "3days": 3, "week": 7, "month": 30}
PAGE_DELAY = 1.0
# jobbank.gc.ca est parfois lent à établir la connexion depuis l'Europe.
TIMEOUT = httpx.Timeout(40.0, connect=25.0)


def offer_url(job_id: str) -> str:
    return f"https://www.jobbank.gc.ca/jobsearch/jobposting/{job_id}"


def _date(text: str) -> str | None:
    for fmt in ("%B %d, %Y", "%d %B %Y"):
        try:
            return iso_utc(datetime.strptime(clean(text), fmt).replace(hour=12))
        except ValueError:
            continue
    return None


def parse_search_page(html: str) -> list[ScrapedOffer]:
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for article in soup.select("article[id^=article-]"):
        job_id = article["id"].removeprefix("article-")
        title_el = article.select_one(".noctitle")
        if not job_id.isdigit() or title_el is None:
            continue
        def text_of(selector: str) -> str:
            el = article.select_one(selector)
            if el is None:
                return ""
            for hidden in el.select(".wb-inv"):
                hidden.decompose()
            return clean(el.get_text(" "))
        flags = [clean(f.get_text(" ")) for f in article.select(".flag > span") if clean(f.get_text(" "))]
        flags = [f for f in flags if f.casefold() not in ("new", "nouveau")]
        location = text_of(".location")
        offers.append(ScrapedOffer(
            source="jobbank", source_id=job_id, url=offer_url(job_id), title=clean(title_el.get_text(" ")),
            company=text_of(".business"),
            location=", ".join(p for p in (location, "Canada") if p),
            contract=" · ".join(flags),
            published_at=_date(text_of(".date")),
        ))
    return offers


async def search(client: httpx.AsyncClient, params: SearchParams) -> list[ScrapedOffer]:
    query: dict[str, str | int] = {"searchstring": params.keywords.strip(), "sort": "D"}
    oldest = now_utc() - timedelta(days=RECENCY_DAYS[params.recency]) if params.recency in RECENCY_DAYS else None
    results: dict[str, ScrapedOffer] = {}
    for page in range(1, 6):
        if page > 1:
            await asyncio.sleep(PAGE_DELAY)
        query["page"] = page
        try:
            resp = await request_with_retry(client, "GET", SEARCH_URL, params=query, timeout=TIMEOUT)
        except SourceError as exc:
            raise SourceError(exc.message, partial=list(results.values())) from exc
        if resp.status_code != 200:
            raise SourceError(f"Job Bank a répondu HTTP {resp.status_code}.", partial=list(results.values()))
        offers = await asyncio.to_thread(parse_search_page, resp.text)
        added = 0
        for offer in offers:
            if oldest and offer.published_at and offer.published_at < iso_utc(oldest):
                continue
            if offer.source_id not in results:
                results[offer.source_id] = offer
                added += 1
        if not added or len(results) >= params.max_results:
            break
    return list(results.values())[: params.max_results]
