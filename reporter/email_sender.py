"""
Sends the HTML digest email via Gmail SMTP (TLS, port 587).

Requires env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, REPORT_TO
All loaded from scraper.config.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from scraper import config

logger = logging.getLogger(__name__)


def send(subject: str, html_body: str) -> None:
    """
    Send an HTML email to all recipients in config.REPORT_TO.

    Raises:
        RuntimeError: if SMTP credentials are not configured.
        smtplib.SMTPException: on any send failure (caller decides whether to abort).
    """
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP_USER and SMTP_PASSWORD must be set in .env to send email reports."
        )

    recipients = [r.strip() for r in config.REPORT_TO.split(",") if r.strip()]
    if not recipients:
        raise RuntimeError("REPORT_TO is empty — add at least one recipient email.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    logger.info("Sending email to %s via %s:%s …", recipients, config.SMTP_HOST, config.SMTP_PORT)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.sendmail(config.SMTP_USER, recipients, msg.as_string())

    logger.info("Email sent successfully.")
