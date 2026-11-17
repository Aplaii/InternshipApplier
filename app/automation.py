"""Recherche d'offres et automatisation.

- ``Automation.search`` : interroge les sites (plusieurs requêtes par site si besoin,
  et plusieurs lieux séparés par « ; »), écarte les offres hors FLE quand le filtre est
  actif, enregistre les nouvelles offres ;
- ``Automation.process_new`` : pour les nouvelles offres, lit la description en ligne
  (adresse du recruteur, thèmes FLE plus précis) puis, si demandé, prépare un brouillon
  d'email avec le modèle local ;
- ``Automation.scheduler`` : tâche de fond qui relance la recherche enregistrée à
  intervalle régulier (« Recherche automatique » dans les paramètres).

L'envoi des emails reste toujours manuel : chaque brouillon est relu avant de partir.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from . import fle_filter, assistant, config, db, llm, translate
from .scrapers import SEARCHERS, ScrapedOffer, SearchParams, SourceError, fetch_details, make_client

log = logging.getLogger("internship_applier")

# Pause entre deux lectures de page d'offre (politesse envers les sites).
DETAILS_DELAY = 1.0
# Nombre maximal de descriptions lues après une recherche.
DETAILS_MAX = 40
# Pause entre deux recherches LinkedIn (une par lieu et par requête) : LinkedIn limite
# vite les requêtes sans compte (HTTP 429).
LINKEDIN_QUERY_DELAY = 1.5
# Fréquence à laquelle la tâche de fond vérifie s'il faut relancer la recherche.
CHECK_EVERY = 60.0


@dataclass
class SourceOutcome:
    source: str
    offers: list[ScrapedOffer] = field(default_factory=list)
    found: int = 0
    error: str | None = None


def queries_for(keywords: str, fle_only: bool, source: str | None = None) -> list[str]:
    """Requêtes à lancer sur un site (pour chaque lieu)."""
    keywords = keywords.strip()
    if keywords:
        return [keywords]
    if not fle_only:
        return [""]
    if source == "linkedin":
        return [fle_filter.LINKEDIN_QUERY]
    return list(fle_filter.DEFAULT_QUERIES)


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_settings() -> dict[str, Any]:
    with db.get_conn() as conn:
        return db.load_settings(conn)


def _save_settings(changes: dict[str, Any]) -> None:
    with db.get_conn() as conn:
        db.save_settings(conn, changes)


class Automation:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.running: str | None = None  # "search" | "details" | "drafts" pendant un traitement
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None  # recherche lancée à la main

    # ------------------------------------------------------------------ recherche

    async def _search_source(
        self, client: httpx.AsyncClient, source: str, params: SearchParams, fle_only: bool
    ) -> SourceOutcome:
        outcome = SourceOutcome(source)
        merged: dict[str, ScrapedOffer] = {}
        locations = fle_filter.locations_for(source, params.location)
        queries = queries_for(params.keywords, fle_only, source)
        combos = [(loc, q) for loc in locations for q in queries]
        # « Offres max. par site » est réparti entre les recherches : sinon chaque lieu
        # parcourrait jusqu'à 6 pages et une recherche sur 9 pays durerait des minutes.
        per_search = max(10, -(-params.max_results // len(combos)))
        for index, (location, query) in enumerate(combos):
            if index and source == "linkedin":
                await asyncio.sleep(LINKEDIN_QUERY_DELAY)
            try:
                offers = await SEARCHERS[source](
                    client, replace(params, keywords=query, location=location, max_results=per_search)
                )
            except SourceError as exc:
                offers, outcome.error = exc.partial, exc.message
            except Exception as exc:  # bug d'analyse, site modifié…
                log.exception("Recherche %s en échec", source)
                offers = []
                outcome.error = f"Erreur inattendue ({type(exc).__name__}) : le site a peut-être changé."
            for offer in offers:
                merged.setdefault(offer.source_id, offer)
            if outcome.error:
                break
        outcome.found = len(merged)
        # Filtre FLE : thème FLE *et* stage (LinkedIn mélange stages et offres d'emploi).
        outcome.offers = [
            o for o in merged.values()
            if not fle_only or (fle_filter.is_fle_offer(o.title) and fle_filter.is_internship(o.title, o.contract))
        ]
        return outcome

    async def search(
        self, params: SearchParams, sources: list[str], *, fle_only: bool
    ) -> dict[str, Any]:
        """Lance la recherche et enregistre les offres. Le verrou doit être pris par l'appelant."""
        queries = queries_for(params.keywords, fle_only)
        self.running = "search"
        try:
            async with make_client() as client:
                outcomes = await asyncio.gather(
                    *(self._search_source(client, s, params, fle_only) for s in sources)
                )
        finally:
            self.running = None

        def save() -> tuple[list[dict[str, Any]], list[int]]:
            results, new_ids = [], []
            with db.get_conn() as conn:
                for outcome in outcomes:
                    fresh = [
                        o for o in outcome.offers
                        if db.find_offer_id(conn, o.source, o.source_id) is None
                    ]
                    new = db.upsert_offers(conn, outcome.offers)
                    new_ids += [db.find_offer_id(conn, o.source, o.source_id) for o in fresh]
                    results.append({
                        "source": outcome.source,
                        "label": config.SOURCE_LABELS[outcome.source],
                        "found": outcome.found,
                        "kept": len(outcome.offers),
                        "new": new,
                        "error": outcome.error,
                    })
            return results, [i for i in new_ids if i is not None]

        results, new_ids = await run_in_threadpool(save)
        return {
            "results": results,
            "new_total": sum(r["new"] for r in results),
            "filtered_total": sum(r["found"] - r["kept"] for r in results),
            "fle_only": fle_only,
            "queries": queries,
            "new_ids": new_ids,
        }

    # ------------------------------------------------------------------ traitement des nouvelles offres

    async def fetch_new_details(self, offer_ids: list[int]) -> int:
        """Lit la description en ligne des offres ``offer_ids``. Renvoie le nombre lu."""
        done = 0
        self.running = "details"
        try:
            async with make_client() as client:
                for index, offer_id in enumerate(offer_ids[:DETAILS_MAX]):
                    offer = await run_in_threadpool(self._get_offer, offer_id)
                    if offer is None or offer["details_fetched_at"]:
                        continue
                    if index:
                        await asyncio.sleep(DETAILS_DELAY)
                    try:
                        details = await fetch_details(client, offer["source"], offer["source_id"], offer["url"])
                    except SourceError as exc:
                        log.info("Description de l'offre %s indisponible : %s", offer_id, exc.message)
                        continue
                    except Exception:
                        log.exception("Lecture de l'offre %s en échec", offer_id)
                        continue

                    def save(offer_id: int = offer_id, details=details) -> None:
                        with db.get_conn() as conn:
                            db.set_details(conn, offer_id, details.description, details.emails)

                    await run_in_threadpool(save)
                    done += 1
        finally:
            self.running = None
        return done

    @staticmethod
    def _get_offer(offer_id: int) -> dict[str, Any] | None:
        with db.get_conn() as conn:
            return db.get_offer(conn, offer_id)

    async def draft_emails(self, settings: dict[str, Any], offer_ids: list[int]) -> dict[str, Any]:
        """Prépare un brouillon d'email (sans l'envoyer) pour chaque offre sans brouillon."""
        limit = int(settings.get("auto_draft_max") or 0)
        language = settings.get("llm_language") or "fr"
        drafted, error = 0, None
        self.running = "drafts"
        try:
            async with httpx.AsyncClient() as client:
                for offer_id in offer_ids:
                    if drafted >= limit:
                        break
                    offer = await run_in_threadpool(self._get_offer, offer_id)
                    if offer is None or offer["draft_body"] or offer["status"] != "new" or offer["is_archived"]:
                        continue
                    result = None
                    try:
                        async for event in assistant.generate_email(
                            settings, offer, language=language, instructions="", attachments=[], client=client,
                        ):
                            if event["type"] == "result":
                                result = event
                    except llm.LLMError as exc:
                        error = str(exc)  # serveur arrêté, contexte trop petit… : inutile d'insister
                        break
                    if result is None:
                        continue

                    def save(offer: dict[str, Any] = offer, result: dict[str, Any] = result) -> None:
                        with db.get_conn() as conn:
                            db.save_draft(conn, offer["id"], result["subject"], result["body"])
                            if not offer["draft_to"] and offer["contact_emails"]:
                                db.update_offers(conn, [offer["id"]], {"draft_to": offer["contact_emails"][0]})

                    await run_in_threadpool(save)
                    drafted += 1
        finally:
            self.running = None
        return {"drafted": drafted, "error": error}

    async def translate_titles(self, settings: dict[str, Any], offer_ids: list[int] | None = None) -> dict[str, Any]:
        """Traduit en français les titres en attente (ceux de ``offer_ids``, ou tous).

        Sans serveur llama.cpp, renvoie l'erreur sans rien changer : la traduction
        reste possible plus tard depuis la liste ou la fiche de l'offre.
        """
        pending = await run_in_threadpool(self._untranslated, offer_ids)
        if not pending:
            return {"translated": 0, "error": None}
        self.running = "translate"
        try:
            async with httpx.AsyncClient() as client:
                titles = await translate.translate_titles(settings, [p["title"] for p in pending], client)
        except llm.LLMError as exc:
            return {"translated": 0, "error": str(exc)}
        finally:
            self.running = None

        def save() -> int:
            done = 0
            with db.get_conn() as conn:
                for item, title_fr in zip(pending, titles):
                    if title_fr:
                        db.set_translation(conn, item["id"], title_fr=title_fr)
                        done += 1
            return done

        return {"translated": await run_in_threadpool(save), "error": None}

    @staticmethod
    def _untranslated(offer_ids: list[int] | None) -> list[dict[str, Any]]:
        with db.get_conn() as conn:
            return db.untranslated_titles(conn, ids=offer_ids, limit=200)

    async def process_new(self, settings: dict[str, Any], offer_ids: list[int]) -> dict[str, Any]:
        summary: dict[str, Any] = {"details": 0, "drafted": 0, "draft_error": None, "translated": 0}
        if not offer_ids:
            return summary
        if settings.get("auto_translate"):
            summary["translated"] = (await self.translate_titles(settings, offer_ids))["translated"]
        if settings.get("auto_fetch_details") or settings.get("auto_draft"):
            summary["details"] = await self.fetch_new_details(offer_ids)
        if settings.get("auto_draft"):
            outcome = await self.draft_emails(settings, offer_ids)
            summary["drafted"], summary["draft_error"] = outcome["drafted"], outcome["error"]
        return summary

    # ------------------------------------------------------------------ recherche programmée

    @staticmethod
    def saved_search(settings: dict[str, Any]) -> tuple[SearchParams, list[str]]:
        params = SearchParams(
            keywords=str(settings.get("search_keywords") or ""),
            location=str(settings.get("search_location") or ""),
            recency=str(settings.get("search_recency") or "week"),
            max_results=int(settings.get("search_max_per_source") or 60),
        )
        sources = [s for s in settings.get("search_sources") or [] if s in SEARCHERS]
        return params, sources or list(config.SEARCH_SOURCES)

    @staticmethod
    def next_run(settings: dict[str, Any]) -> datetime | None:
        if not settings.get("auto_enabled"):
            return None
        last = _parse_iso(settings.get("auto_last_run"))
        if last is None:
            return datetime.now(timezone.utc)
        return last + timedelta(hours=float(settings.get("auto_interval_hours") or 6))

    async def run_once(self, *, trigger: str = "schedule") -> dict[str, Any]:
        """Recherche enregistrée + traitement des nouvelles offres. Résumé gardé en base."""
        async with self.lock:
            return await self._run(trigger)

    async def start_now(self) -> None:
        """Démarre la recherche automatique en tâche de fond (le verrou est pris avant de rendre la main)."""
        await self.lock.acquire()

        async def go() -> None:
            try:
                await self._run("manual")
            finally:
                self.lock.release()

        self._task = asyncio.create_task(go())

    async def _run(self, trigger: str) -> dict[str, Any]:
        settings = await run_in_threadpool(_load_settings)
        params, sources = self.saved_search(settings)
        started = db.utcnow_iso()
        try:
            result = await self.search(params, sources, fle_only=bool(settings.get("fle_only")))
            processed = await self.process_new(settings, result["new_ids"])
            summary = {
                "at": started,
                "trigger": trigger,
                "ok": not any(r["error"] for r in result["results"]),
                "new_total": result["new_total"],
                "filtered_total": result["filtered_total"],
                "errors": [f"{r['label']} : {r['error']}" for r in result["results"] if r["error"]],
                **processed,
            }
        except Exception as exc:  # la tâche de fond ne doit jamais s'arrêter
            log.exception("Recherche automatique en échec")
            summary = {
                "at": started, "trigger": trigger, "ok": False, "new_total": 0, "filtered_total": 0,
                "errors": [f"Erreur inattendue ({type(exc).__name__})"], "details": 0, "drafted": 0,
                "draft_error": None,
            }
        await run_in_threadpool(_save_settings, {"auto_last_run": started, "auto_last_summary": summary})
        return summary

    def wake(self) -> None:
        """Réveille la tâche de fond (paramètres modifiés)."""
        self._wake.set()

    async def scheduler(self) -> None:
        while True:
            try:
                settings = await run_in_threadpool(_load_settings)
                due = self.next_run(settings)
                if due is not None and due <= datetime.now(timezone.utc) and not self.lock.locked():
                    await self.run_once(trigger="schedule")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Tâche de recherche automatique")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=CHECK_EVERY)
            except asyncio.TimeoutError:
                pass

    async def status(self) -> dict[str, Any]:
        settings = await run_in_threadpool(_load_settings)
        due = self.next_run(settings)
        return {
            "enabled": bool(settings.get("auto_enabled")),
            "interval_hours": settings.get("auto_interval_hours"),
            "running": self.running or ("search" if self.lock.locked() else None),
            "last_run": settings.get("auto_last_run") or None,
            "last_summary": settings.get("auto_last_summary") or None,
            "next_run": due.strftime("%Y-%m-%dT%H:%M:%SZ") if due else None,
        }
