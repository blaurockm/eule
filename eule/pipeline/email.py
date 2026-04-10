"""
Email-Versand fuer Eule (Morning Brief, Reports).
"""

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(subject: str, body: str, html: bool = False) -> None:
    """Email senden via SMTP (Posteo).

    Liest Credentials aus Umgebungsvariablen (via .env).
    """
    host = os.environ.get("SMTP_HOST", "posteo.de")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("EMAIL_FROM", user)
    to_addr = os.environ.get("EMAIL_TO", "")

    if not user or not password or not to_addr:
        raise RuntimeError(
            "SMTP-Credentials nicht konfiguriert. "
            "Setze SMTP_USER, SMTP_PASS, EMAIL_TO in .env"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Eule <{from_addr}>"
    msg["To"] = to_addr

    if html:
        msg.attach(MIMEText(body, "html"))
    else:
        msg.attach(MIMEText(body, "plain"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls(context=ctx)
        smtp.login(user, password)
        smtp.send_message(msg)
