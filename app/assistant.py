"""Rédaction d'un email de candidature par le modèle local (llama.cpp)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from . import llm, prompts


def fallback_subject(offer: dict[str, Any], language: str) -> str:
    prefix = "Application" if language == "en" else "Candidature"
    return f"{prefix} : {offer.get('title') or ''}".strip(" :")


async def generate_email(
    settings: dict[str, Any],
    offer: dict[str, Any],
    *,
    language: str,
    instructions: str,
    attachments: list[str],
    client: httpx.AsyncClient,
) -> AsyncIterator[dict[str, Any]]:
    """Événements pour l'interface : status, delta (texte en cours), reasoning, result.

    Lève ``llm.LLMError`` (message lisible) en cas de problème.
    """
    yield {"type": "status", "message": "Connexion au serveur llama.cpp…"}
    info = await llm.server_info(settings, client)
    model = str(settings.get("llm_model") or "").strip() or (info["models"][0] if info["models"] else "")
    yield {
        "type": "status",
        "message": "Rédaction en cours…",
        "model": model,
        "n_ctx": info["n_ctx"],
    }

    budget = prompts.char_budget(info["n_ctx"], int(settings.get("llm_max_tokens") or 1200))
    text = ""
    reasoning_chars = 0
    finish_reason = None
    for attempt in range(3):
        messages = prompts.build_messages(
            settings,
            offer,
            language=language,
            instructions=[str(settings.get("llm_extra_instructions") or ""), instructions],
            attachments=attachments,
            budget=budget,
        )
        think = prompts.ThinkFilter()
        try:
            async for event in llm.stream_chat(settings, messages, model=model, client=client):
                if event["type"] == "delta":
                    visible = think.feed(event["text"])
                    if visible:
                        text += visible
                        yield {"type": "delta", "text": visible}
                elif event["type"] == "reasoning":
                    reasoning_chars += len(event["text"])
                    yield {"type": "reasoning", "chars": reasoning_chars}
                elif event["type"] == "end":
                    finish_reason = event["finish_reason"]
        except llm.ContextTooLong as exc:
            if attempt == 2 or text:
                raise llm.LLMError(
                    "L'offre et votre CV sont trop longs pour la mémoire (contexte) du modèle. "
                    "Relancez llama-server avec un contexte plus grand (option -c 8192) "
                    "ou raccourcissez le texte du CV dans les paramètres."
                ) from exc
            budget = budget // 2
            yield {"type": "status", "message": "Texte trop long pour le modèle : nouvel essai en raccourcissant l'offre…"}
            continue
        tail = think.flush()
        if tail:
            text += tail
            yield {"type": "delta", "text": tail}
        break

    subject, body = prompts.parse_email(text, fallback_subject=fallback_subject(offer, language))
    if not body.strip():
        if reasoning_chars and finish_reason == "length":
            raise llm.LLMError(
                "Le modèle a utilisé toute sa limite de tokens pour « réfléchir ». Cochez « Désactiver "
                "la réflexion » ou augmentez « Longueur maximale » dans Paramètres > IA."
            )
        raise llm.LLMError("Le modèle n'a renvoyé aucun texte.")
    body = prompts.add_signature(
        body, prompts.default_signature(settings), str(settings.get("full_name") or "")
    )
    warnings = []
    if finish_reason == "length":
        warnings.append(
            "Le texte a été coupé car la limite de tokens est atteinte : relisez la fin ou augmentez "
            "« Longueur maximale » dans Paramètres > IA."
        )
    placeholders = prompts.find_placeholders(subject, body)
    if placeholders:
        warnings.append(
            f"Le modèle a laissé des champs à compléter ({', '.join(placeholders[:4])}) : "
            "remplacez-les avant d'envoyer."
        )
    yield {
        "type": "result",
        "subject": subject,
        "body": body,
        "warning": " ".join(warnings) or None,
        "model": model,
    }
