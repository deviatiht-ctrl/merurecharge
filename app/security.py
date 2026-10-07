import hashlib
import hmac
import secrets
from datetime import timedelta

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from . import config
from .db import get_db, get_setting
from .models import AuditLog, LoginAttempt, Session, utcnow

SESSION_TTL_H = 12
LOGIN_WINDOW_S = 900
LOGIN_MAX_ATTEMPTS = 5
COOKIE_NAME = "meru_session"


# ---------- modpas ----------

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def password_is_set(db) -> bool:
    return bool(get_setting(db, "password_hash"))


# ---------- sesyon ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, ip: str) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.add(Session(
        token_hash=_hash_token(token),
        csrf=csrf,
        ip=ip,
        expires_at=utcnow() + timedelta(hours=SESSION_TTL_H),
    ))
    db.commit()
    return token, csrf


def destroy_session(db, token: str):
    s = db.get(Session, _hash_token(token))
    if s:
        db.delete(s)
        db.commit()


def get_session(db, token: str | None) -> Session | None:
    if not token:
        return None
    s = db.get(Session, _hash_token(token))
    if s is None or s.expires_at < utcnow():
        return None
    return s


def current_session(request: Request, db: OrmSession = Depends(get_db)) -> Session:
    s = get_session(db, request.cookies.get(COOKIE_NAME))
    if s is None:
        raise HTTPException(401, "Ou pa konekte")
    return s


def require_csrf(request: Request, session: Session = Depends(current_session)):
    """Verifye CSRF pou tout aksyon ki modifye — header X-CSRF-Token."""
    token = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(token, session.csrf):
        raise HTTPException(403, "CSRF token envalid")
    return session


# ---------- rate limit login ----------

def login_rate_limited(db, ip: str) -> bool:
    since = utcnow() - timedelta(seconds=LOGIN_WINDOW_S)
    n = db.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.ip == ip,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= since,
        )
    )
    return n >= LOGIN_MAX_ATTEMPTS


def record_login_attempt(db, ip: str, success: bool):
    db.add(LoginAttempt(ip=ip, success=success))
    db.commit()


# ---------- siyati / HMAC ----------

def verify_webhook_signature(raw_body: bytes, header: str, secret: str) -> bool:
    """X-Webhook-Signature = 'sha256=' + HMAC-SHA256(kò brit, client_secret)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def withdrawal_signature(amount: str, method: str, recipient: str,
                         reference: str, timestamp: int, secret: str) -> str:
    """HMAC-SHA256('amount|method|recipient|reference|timestamp', secret), hex.
    Atansyon: valè yo dwe nan MENM fòma egzat nan siyati a ak nan kò JSON la."""
    payload = f"{amount}|{method}|{recipient}|{reference}|{timestamp}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def check_cron_secret(request: Request):
    if not config.CRON_SECRET:
        raise HTTPException(503, "CRON_SECRET pa konfigire")
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {config.CRON_SECRET}":
        raise HTTPException(401, "Non otorize")


# ---------- odit ----------

def audit(db, action: str, detail: str = "", ip: str = ""):
    """Jounal odit imuable — pa janm mete sekrè nan 'detail'."""
    db.add(AuditLog(action=action, detail=detail, ip=ip))
    db.commit()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"
