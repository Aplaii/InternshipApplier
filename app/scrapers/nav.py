"""Arbeidsplassen (NAV, service public de l'emploi norvégien).

Utilise la recherche JSON publique du site arbeidsplassen.nav.no (sans compte). Les
offres de FINN.no n'y figurent pas toutes : FINN reste listé dans l'annuaire des plateformes.
"""

from __future__ import annotations

import httpx

from .base import (
    ScrapedOffer, SearchParams, SourceError, clean, normalize_date, request_with_retry,
)

SEARCH_URL = "https://arbeidsplassen.nav.no/stillinger/api/search"
PAGE_SIZE = 25
PUBLISHED = {"day": "now/d", "3days": "now/d-3d", "week": "now/d-7d", "month": "now/d-30d"}
# engagementType -> contrat affiché (Trainee / Lærling sont des formations).
CONTRACTS = {"fast": "Emploi permanent", "vikariat": "Remplacement", "engasjement": "Contrat",
             "prosjekt": "Projet", "sesong": "Saisonnier", "trainee": "Trainee", "lærling": "Apprentissage"}


def offer_url(uuid: str) -> str:
    return f"https://arbeidsplassen.nav.no/stillinger/stilling/{uuid}"


def parse_hit(hit: dict) -> ScrapedOffer | None:
    src = hit.get("_source") or {}
    uuid = str(src.get("uuid") or hit.get("_id") or "")
    title = clean(src.get("title"))
    if not uuid or not title:
        return None
    locations = src.get("locationList") or []
    city = clean(locations[0].get("city") or locations[0].get("municipal") or "").title() if locations else ""
    employer = src.get("employer") or {}
    engagement = clean(src.get("engagementtype") or src.get("engagementType") or "")
    summary = (src.get("generatedSearchMetadata") or {}).get("shortSummary") or ""
    return ScrapedOffer(
        source="nav", source_id=uuid, url=offer_url(uuid), title=title,
        company=clean(src.get("businessName") or employer.get("name")),
        location=", ".join(p for p in (city, "Norvège") if p),
        contract=CONTRACTS.get(engagement.casefold(), engagement),
        published_at=normalize_date(src.get("published")),
        description=clean(summary),
    )


async def search(client: httpx.AsyncClient, params: SearchParams) -> list[ScrapedOffer]:
    query: dict[str, str | int] = {"q": params.keywords.strip(), "size": PAGE_SIZE, "sort": "published"}
    if params.recency in PUBLISHED:
        query["published"] = PUBLISHED[params.recency]
    results: dict[str, ScrapedOffer] = {}
    start = 0
    while len(results) < params.max_results:
        query["from"] = start
        try:
            resp = await request_with_retry(client, "GET", SEARCH_URL, params=query, headers={"Accept": "application/json"})
        except SourceError as exc:
            raise SourceError(exc.message, partial=list(results.values())) from exc
        if resp.status_code != 200:
            raise SourceError(f"Arbeidsplassen (NAV) a répondu HTTP {resp.status_code}.", partial=list(results.values()))
        try:
            data = resp.json()
        except ValueError as exc:
            raise SourceError("Réponse illisible d'Arbeidsplassen.", partial=list(results.values())) from exc
        hits = (data.get("hits") or {}).get("hits") or []
        for hit in hits:
            offer = parse_hit(hit)
            if offer is not None:
                results.setdefault(offer.source_id, offer)
        total = ((data.get("hits") or {}).get("total") or {}).get("value", 0)
        start += len(hits)
        if not hits or start >= total:
            break
    return list(results.values())[: params.max_results]
