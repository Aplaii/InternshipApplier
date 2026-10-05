"""Application FastAPI : API JSON + interface web (dossier static/)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__, assistant, config, db, documents, llm, mailer, prompts
from .automation import Automation
from .scrapers import (
    ScrapedOffer, SearchParams, SourceError, fetch_details, identify_url, make_client,
    source_label_for_url,
)
from .scrapers.base import clean, extract_emails

log = logging.getLogger("internship_applier")

Status = Literal["new", "sent", "answered", "refused", "interview"]
SearchSource = Literal["hellowork", "linkedin", "wttj"]
Recency = Literal["day", "3days", "week", "month", "all"]
Folder = Literal["inbox", "unread", "starred", "archived", "all"]

APP_HEADER = "InternshipApplier"


# --------------------------------------------------------------------------- modèles


class OfferPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status | None = None
    is_read: bool | None = None
    is_starred: bool | None = None
    is_archived: bool | None = None
    notes: str | None = Field(None, max_length=20000)
    draft_to: str | None = Field(None, max_length=1000)
    draft_subject: str | None = Field(None, max_length=1000)
    draft_body: str | None = Field(None, max_length=100000)
    description: str | None = Field(None, max_length=100000)


class BulkPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[int] = Field(min_length=1, max_length=5000)
    status: Status | None = None
    is_read: bool | None = None
    is_starred: bool | None = None
    is_archived: bool | None = None


class FetchRequest(BaseModel):
    keywords: str = Field("", max_length=200)
    location: str = Field("", max_length=120)
    sources: list[SearchSource] = Field(min_length=1)
    recency: Recency = "week"
    max_per_source: int = Field(60, ge=10, le=300)
    ai_only: bool | None = None  # None = paramètre enregistré


class ImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class ManualOfferIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    title: str = Field(min_length=1, max_length=300)
    company: str = Field("", max_length=200)
    location: str = Field("", max_length=200)
    contract: str = Field("", max_length=200)
    description: str = Field("", max_length=100000)


class GenerateRequest(BaseModel):
    language: Literal["fr", "en"] | None = None
    instructions: str = Field("", max_length=2000)
    attachments: list[str] = Field(default_factory=list, max_length=10)


class SendRequest(BaseModel):
    to: str = Field(min_length=3, max_length=1000)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100000)
    attachments: list[str] = Field(default_factory=list, max_length=10)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=254)
    phone: str | None = Field(None, max_length=40)
    links: str | None = Field(None, max_length=1000)
    education: str | None = Field(None, max_length=1000)
    internship: str | None = Field(None, max_length=1000)
    cv_text: str | None = Field(None, max_length=30000)
    signature: str | None = Field(None, max_length=1000)
    llm_base_url: str | None = Field(None, max_length=300)
    llm_api_key: str | None = Field(None, max_length=300)
    llm_model: str | None = Field(None, max_length=300)
    llm_temperature: float | None = Field(None, ge=0, le=2)
    llm_max_tokens: int | None = Field(None, ge=128, le=8192)
    llm_disable_thinking: bool | None = None
    llm_language: Literal["fr", "en"] | None = None
    llm_extra_instructions: str | None = Field(None, max_length=2000)
    smtp_host: str | None = Field(None, max_length=255)
    smtp_port: int | None = Field(None, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "none"] | None = None
    smtp_username: str | None = Field(None, max_length=254)
    smtp_password: str | None = Field(None, max_length=500)
    smtp_from_name: str | None = Field(None, max_length=120)
    smtp_from_email: str | None = Field(None, max_length=254)
    smtp_bcc_self: bool | None = None
    search_keywords: str | None = Field(None, max_length=200)
    search_location: str | None = Field(None, max_length=120)
    search_sources: list[SearchSource] | None = None
    search_recency: Recency | None = None
    search_max_per_source: int | None = Field(None, ge=10, le=300)
    ai_only: bool | None = None
    auto_enabled: bool | None = None
    auto_interval_hours: float | None = Field(None, ge=1, le=168)
    auto_fetch_details: bool | None = None
    auto_draft: bool | None = None
    auto_draft_max: int | None = Field(None, ge=1, le=30)


# --------------------------------------------------------------------------- sécurité locale


class LocalSecurityMiddleware:
    """Les requêtes qui modifient des données doivent porter l'en-tête X-Requested-With.

    Un site tiers ouvert dans le navigateur ne peut pas ajouter cet en-tête à une requête
    vers 127.0.0.1 (le navigateur exigerait une autorisation CORS que l'on ne donne pas) :
    il ne peut donc ni envoyer d'email ni modifier les offres à votre insu.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        if path.startswith("/api/") and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            headers = dict(scope.get("headers") or [])
            if headers.get(b"x-requested-with") != APP_HEADER.encode():
                response = JSONResponse(
                    {"detail": "Requête refusée : en-tête X-Requested-With manquant."}, status_code=403
                )
                await response(scope, receive, send)
                return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                if not path.startswith("/api/"):
                    headers["Cache-Control"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_with_headers)


# --------------------------------------------------------------------------- utilitaires


def _load_settings() -> dict[str, Any]:
    with db.get_conn() as conn:
        return db.load_settings(conn)


def _public_settings(settings: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in settings.items() if k not in config.SECRET_SETTINGS}
    for key in config.SECRET_SETTINGS:
        data[f"has_{key}"] = bool(settings.get(key))
    data["effective_signature"] = prompts.default_signature(settings)
    return data


def _ai_only() -> bool:
    return bool(_load_settings().get("ai_only"))


def _with_label(offer: dict[str, Any]) -> dict[str, Any]:
    source = offer.get("source", "")
    if source == "manual":
        offer["source_label"] = source_label_for_url(offer.get("url", ""))
    else:
        offer["source_label"] = config.SOURCE_LABELS.get(source, source)
    return offer


def _get_offer_or_404(offer_id: int) -> dict[str, Any]:
    with db.get_conn() as conn:
        offer = db.get_offer(conn, offer_id)
    if offer is None:
        raise HTTPException(404, "Offre introuvable.")
    return offer


def _check_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(422, "Lien invalide : collez l'adresse complète de l'offre (https://…).")
    return url


async def _ensure_details(offer_id: int, *, refresh: bool = False) -> dict[str, Any]:
    """Description de l'offre (récupérée en ligne une seule fois, puis gardée en base)."""
    offer = await run_in_threadpool(_get_offer_or_404, offer_id)
    if offer["details_fetched_at"] and not refresh:
        return offer
    async with make_client() as client:
        details = await fetch_details(client, offer["source"], offer["source_id"], offer["url"])

    def save() -> dict[str, Any]:
        with db.get_conn() as conn:
            db.set_details(conn, offer_id, details.description, details.emails)
            return db.get_offer(conn, offer_id)

    return await run_in_threadpool(save)


def _ndjson(event: dict[str, Any]) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- application


def create_app() -> FastAPI:
    automation = Automation()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        db.init_db()
        task = asyncio.create_task(automation.scheduler())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="InternshipApplier", version=__version__, lifespan=lifespan)
    allowed_hosts = [
        h.strip() for h in os.environ.get("IA_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()
    ]
    app.add_middleware(LocalSecurityMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.state.automation = automation

    # ------------------------------------------------------------------- offres

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "version": __version__,
            "statuses": list(config.STATUSES),
            "sources": config.SOURCE_LABELS,
            "search_sources": list(config.SEARCH_SOURCES),
        }

    @app.get("/api/offers")
    def list_offers(
        folder: Folder = "inbox",
        status: Status | None = None,
        source: str | None = Query(None, max_length=40),
        q: str | None = Query(None, max_length=200),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        ai_only = _ai_only()
        with db.get_conn() as conn:
            items, total = db.list_offers(
                conn, folder=folder, status=status, source=source, q=q, ai_only=ai_only,
                page=page, page_size=page_size,
            )
        return {
            "items": [_with_label(item) for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @app.get("/api/counts")
    def get_counts() -> dict[str, Any]:
        ai_only = _ai_only()
        with db.get_conn() as conn:
            return {**db.counts(conn, ai_only=ai_only), "ai_only": ai_only}

    @app.get("/api/stats")
    def get_stats() -> dict[str, Any]:
        ai_only = _ai_only()
        with db.get_conn() as conn:
            return {**db.stats(conn, ai_only=ai_only), "ai_only": ai_only}

    @app.get("/api/offers/{offer_id}")
    def get_offer(offer_id: int) -> dict[str, Any]:
        return _with_label(_get_offer_or_404(offer_id))

    @app.patch("/api/offers/{offer_id}")
    def patch_offer(offer_id: int, patch: OfferPatch) -> dict[str, Any]:
        changes = patch.model_dump(exclude_none=True)
        description = changes.pop("description", None)
        with db.get_conn() as conn:
            if db.get_offer(conn, offer_id) is None:
                raise HTTPException(404, "Offre introuvable.")
            db.update_offers(conn, [offer_id], changes)
            if description is not None:
                description = description.strip()
                db.set_details(conn, offer_id, description, extract_emails(description))
            offer = db.get_offer(conn, offer_id)
        return _with_label(offer)

    @app.post("/api/offers/bulk")
    def bulk_update(patch: BulkPatch) -> dict[str, int]:
        changes = patch.model_dump(exclude_none=True, exclude={"ids"})
        if not changes:
            raise HTTPException(422, "Aucune modification demandée.")
        with db.get_conn() as conn:
            return {"updated": db.update_offers(conn, patch.ids, changes)}

    @app.delete("/api/offers/{offer_id}")
    def delete_offer(offer_id: int) -> dict[str, bool]:
        with db.get_conn() as conn:
            if not db.delete_offer(conn, offer_id):
                raise HTTPException(404, "Offre introuvable.")
        return {"ok": True}

    @app.post("/api/offers/import")
    async def import_offer(req: ImportRequest) -> dict[str, Any]:
        """Lit une offre à partir de son lien (sans l'enregistrer) pour pré-remplir le formulaire."""
        source, source_id, url = identify_url(_check_url(req.url))

        def find_existing() -> int | None:
            with db.get_conn() as conn:
                return db.find_offer_id(conn, source, source_id)

        result: dict[str, Any] = {
            "url": url,
            "source": source,
            "source_label": config.SOURCE_LABELS[source] if source != "manual" else source_label_for_url(url),
            "existing_id": await run_in_threadpool(find_existing),
            "title": "", "company": "", "location": "", "contract": "", "description": "",
            "error": None,
        }
        try:
            async with make_client() as client:
                details = await fetch_details(client, source, source_id, url)
        except SourceError as exc:
            result["error"] = exc.message
        else:
            result.update(
                title=details.title, company=details.company, location=details.location,
                contract=details.contract, description=details.description,
            )
        return result

    @app.post("/api/offers", status_code=201)
    def create_offer(req: ManualOfferIn) -> dict[str, Any]:
        source, source_id, url = identify_url(_check_url(req.url))
        offer = ScrapedOffer(
            source=source, source_id=source_id, url=url, title=clean(req.title),
            company=clean(req.company), location=clean(req.location), contract=clean(req.contract),
        )
        if not offer.title:
            raise HTTPException(422, "Le titre de l'offre est obligatoire.")
        description = req.description.strip()
        with db.get_conn() as conn:
            existing = db.find_offer_id(conn, source, source_id)
            if existing is not None:
                raise HTTPException(409, {"message": "Cette offre est déjà dans la liste.", "id": existing})
            offer_id = db.insert_manual_offer(conn, offer, description or None)
            if description:
                db.set_details(conn, offer_id, description, extract_emails(description))
            created = db.get_offer(conn, offer_id)
        return _with_label(created)

    @app.post("/api/offers/{offer_id}/details")
    async def offer_details(offer_id: int, refresh: bool = False) -> dict[str, Any]:
        try:
            offer = await _ensure_details(offer_id, refresh=refresh)
        except SourceError as exc:
            raise HTTPException(502, exc.message) from exc
        return _with_label(offer)

    # ------------------------------------------------------------------- recherche

    @app.post("/api/fetch")
    async def fetch_offers(req: FetchRequest) -> dict[str, Any]:
        if automation.lock.locked():
            raise HTTPException(409, "Une recherche est déjà en cours.")
        async with automation.lock:
            params = SearchParams(
                keywords=req.keywords.strip(), location=req.location.strip(),
                recency=req.recency, max_results=req.max_per_source,
            )
            sources = list(dict.fromkeys(req.sources))

            def remember() -> dict[str, Any]:
                with db.get_conn() as conn:
                    changes: dict[str, Any] = {
                        "search_keywords": params.keywords, "search_location": params.location,
                        "search_sources": sources, "search_recency": params.recency,
                        "search_max_per_source": params.max_results,
                    }
                    if req.ai_only is not None:
                        changes["ai_only"] = req.ai_only
                    db.save_settings(conn, changes)
                    return db.load_settings(conn)

            settings = await run_in_threadpool(remember)
            result = await automation.search(params, sources, ai_only=bool(settings.get("ai_only")))
            new_ids = result.pop("new_ids")
            if settings.get("auto_fetch_details"):
                result["details"] = await automation.fetch_new_details(new_ids)
        return result

    @app.get("/api/automation")
    async def automation_status() -> dict[str, Any]:
        return await automation.status()

    @app.post("/api/automation/run", status_code=202)
    async def automation_run() -> dict[str, Any]:
        """Lance tout de suite la recherche automatique, en arrière-plan (suivi : GET /api/automation)."""
        if automation.lock.locked():
            raise HTTPException(409, "Une recherche est déjà en cours.")
        await automation.start_now()
        return await automation.status()

    # ------------------------------------------------------------------- IA & email

    @app.post("/api/offers/{offer_id}/generate")
    async def generate(offer_id: int, req: GenerateRequest) -> StreamingResponse:
        settings = await run_in_threadpool(_load_settings)
        offer = await run_in_threadpool(_get_offer_or_404, offer_id)
        language = req.language or settings.get("llm_language") or "fr"

        async def events() -> AsyncIterator[bytes]:
            nonlocal offer
            try:
                if not offer["details_fetched_at"]:
                    yield _ndjson({"type": "status", "message": "Lecture de l'offre en ligne…"})
                    try:
                        offer = await _ensure_details(offer_id)
                    except SourceError as exc:
                        yield _ndjson({
                            "type": "notice",
                            "message": f"Description indisponible ({exc.message}) : l'email s'appuiera "
                                       "sur le titre et l'entreprise.",
                        })
                async with httpx.AsyncClient() as client:
                    async for event in assistant.generate_email(
                        settings, offer, language=language, instructions=req.instructions,
                        attachments=req.attachments, client=client,
                    ):
                        if event["type"] == "result":
                            def save_draft() -> None:
                                with db.get_conn() as conn:
                                    db.save_draft(conn, offer_id, event["subject"], event["body"])

                            await run_in_threadpool(save_draft)
                        yield _ndjson(event)
            except llm.LLMError as exc:
                yield _ndjson({"type": "error", "message": str(exc)})
            except Exception:
                log.exception("Génération en échec")
                yield _ndjson({"type": "error", "message": "Erreur inattendue pendant la génération (détails dans le terminal)."})

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/offers/{offer_id}/send")
    def send_application(offer_id: int, req: SendRequest) -> dict[str, Any]:
        _get_offer_or_404(offer_id)
        settings = _load_settings()
        try:
            recipients = mailer.parse_recipients(req.to)
            paths = [documents.resolve(name) for name in dict.fromkeys(req.attachments)]
            message = mailer.build_message(settings, recipients, req.subject, req.body, paths)
        except (mailer.MailError, documents.DocumentError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            refused = mailer.send(settings, message)
        except mailer.MailError as exc:
            raise HTTPException(502, str(exc)) from exc
        with db.get_conn() as conn:
            db.record_sent_email(
                conn, offer_id, to_addr=", ".join(recipients), subject=req.subject, body=req.body,
                attachments=[p.name for p in paths], message_id=str(message["Message-ID"]),
            )
            offer = db.get_offer(conn, offer_id)
        return {"offer": _with_label(offer), "refused": refused}

    # ------------------------------------------------------------------- paramètres

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return _public_settings(_load_settings())

    @app.put("/api/settings")
    def put_settings(update: SettingsUpdate) -> dict[str, Any]:
        changes = {k: v for k, v in update.model_dump(exclude_unset=True).items() if v is not None}
        for key, value in list(changes.items()):
            if isinstance(value, str) and key != "smtp_password":
                changes[key] = value.strip()
        for key in ("email", "smtp_from_email"):
            if changes.get(key) and not mailer.is_valid_email(changes[key]):
                raise HTTPException(422, f"Adresse email invalide : {changes[key]}")
        with db.get_conn() as conn:
            db.save_settings(conn, changes)
            settings = db.load_settings(conn)
        if any(key.startswith("auto_") for key in changes):
            automation.wake()
        return _public_settings(settings)

    @app.post("/api/settings/test-llm")
    async def test_llm() -> dict[str, Any]:
        settings = await run_in_threadpool(_load_settings)
        try:
            async with httpx.AsyncClient() as client:
                info = await llm.server_info(settings, client)
        except llm.LLMError as exc:
            return {"ok": False, "message": str(exc)}
        models = ", ".join(info["models"]) or "modèle inconnu"
        context = f", contexte {info['n_ctx']} tokens" if info["n_ctx"] else ""
        return {"ok": True, "message": f"Connecté : {models}{context}.", **info}

    @app.post("/api/settings/test-smtp")
    def test_smtp() -> dict[str, Any]:
        try:
            mailer.test_connection(_load_settings())
        except mailer.MailError as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "Connexion et authentification SMTP réussies."}

    # ------------------------------------------------------------------- documents

    @app.get("/api/documents")
    def get_documents() -> list[dict[str, Any]]:
        return documents.list_documents()

    @app.put("/api/documents/{name}")
    async def upload_document(name: str, request: Request) -> dict[str, Any]:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > documents.MAX_SIZE:
                raise HTTPException(413, "Fichier trop volumineux (10 Mo maximum).")
            chunks.append(chunk)
        try:
            return await run_in_threadpool(documents.save_document, name, b"".join(chunks))
        except documents.DocumentError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/documents/{name}")
    def remove_document(name: str) -> dict[str, bool]:
        try:
            documents.delete_document(name)
        except documents.DocumentError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/documents/{name}/text")
    def document_text(name: str) -> dict[str, str]:
        try:
            return {"text": documents.extract_text(name)}
        except documents.DocumentError as exc:
            raise HTTPException(422, str(exc)) from exc

    # ------------------------------------------------------------------- interface

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(config.STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return FileResponse(config.STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    return app


app = create_app()
