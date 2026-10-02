"""Client du serveur llama.cpp (``llama-server``), via son API compatible OpenAI.

Fonctionne aussi avec d'autres serveurs compatibles (Ollama, LM Studio, vLLM…) :
il suffit d'indiquer leur adresse dans les paramètres.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .scrapers.base import describe_http_error

START_HINT = (
    "Lancez-le par exemple avec : llama-server -m votre-modele.gguf --port 8080 "
    "(voir le README)."
)
GENERATION_TIMEOUT = httpx.Timeout(connect=10.0, read=900.0, write=60.0, pool=10.0)


class LLMError(Exception):
    """Erreur affichable à l'utilisateur."""


class ContextTooLong(LLMError):
    """La demande dépasse la taille de contexte du modèle : il faut raccourcir le prompt."""


def base_url(settings: dict[str, Any]) -> str:
    url = str(settings.get("llm_base_url") or "").strip().rstrip("/")
    if not url:
        raise LLMError("Adresse du serveur llama.cpp non renseignée (Paramètres > IA).")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    if url.endswith("/v1"):
        url = url[:-3]
    return url


def _headers(settings: dict[str, Any]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if settings.get("llm_api_key"):
        headers["Authorization"] = f"Bearer {settings['llm_api_key']}"
    return headers


def _error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        inner = payload.get("error", payload)
        if isinstance(inner, dict):
            return str(inner.get("message") or inner.get("type") or inner)
        return str(inner)
    return str(payload)


def _http_error(status: int, raw: bytes) -> LLMError:
    try:
        payload: Any = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        payload = raw.decode("utf-8", "replace")[:300]
    message = _error_text(payload)
    kind = payload.get("error", {}).get("type", "") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else ""
    if kind == "exceed_context_size_error" or "context size" in message or "context length" in message:
        return ContextTooLong(message)
    if status == 401:
        return LLMError("Le serveur refuse la clé API (HTTP 401) : vérifiez-la dans Paramètres > IA.")
    if status == 404:
        return LLMError(
            "Adresse introuvable sur le serveur (HTTP 404) : l'adresse configurée est-elle bien "
            "celle de llama-server ?"
        )
    if status == 503:
        return LLMError("Le serveur llama.cpp charge encore le modèle. Réessayez dans quelques secondes.")
    return LLMError(f"Erreur du serveur llama.cpp (HTTP {status}) : {message}")


async def server_info(settings: dict[str, Any], client: httpx.AsyncClient) -> dict[str, Any]:
    """Vérifie que le serveur répond ; renvoie les modèles disponibles et la taille de contexte."""
    url = base_url(settings)
    try:
        resp = await client.get(f"{url}/v1/models", headers=_headers(settings), timeout=10.0)
    except httpx.HTTPError as exc:
        raise LLMError(
            f"Impossible de joindre le serveur llama.cpp ({url}) : {describe_http_error(exc)}. {START_HINT}"
        ) from exc
    if resp.status_code != 200:
        raise _http_error(resp.status_code, resp.content)
    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMError(f"Réponse illisible de {url} : est-ce bien un serveur llama.cpp ?") from exc
    models = [
        m["id"] for m in (data.get("data") or [])
        if isinstance(m, dict) and isinstance(m.get("id"), str)
    ] if isinstance(data, dict) else []

    n_ctx = None
    try:
        props = await client.get(f"{url}/props", headers=_headers(settings), timeout=10.0)
        if props.status_code == 200:
            value = props.json().get("default_generation_settings", {}).get("n_ctx")
            if isinstance(value, int) and value > 0:
                n_ctx = value
    except (httpx.HTTPError, ValueError, AttributeError):
        pass  # /props est propre à llama.cpp : facultatif
    return {"base_url": url, "models": models, "n_ctx": n_ctx}


async def stream_chat(
    settings: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    model: str,
    client: httpx.AsyncClient,
) -> AsyncIterator[dict[str, Any]]:
    """Génère la réponse au fil de l'eau.

    Événements produits : ``{"type": "delta", "text"}`` (texte de la réponse),
    ``{"type": "reasoning", "text"}`` (réflexion d'un modèle à raisonnement) puis
    ``{"type": "end", "finish_reason"}``.
    """
    body: dict[str, Any] = {
        "messages": messages,
        "stream": True,
        "temperature": float(settings.get("llm_temperature", 0.7)),
        "max_tokens": int(settings.get("llm_max_tokens", 1200)),
    }
    if model:
        body["model"] = model
    if settings.get("llm_disable_thinking"):
        # Qwen3 & co : pas de phase de réflexion (ignoré par les autres modèles).
        body["chat_template_kwargs"] = {"enable_thinking": False}

    url = f"{base_url(settings)}/v1/chat/completions"
    try:
        async with client.stream(
            "POST", url, json=body, headers=_headers(settings), timeout=GENERATION_TIMEOUT
        ) as resp:
            if resp.status_code != 200:
                raise _http_error(resp.status_code, await resp.aread())
            finish_reason = None
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    error = _error_text(chunk)
                    if "context" in error:
                        raise ContextTooLong(error)
                    raise LLMError(f"Erreur du serveur llama.cpp : {error}")
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        yield {"type": "reasoning", "text": delta["reasoning_content"]}
                    if delta.get("content"):
                        yield {"type": "delta", "text": delta["content"]}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
            yield {"type": "end", "finish_reason": finish_reason}
    except httpx.TimeoutException as exc:
        raise LLMError("Le serveur llama.cpp ne répond plus (délai dépassé).") from exc
    except httpx.HTTPError as exc:
        raise LLMError(
            f"Connexion au serveur llama.cpp impossible ou interrompue : {describe_http_error(exc)}. {START_HINT}"
        ) from exc
