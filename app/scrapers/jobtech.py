"""Platsbanken (Arbetsförmedlingen, Suède) via l'API publique JobTech « JobSearch ».

API ouverte du service public de l'emploi suédois, sans clé :
https://jobsearch.api.jobtechdev.se — documentation sur jobtechdev.se.
"""

from __future__ import annotations

from datetime import timedelta

import httpx

from .base import (
    OfferDetails, ScrapedOffer, SearchParams, SourceError, clean, extract_emails, iso_utc,
    normalize_date, now_utc, request_with_retry,
)

SEARCH_URL = "https://jobsearch.api.jobtechdev.se/search"
AD_URL = "https://jobsearch.api.jobtechdev.se/ad/{id}"
PAGE_SIZE = 100
RECENCY_DAYS = {"day": 1, "3days": 3, "week": 7, "month": 30}


def _label(value: object) -> str:
    return clean(value.get("label")) if isinstance(value, dict) else ""


def _description(hit: dict) -> str:
    desc = hit.get("description") or {}
    return (desc.get("text") or "").strip() if isinstance(desc, dict) else str(desc)


def parse_hit(hit: dict) -> ScrapedOffer | None:
    job_id = str(hit.get("id") or "")
    title = clean(hit.get("headline"))
    if not job_id or not title:
        return None
    address = hit.get("workplace_address") or {}
    place = ", ".join(p for p in (clean(address.get("municipality")), "Suède") if p)
    employer = hit.get("employer") or {}
    return ScrapedOffer(
        source="jobtech", source_id=job_id,
        url=hit.get("webpage_url") or f"https://arbetsformedlingen.se/platsbanken/annonser/{job_id}",
        title=title, company=clean(employer.get("name")), location=place,
        contract=" · ".join(p for p in (_label(hit.get("employment_type")), _label(hit.get("duration"))) if p),
        logo_url=hit.get("logo_url") or "",
        published_at=normalize_date(hit.get("publication_date")),
        description=_description(hit),
    )


async def search(client: httpx.AsyncClient, params: SearchParams) -> list[ScrapedOffer]:
    query: dict[str, str | int] = {"q": params.keywords.strip(), "limit": min(PAGE_SIZE, params.max_results)}
    if params.recency in RECENCY_DAYS:
        since = now_utc() - timedelta(days=RECENCY_DAYS[params.recency])
        query["published-after"] = iso_utc(since).rstrip("Z")
    results: dict[str, ScrapedOffer] = {}
    offset = 0
    while len(results) < params.max_results:
        query["offset"] = offset
        try:
            resp = await request_with_retry(client, "GET", SEARCH_URL, params=query, headers={"Accept": "application/json"})
        except SourceError as exc:
            raise SourceError(exc.message, partial=list(results.values())) from exc
        if resp.status_code != 200:
            raise SourceError(f"Platsbanken (JobTech) a répondu HTTP {resp.status_code}.", partial=list(results.values()))
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError("Réponse illisible de Platsbanken.", partial=list(results.values())) from exc
        hits = data.get("hits") or []
        for hit in hits:
            offer = parse_hit(hit)
            if offer is not None:
                results.setdefault(offer.source_id, offer)
        total = (data.get("total") or {}).get("value", 0)
        offset += len(hits)
        if not hits or offset >= total:
            break
    return list(results.values())[: params.max_results]


async def fetch_details(client: httpx.AsyncClient, job_id: str) -> OfferDetails:
    resp = await request_with_retry(client, "GET", AD_URL.format(id=job_id), headers={"Accept": "application/json"})
    if resp.status_code in (404, 410):
        raise SourceError("L'annonce n'est plus disponible sur Platsbanken.")
    if resp.status_code != 200:
        raise SourceError(f"Platsbanken (JobTech) a répondu HTTP {resp.status_code}.")
    offer = parse_hit(resp.json())
    if offer is None:
        raise SourceError("Annonce Platsbanken illisible.")
    application = resp.json().get("application_details") or {}
    return OfferDetails(
        description=offer.description, title=offer.title, company=offer.company, location=offer.location,
        contract=offer.contract, published_at=offer.published_at,
        emails=extract_emails(offer.description, application.get("email") or ""),
    )
