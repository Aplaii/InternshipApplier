"""Vérification en ligne des trois sites (désactivée par défaut).

À lancer si une source ne renvoie plus rien, pour savoir si le site a changé :
    RUN_LIVE=1 .venv/bin/python -m pytest tests/test_live.py
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.scrapers import SEARCHERS, SearchParams, fetch_details, make_client

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="tests en ligne : RUN_LIVE=1")


@pytest.mark.parametrize("source", sorted(SEARCHERS))
def test_live_search_and_details(source):
    async def go():
        async with make_client() as client:
            offers = await SEARCHERS[source](client, SearchParams(keywords="data", recency="month", max_results=10))
            assert offers, f"{source} : aucune offre"
            for offer in offers:
                assert offer.source == source and offer.source_id and offer.title
                assert offer.url.startswith("https://")
            assert sum(bool(o.company) for o in offers) >= len(offers) * 0.8
            assert sum(bool(o.published_at) for o in offers) >= len(offers) * 0.8
            details = await fetch_details(client, source, offers[0].source_id, offers[0].url)
            assert len(details.description) > 200, f"{source} : description vide"

    asyncio.run(go())
