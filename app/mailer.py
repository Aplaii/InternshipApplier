"""Construction et envoi des emails de candidature (SMTP)."""

from __future__ import annotations

import mimetypes
import re
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
SMTP_TIMEOUT = 30
GMAIL_APP_PASSWORD = re.compile(r"^[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}$")


class MailError(Exception):
    """Erreur affichable à l'utilisateur."""


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value or "")) and len(value) <= 254


def parse_recipients(value: str) -> list[str]:
    """« a@x.fr, b@y.fr » -> liste validée (séparateurs acceptés : virgule, point-virgule, espace)."""
    recipients = [r.strip() for r in re.split(r"[,;\s]+", value or "") if r.strip()]
    if not recipients:
        raise MailError("Indiquez l'adresse email du destinataire.")
    invalid = [r for r in recipients if not is_valid_email(r)]
    if invalid:
        raise MailError(f"Adresse email invalide : {', '.join(invalid)}")
    return list(dict.fromkeys(recipients))


def sender_address(settings: dict[str, Any]) -> tuple[str, str]:
    """(nom affiché, adresse) de l'expéditeur."""
    email = (settings.get("smtp_from_email") or settings.get("email") or settings.get("smtp_username") or "").strip()
    if not is_valid_email(email):
        raise MailError(
            "Adresse d'expédition manquante ou invalide : renseignez votre email dans Paramètres > Email."
        )
    name = (settings.get("smtp_from_name") or settings.get("full_name") or "").strip()
    return name, email


def build_message(
    settings: dict[str, Any],
    recipients: list[str],
    subject: str,
    body: str,
    attachments: list[Path],
) -> EmailMessage:
    name, email = sender_address(settings)
    local, domain = email.rsplit("@", 1)
    msg = EmailMessage()
    msg["From"] = Address(display_name=name, username=local, domain=domain)
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ").strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    if settings.get("smtp_bcc_self"):
        msg["Bcc"] = email  # retiré de l'en-tête transmis par smtplib, mais destinataire réel
    msg.set_content(body.replace("\r\n", "\n"))
    for path in attachments:
        ctype, encoding = mimetypes.guess_type(path.name)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def _connect(settings: dict[str, Any]) -> smtplib.SMTP:
    host = str(settings.get("smtp_host") or "").strip()
    if not host:
        raise MailError("Serveur SMTP non renseigné (Paramètres > Email).")
    port = int(settings.get("smtp_port") or 587)
    security = settings.get("smtp_security") or "starttls"
    context = ssl.create_default_context()
    if security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
    try:
        server.ehlo()
        if security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        username = str(settings.get("smtp_username") or "").strip()
        if username:
            password = str(settings.get("smtp_password") or "")
            if not password:
                raise MailError("Mot de passe SMTP manquant (Paramètres > Email).")
            if GMAIL_APP_PASSWORD.match(password.strip()):
                # Google affiche les mots de passe d'application par groupes de 4 lettres.
                password = password.replace(" ", "")
            server.login(username, password)
    except BaseException:
        server.close()
        raise
    return server


def _friendly(exc: BaseException) -> MailError:
    if isinstance(exc, MailError):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError(
            "Identifiants SMTP refusés. Avec Gmail, utilisez un « mot de passe d'application » "
            "(compte Google > Sécurité > Validation en deux étapes > Mots de passe des applications)."
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return MailError(f"Destinataire refusé par le serveur : {', '.join(exc.recipients)}")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return MailError(f"Option non prise en charge par le serveur SMTP : {exc}")
    if isinstance(exc, ssl.SSLError):
        return MailError(
            f"Erreur TLS ({getattr(exc, 'reason', None) or exc}). Vérifiez le port et la sécurité : "
            "587 + STARTTLS ou 465 + SSL."
        )
    if isinstance(exc, (TimeoutError, OSError)) and not isinstance(exc, smtplib.SMTPException):
        return MailError(f"Connexion au serveur SMTP impossible : {exc}")
    return MailError(f"Échec SMTP : {exc}")


def send(settings: dict[str, Any], msg: EmailMessage) -> list[str]:
    """Envoie le message. Renvoie la liste des destinataires refusés (vide si tout va bien)."""
    try:
        server = _connect(settings)
        try:
            refused = server.send_message(msg)
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                server.close()
    except Exception as exc:  # converti en message lisible
        raise _friendly(exc) from exc
    return sorted(refused)


def test_connection(settings: dict[str, Any]) -> None:
    """Connexion + authentification, sans rien envoyer."""
    try:
        server = _connect(settings)
        try:
            server.noop()
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                server.close()
    except Exception as exc:
        raise _friendly(exc) from exc
