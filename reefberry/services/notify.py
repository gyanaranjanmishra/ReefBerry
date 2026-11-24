"""Alert delivery: email over SMTP and push notifications through ntfy."""

from __future__ import annotations

import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

from ..db import Database

EMAIL_KEYS = [
    "email_enabled", "smtp_host", "smtp_port", "smtp_security",
    "smtp_username", "smtp_password", "email_from", "email_to",
]
NTFY_KEYS = ["ntfy_enabled", "ntfy_server", "ntfy_topic", "ntfy_token"]
SECRET_KEYS = {"smtp_password", "ntfy_token", "kasa_password"}

SMTP_PRESETS = {
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_security": "starttls"},
    "brevo": {"smtp_host": "smtp-relay.brevo.com", "smtp_port": 587, "smtp_security": "starttls"},
    "custom": {},
}


class NotifyError(Exception):
    pass


def send_email(db: Database, subject: str, body: str) -> None:
    host = db.get_setting("smtp_host")
    to = db.get_setting("email_to")
    if not host or not to:
        raise NotifyError("Email isn't set up yet: add a mail server and a recipient.")
    port = int(db.get_setting("smtp_port") or 587)
    security = db.get_setting("smtp_security") or "starttls"
    username = db.get_setting("smtp_username") or ""
    password = db.get_setting("smtp_password") or ""
    sender = db.get_setting("email_from") or username

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)

    ctx = ssl.create_default_context()
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            if security == "starttls":
                server.starttls(context=ctx)
        with server:
            if username:
                server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise NotifyError(
            "The mail server rejected the login. For Gmail, use an app password, not your normal password."
        ) from e
    except (OSError, smtplib.SMTPException) as e:
        raise NotifyError(f"Couldn't send email: {e}") from e


def send_ntfy(db: Database, title: str, body: str, priority: str = "high") -> None:
    server = (db.get_setting("ntfy_server") or "https://ntfy.sh").rstrip("/")
    topic = db.get_setting("ntfy_topic")
    if not topic:
        raise NotifyError("Push alerts aren't set up yet: add an ntfy topic.")
    req = urllib.request.Request(f"{server}/{topic}", data=body.encode(), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    req.add_header("Tags", "fish")
    token = db.get_setting("ntfy_token")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status >= 300:
                raise NotifyError(f"ntfy answered with status {resp.status}.")
    except OSError as e:
        raise NotifyError(f"Couldn't reach ntfy: {e}") from e


def send_alert(db: Database, subject: str, body: str) -> list[str]:
    """Send through every enabled channel. Returns a list of problems (empty = all sent)."""
    problems = []
    if db.get_setting("email_enabled"):
        try:
            send_email(db, subject, body)
        except NotifyError as e:
            problems.append(str(e))
    if db.get_setting("ntfy_enabled"):
        try:
            send_ntfy(db, subject, body)
        except NotifyError as e:
            problems.append(str(e))
    return problems
