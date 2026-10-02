"""Construction des emails et envoi SMTP (serveur SMTP local de test)."""

from __future__ import annotations

import email
import email.policy
import socket

import pytest
from aiosmtpd.controller import Controller

from app import mailer

SETTINGS = {
    "full_name": "Clément Test", "email": "clement@example.org", "smtp_from_name": "", "smtp_from_email": "",
    "smtp_username": "", "smtp_password": "", "smtp_host": "127.0.0.1", "smtp_port": 0,
    "smtp_security": "none", "smtp_bcc_self": False,
}


class Sink:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def smtp_server():
    sink = Sink()
    port = free_port()  # aiosmtpd n'accepte pas le port 0
    controller = Controller(sink, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield sink, port
    finally:
        controller.stop()


def test_parse_recipients():
    assert mailer.parse_recipients(" a@x.fr; b@y.fr , a@x.fr ") == ["a@x.fr", "b@y.fr"]
    with pytest.raises(mailer.MailError, match="invalide : pas-une-adresse"):
        mailer.parse_recipients("a@x.fr, pas-une-adresse")
    with pytest.raises(mailer.MailError, match="destinataire"):
        mailer.parse_recipients("  ")


def test_build_message(tmp_path):
    cv = tmp_path / "CV Clément.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    other = tmp_path / "notes.xyz1"
    other.write_bytes(b"data")
    msg = mailer.build_message({**SETTINGS, "smtp_bcc_self": True}, ["rh@acme.fr"],
                               "Candidature\nstage", "Bonjour,\r\nMerci.", [cv, other])
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    assert str(parsed["From"]) == "Clément Test <clement@example.org>"
    assert parsed["Subject"] == "Candidature stage"
    assert parsed["Bcc"] == "clement@example.org"
    assert parsed["Message-ID"].endswith("@example.org>")
    assert parsed.get_body(("plain",)).get_content() == "Bonjour,\nMerci.\n"
    attachments = {part.get_filename(): part.get_content_type() for part in parsed.iter_attachments()}
    assert attachments == {"CV Clément.pdf": "application/pdf", "notes.xyz1": "application/octet-stream"}


def test_sender_address_required():
    with pytest.raises(mailer.MailError, match="Adresse d'expédition"):
        mailer.build_message({**SETTINGS, "email": ""}, ["rh@acme.fr"], "S", "B", [])


def test_send_and_bcc(smtp_server, tmp_path):
    sink, port = smtp_server
    settings = {**SETTINGS, "smtp_port": port, "smtp_bcc_self": True}
    msg = mailer.build_message(settings, ["rh@acme.fr"], "Objet", "Corps", [])
    assert mailer.send(settings, msg) == []
    envelope = sink.messages[0]
    assert envelope.mail_from == "clement@example.org"
    assert sorted(envelope.rcpt_tos) == ["clement@example.org", "rh@acme.fr"]
    assert b"Bcc:" not in envelope.original_content  # copie cachée : absente des en-têtes transmis


def test_connection_errors_are_readable():
    settings = {**SETTINGS, "smtp_port": free_port()}
    with pytest.raises(mailer.MailError, match="Connexion au serveur SMTP impossible"):
        mailer.test_connection(settings)
    with pytest.raises(mailer.MailError, match="Serveur SMTP non renseigné"):
        mailer.test_connection({**SETTINGS, "smtp_host": ""})


def test_starttls_unsupported_is_readable(smtp_server):
    _, port = smtp_server
    with pytest.raises(mailer.MailError, match="non prise en charge"):
        mailer.test_connection({**SETTINGS, "smtp_port": port, "smtp_security": "starttls"})


def test_missing_password(smtp_server):
    _, port = smtp_server
    with pytest.raises(mailer.MailError, match="Mot de passe SMTP manquant"):
        mailer.test_connection({**SETTINGS, "smtp_port": port, "smtp_username": "moi"})


def test_gmail_app_password_pattern():
    assert mailer.GMAIL_APP_PASSWORD.match("abcd efgh ijkl mnop")
    assert not mailer.GMAIL_APP_PASSWORD.match("mot de passe normal")
