"""EURES, portail européen de l'emploi (Commission européenne).

Une seule recherche couvre tous les pays de l'UE et de l'EEE visés (Suède, Norvège,
Danemark, Finlande, Islande, pays baltes, Pays-Bas, Irlande…). L'API JSON publique est
celle qu'utilise le site lui-même ; elle ne demande ni compte ni clé.

``params.location`` contient les codes pays séparés par des virgules (« se,no,dk »),
calculés par ``fle_filter.locations_for`` ; ``params.keywords`` est une liste de mots
séparés par des espaces, que l'offre doit tous contenir.
"""

from __future__ import annotations

import httpx

from .base import (
    OfferDetails, ScrapedOffer, SearchParams, SourceError, clean, extract_emails, html_to_text,
    normalize_date, request_with_retry,
)

SEARCH_URL = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
DETAIL_API = "https://europa.eu/eures/api/jv-searchengine/public/jv/id/{id}"
PAGE_SIZE = 50
PERIODS = {"day": "LAST_DAY", "3days": "LAST_THREE_DAYS", "week": "LAST_WEEK", "month": "LAST_MONTH"}

# positionOfferingCode -> contrat affiché.
CONTRACTS = {
    "directhire": "Emploi", "temporary": "Contrat temporaire", "contract": "Contrat",
    "contracttohire": "Contrat puis embauche", "apprenticeship": "Apprentissage",
    "internship": "Stage", "traineeship": "Stage", "seasonal": "Saisonnier", "oncall": "Sur appel",
    "selfemployed": "Indépendant", "volunteer": "Bénévolat",
}


def offer_url(job_id: str) -> str:
    return f"https://europa.eu/eures/portal/jv-se/jv-details/{job_id}?lang=fr"


def search_body(params: SearchParams, page: int) -> dict:
    codes = [c for c in params.location.split(",") if c]
    words = [w for w in params.keywords.split() if w]
    return {
        "resultsPerPage": PAGE_SIZE, "page": page, "sortSearch": "MOST_RECENT",
        "keywords": [{"keyword": w, "specificSearchCode": "EVERYWHERE"} for w in words],
        "publicationPeriod": PERIODS.get(params.recency), "occupationUris": [], "skillUris": [],
        "requiredExperienceCodes": [], "positionScheduleCodes": [], "sectorCodes": [],
        "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [], "locationCodes": codes,
        "euresFlagCodes": [], "otherBenefitsCodes": [], "requiredLanguages": [], "minNumberPost": None,
        "sessionId": "internship-applier",
    }


def _location(location_map: dict | None) -> str:
    countries = [c for c in (location_map or {}) if c]
    return ", ".join(COUNTRY_NAMES.get(c.upper(), c.upper()) for c in countries)


COUNTRY_NAMES = {
    "SE": "Suède", "NO": "Norvège", "DK": "Danemark", "FI": "Finlande", "IS": "Islande", "EE": "Estonie",
    "LV": "Lettonie", "LT": "Lituanie", "NL": "Pays-Bas", "IE": "Irlande", "BE": "Belgique", "DE": "Allemagne",
    "LU": "Luxembourg", "AT": "Autriche", "CH": "Suisse", "PL": "Pologne", "FR": "France",
}


def parse_search(data: dict) -> tuple[list[ScrapedOffer], int]:
    """(offres de la page, nombre total de résultats)."""
    results = []
    for jv in data.get("jvs") or []:
        job_id = str(jv.get("id") or "")
        title = clean(jv.get("title"))
        if not job_id or not title:
            continue
        created = jv.get("creationDate")
        offer = ScrapedOffer(
            source="eures", source_id=job_id, url=offer_url(job_id), title=title,
            company=clean((jv.get("employer") or {}).get("name")),
            location=_location(jv.get("locationMap")),
            contract=CONTRACTS.get(str(jv.get("positionOfferingCode") or "").lower(), ""),
            published_at=normalize_date(created / 1000) if isinstance(created, (int, float)) else None,
            description=html_to_text(jv.get("description") or ""),
        )
        results.append(offer)
    return results, int(data.get("numberRecords") or 0)


async def search(client: httpx.AsyncClient, params: SearchParams) -> list[ScrapedOffer]:
    if not params.location:
        return []
    results: dict[str, ScrapedOffer] = {}
    page = 1
    while len(results) < params.max_results:
        try:
            resp = await request_with_retry(client, "POST", SEARCH_URL, json=search_body(params, page))
        except SourceError as exc:
            raise SourceError(exc.message, partial=list(results.values())) from exc
        if resp.status_code != 200:
            raise SourceError(f"EURES a répondu HTTP {resp.status_code}.", partial=list(results.values()))
        try:
            offers, total = parse_search(resp.json())
        except ValueError as exc:
            raise SourceError("Réponse illisible d'EURES.", partial=list(results.values())) from exc
        for offer in offers:
            results.setdefault(offer.source_id, offer)
        if not offers or page * PAGE_SIZE >= total:
            break
        page += 1
    return list(results.values())[: params.max_results]


def parse_detail(data: dict) -> OfferDetails:
    profiles = data.get("jvProfiles") or {}
    lang = data.get("preferredLanguage") or next(iter(profiles), "")
    profile = profiles.get(lang) or next(iter(profiles.values()), {}) or {}
    description = html_to_text(profile.get("jobDescription") or profile.get("description") or "")
    employer = profile.get("employer") or {}
    return OfferDetails(
        description=description,
        emails=extract_emails(description),
        title=clean(profile.get("title")),
        company=clean(employer.get("name") if isinstance(employer, dict) else ""),
        contract=CONTRACTS.get(str(profile.get("positionOfferingCode") or "").lower(), ""),
    )


async def fetch_details(client: httpx.AsyncClient, job_id: str) -> OfferDetails:
    resp = await request_with_retry(client, "GET", DETAIL_API.format(id=job_id), params={"lang": "fr"})
    if resp.status_code == 404:
        raise SourceError("L'offre n'est plus disponible sur EURES.")
    if resp.status_code != 200:
        raise SourceError(f"EURES a répondu HTTP {resp.status_code}.")
    try:
        return parse_detail(resp.json())
    except ValueError as exc:
        raise SourceError("Réponse illisible d'EURES.") from exc
