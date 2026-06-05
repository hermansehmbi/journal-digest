"""
Anesthesia Journal Digest — Email Sender
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, html_body: str,
               from_email: str | None = None, attachments: list[str] | None = None):
    """
    Send HTML email via Gmail SMTP.

    Env vars needed: GMAIL_ADDRESS, GMAIL_APP_PASSWORD
    If not set, saves email as local HTML file for preview.
    """
    smtp_user = os.environ.get("GMAIL_ADDRESS", from_email or "")
    smtp_password = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not smtp_user or not smtp_password:
        logger.warning("SMTP not configured — saving email locally for preview")
        _save_local(subject, html_body)
        return False

    try:
        from config import BRAND_SHORT
        _from_name = f"{BRAND_SHORT} Digest"
    except Exception:
        _from_name = "Journal Digest"

    msg = MIMEMultipart("mixed")
    msg["From"] = f"{_from_name} <{smtp_user}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for filepath in (attachments or []):
        path = Path(filepath)
        if path.exists():
            part = MIMEBase("application", "octet-stream")
            part.set_payload(path.read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={path.name}")
            msg.attach(part)
            logger.info(f"Attached: {path.name}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        logger.info(f"✅ Sent to {to_email}: {subject}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail auth failed — use an App Password, not your regular password")
        return False
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False


def _save_local(subject: str, html_body: str):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    f = f"preview_{ts}.html"
    Path(f).write_text(html_body, encoding="utf-8")
    logger.info(f"Preview saved: {f} — open in browser to view")
