"""Repérage des offres de stage en intelligence artificielle.

Chaque offre reçoit une liste de « thèmes IA » (LLM, vision, NLP…) déduite de son titre
et, quand elle est connue, de sa description. Une offre sans thème n'est pas une offre
en IA : elle est écartée de la recherche automatique et masquée de la liste quand le
filtre « stages en IA uniquement » est actif.
"""

from __future__ import annotations

import re
import unicodedata

# Requêtes lancées sur chaque site quand le filtre IA est actif et qu'aucun mot-clé
# n'est saisi : les sites ne font qu'une recherche plein texte, il faut donc plusieurs
# formulations pour couvrir le domaine.
DEFAULT_QUERIES = ("intelligence artificielle", "machine learning", "data scientist")

# (thème, motifs) sur du texte en minuscules sans accents. L'ordre est celui d'affichage :
# du plus spécifique au plus général.
TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("IA générative", (
        r"\bllms?\b", r"large language models?", r"\bgen ?ai\b", r"\bia gen\b",
        r"\b(ia|ai|intelligence artificielle) generative\b", r"\bgenerative (ai|ia)\b",
        r"\brag\b", r"retrieval[- ]augmented", r"\bprompt", r"\bchat ?bots?\b", r"\bchatgpt\b",
        r"\bagents? (ia|ai|intelligents?|conversationnels?)\b", r"\b(ai|ia) agents?\b",
        r"\blangchain\b", r"\bfine[- ]?tuning\b", r"\bgpt\b",
    )),
    ("NLP", (
        r"\bnlp\b", r"natural language", r"traitement (automatique )?du langage",
        r"\btal\b", r"speech", r"reconnaissance vocale", r"\btext mining\b",
    )),
    ("Vision", (
        r"computer vision", r"vision (par ordinateur|artificielle)", r"traitement d'?images?",
        r"image processing", r"reconnaissance d'?images?", r"\bocr\b",
        r"detection d'?objets?", r"object detection", r"segmentation d'?images?",
    )),
    ("Deep learning", (
        r"deep learning", r"apprentissage profond", r"reseaux? de neurones", r"neural networks?",
        r"\bpytorch\b", r"\btensorflow\b", r"\btransformers?\b(?! digital)", r"reinforcement learning",
        r"apprentissage par renforcement",
    )),
    ("MLOps", (r"\bmlops\b", r"\bml ?engineer", r"\bml ?ops\b")),
    ("Machine learning", (
        r"machine learning", r"apprentissage automatique", r"\bml\b", r"\bmachine-learning\b",
    )),
    ("Data science", (
        r"data scien", r"scientifique des donnees", r"\bdata ?scientist", r"\bdatascien",
    )),
    ("IA", (
        r"intelligence artificielle", r"artificial intelligence", r"\bi\.?a\b", r"\ba\.?i\b",
    )),
)

_COMPILED = [(topic, re.compile("|".join(patterns))) for topic, patterns in TOPICS]

# Dans un titre, « IA »/« AI » isolés suffisent ; dans une description, on évite les faux
# positifs (« j'ai », « Assistant(e) RH ai… ») en n'acceptant que les formes explicites.
_LOOSE_TOPIC = "IA"
_STRICT_IA = re.compile(r"intelligence artificielle|artificial intelligence|\b(ia|ai) (generative|engineer|developer|research)")

_LIGATURES = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "’": "'"})


def _fold(text: str | None) -> str:
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.translate(_LIGATURES))
    folded = "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", folded)


def _topics_in(text: str, *, strict: bool) -> list[str]:
    found = []
    for topic, regex in _COMPILED:
        if topic == _LOOSE_TOPIC and strict:
            if _STRICT_IA.search(text):
                found.append(topic)
        elif regex.search(text):
            found.append(topic)
    return found


def detect_topics(title: str | None, description: str | None = None) -> list[str]:
    """Thèmes IA d'une offre (liste vide = pas une offre en IA).

    Le titre fait foi ; la description n'ajoute des thèmes que si elle en parle au moins
    deux fois, pour ne pas retenir une offre de comptabilité qui cite « l'IA » en passant.
    """
    topics = _topics_in(_fold(title), strict=False)
    body = _fold(description)
    if body:
        for topic in _topics_in(body, strict=True):
            regex = dict(_COMPILED)[topic]
            hits = len(regex.findall(body)) if topic != _LOOSE_TOPIC else len(_STRICT_IA.findall(body))
            if topic not in topics and (topics or hits >= 2):
                topics.append(topic)
    order = [t for t, _ in TOPICS]
    topics = sorted(set(topics), key=order.index)
    # « IA » seul ne sert que si aucun thème plus précis n'a été trouvé.
    return [t for t in topics if t != _LOOSE_TOPIC] or topics


def is_ai_offer(title: str | None, description: str | None = None) -> bool:
    return bool(detect_topics(title, description))
