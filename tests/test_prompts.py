"""Consignes envoyées au modèle et découpage de sa réponse."""

from __future__ import annotations

import pytest

from app import prompts

PROFILE = {
    "full_name": "Clément Test", "email": "c@example.org", "phone": "06 12 34 56 78",
    "links": "https://github.com/c\nhttps://linkedin.com/in/c", "education": "Master Informatique",
    "internship": "", "cv_text": "Python, SQL", "signature": "", "smtp_from_email": "",
}
OFFER = {"title": "Stage Data", "company": "ACME", "location": "Lyon", "contract": "", "description": "Missions : SQL."}


def test_build_messages_french():
    messages = prompts.build_messages(PROFILE, OFFER, language="fr", instructions=["Sois bref", ""],
                                      attachments=["CV.pdf"], budget=5000)
    system, user = messages[0]["content"], messages[1]["content"]
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    assert "Objet :" in system and "N'invente rien" in system
    assert "Nom : Clément Test" in user
    assert "Liens : https://github.com/c ; https://linkedin.com/in/c" in user
    assert "Stage recherché" not in user  # champ vide omis
    assert "Contrat" not in user
    assert '"""\nMissions : SQL.\n"""' in user
    assert "## PIÈCES JOINTES : CV.pdf" in user
    assert user.endswith("## CONSIGNES SUPPLÉMENTAIRES\nSois bref\n\nRédige maintenant l'email de candidature en français.")
    assert "06 12 34 56 78" not in user  # coordonnées : ajoutées par la signature, pas par le modèle


def test_build_messages_english_without_description():
    messages = prompts.build_messages(PROFILE, {**OFFER, "description": None}, language="en",
                                      instructions=[], attachments=[], budget=5000)
    assert "Subject:" in messages[0]["content"]
    assert "Name: Clément Test" in messages[1]["content"]
    assert "(description unavailable" in messages[1]["content"]
    assert "## ATTACHMENTS : none" in messages[1]["content"]


def test_budget_helpers():
    assert prompts.char_budget(4096, 1200) == (4096 - 1200 - 700) * 3
    assert prompts.char_budget(None, 1200) == prompts.char_budget(4096, 1200)
    assert prompts.char_budget(2048, 2000) == 1200  # plancher
    assert prompts.split_budget(6000, "d" * 1000, "c" * 9000) == (1000, 5000)
    assert prompts.split_budget(6000, "d" * 9000, "c" * 9000) == (3600, 2400)
    assert prompts.split_budget(6000, "d" * 9000, "") == (6000, 0)


def test_truncate():
    text = "Phrase un. Phrase deux est longue. Phrase trois."
    assert prompts.truncate(text, 1000) == text
    assert prompts.truncate(text, 40) == "Phrase un. Phrase deux est longue.\n[…]"  # fin de phrase
    assert prompts.truncate("A" * 30 + "\n\n" + "B" * 30, 40) == "A" * 30 + "\n[…]"  # fin de paragraphe
    # Coupure trop tôt (avant 60 % de la limite) : on garde la limite exacte.
    assert prompts.truncate("Court.\n\n" + "x" * 50, 40) == "Court.\n\n" + "x" * 32 + "\n[…]"
    assert prompts.truncate("x" * 50, 10) == "x" * 10 + "\n[…]"
    assert prompts.truncate(None, 10) == ""


@pytest.mark.parametrize("raw, subject, body", [
    ("Objet : Candidature Stage Data\n\nMadame, Monsieur,\nTexte.", "Candidature Stage Data", "Madame, Monsieur,\nTexte."),
    ("**Objet :** Candidature\n\nCorps", "Candidature", "Corps"),
    ("Voici l'email :\nSubject: Application\n\nDear Hiring Manager,", "Application", "Dear Hiring Manager,"),
    ("```\nObjet: « Stage »\n\nTexte\n```", "Stage", "Texte"),
    ("<think>réflexion</think>\nObjet : A\n\nB", "A", "B"),
    ("réflexion sans balise ouvrante</think>Objet : A\n\nB", "A", "B"),
    ("Madame, Monsieur,\nSans objet.", "Candidature : Stage Data", "Madame, Monsieur,\nSans objet."),
    ("Objet : A\n\nCorps de l'email :\nTexte **gras**", "A", "Texte gras"),
])
def test_parse_email(raw, subject, body):
    assert prompts.parse_email(raw, fallback_subject="Candidature : Stage Data") == (subject, body)


def test_think_filter_split_tags():
    flt = prompts.ThinkFilter()
    pieces = ["Bon", "jour <thi", "nk>secret ", "</th", "ink> à tous", " <", "b>ok"]
    out = "".join(flt.feed(p) for p in pieces) + flt.flush()
    assert out == "Bonjour  à tous <b>ok"


def test_think_filter_unclosed():
    flt = prompts.ThinkFilter()
    assert flt.feed("A<think>jamais fermé") == "A"
    assert flt.flush() == ""


def test_signature():
    assert prompts.default_signature(PROFILE) == (
        "Clément Test\n06 12 34 56 78\nc@example.org\nhttps://github.com/c\nhttps://linkedin.com/in/c"
    )
    assert prompts.default_signature({**PROFILE, "signature": "  Clément  "}) == "Clément"
    body = "Cordialement,\n\nclement test\n[Votre téléphone]\n06 12 34 56 78\n"
    assert prompts.add_signature(body, "Clément Test\n06 12 34 56 78", "Clément Test") == (
        "Cordialement,\n\nClément Test\n06 12 34 56 78"
    )
    assert prompts.add_signature("Texte  \n", "", "") == "Texte"


def test_find_placeholders():
    assert prompts.find_placeholders("Bonjour [Nom],", "fin […] [Date] [Nom]") == ["[Nom]", "[Date]"]
    assert prompts.find_placeholders("Rien à signaler") == []
