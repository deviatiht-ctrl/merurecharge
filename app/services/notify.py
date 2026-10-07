"""Notifikasyon opsyonèl — Telegram oswa SMTP. Pa janm bloke flux la."""
import smtplib
from email.mime.text import MIMEText

import httpx

from .. import config


def notify(text: str):
    try:
        _send(text)
    except Exception:
        pass  # notifikasyon pa janm fè sistèm nan echwe


def _send(text: str):
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        httpx.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
        }, timeout=10)
        return
    if config.SMTP_HOST and config.SMTP_TO:
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = "Meru Auto Recharge"
        msg["From"] = config.SMTP_FROM or config.SMTP_USER
        msg["To"] = config.SMTP_TO
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASS)
            s.send_message(msg)
