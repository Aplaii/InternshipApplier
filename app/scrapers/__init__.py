"""Sources d'offres de stage et récupération du détail d'une offre."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import hellowork, linkedin, wttj
from .base import (
    OfferDetails, ScrapedOffer, SearchParams, SourceError, fetch_generic_details, make_client,
)

__all__ = [
    "SEARCHERS", "OfferDetails", "ScrapedOffer", "SearchParams", "SourceError",
    "fetch_details", "identify_url", "make_client", "source_label_for_url",
]

SEARCHERS: dict[str, Callable[[httpx.AsyncClient, SearchParams], Awaitable[list[ScrapedOffer]]]] = {
    "hellowork": hellowork.search,
    "linkedin": linkedin.search,
    "wttj": wttj.search,
}

_HELLOWORK_ID = re.compile(r"hellowork\.com/[a-z]{2}-[a-z]{2}/emplois/(\d+)\.html")


def normalize_url(url: str) -> str:
    """URL sans fragment ni espaces superflus (sert d'identifiant aux offres manuelles)."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def identify_url(url: str) -> tuple[str, str, str]:
    """(source, identifiant, url canonique) d'une URL d'offre collée par l'utilisateur.

    Les offres HelloWork / LinkedIn / Welcome to the Jungle sont reconnues pour éviter
    les doublons avec la recherche automatique ; les autres sites sont « manual ».
    """
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith("hellowork.com"):
        m = _HELLOWORK_ID.search(url)
        if m:
            return "hellowork", m.group(1), hellowork.offer_url(m.group(1))
    if host.endswith("linkedin.com"):
        job_id = linkedin.job_id_from_url(url)
        if job_id:
            return "linkedin", job_id, linkedin.offer_url(job_id)
    if host.endswith("welcometothejungle.com"):
        source_id = wttj.source_id_from_url(url)
        if source_id:
            org_slug, job_slug = source_id.split("/", 1)
            return "wttj", source_id, wttj.offer_url(org_slug, job_slug)
    canonical = normalize_url(url)
    return "manual", canonical, canonical


def source_label_for_url(url: str) -> str:
    """Nom court du site d'une offre manuelle : fr.indeed.com -> « Indeed »."""
    host = (urlsplit(url).hostname or "").lower()
    labels = [p for p in host.split(".") if p and p != "www"]
    if len(labels) >= 2:
        labels = labels[:-1]  # retire l'extension (.fr, .com…)
    name = labels[-1] if labels else ""
    return name.capitalize() if name else "Manuel"


async def fetch_details(
    client: httpx.AsyncClient, source: str, source_id: str, url: str
) -> OfferDetails:
    if source == "linkedin":
        return await linkedin.fetch_details(client, source_id)
    if source == "wttj":
        return await wttj.fetch_details(client, url)
    return await fetch_generic_details(client, url)
