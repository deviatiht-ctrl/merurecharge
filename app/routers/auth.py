from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db, get_setting, is_mock, set_setting
from ..security import (COOKIE_NAME, audit, client_ip, create_session,
                        current_session, destroy_session, hash_password,
                        login_rate_limited, password_is_set,
                        record_login_attempt, verify_password)
from ..models import Session as SessionRow

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PasswordBody(BaseModel):
    password: str = Field(min_length=8, max_length=200)


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    setup_required = not password_is_set(db)
    token = request.cookies.get(COOKIE_NAME)
    from ..security import get_session
    s = get_session(db, token)
    return {
        "setup_required": setup_required,
        "authenticated": s is not None,
        "csrf": s.csrf if s else None,
        "mock_mode": is_mock(db),
        "kill_switch": bool(get_setting(db, "kill_switch", False)),
    }


@router.post("/setup")
def setup(body: PasswordBody, request: Request, response: Response,
          db: Session = Depends(get_db)):
    if password_is_set(db):
        raise HTTPException(409, "Modpas la deja konfigire")
    set_setting(db, "password_hash", hash_password(body.password))
    db.commit()
    ip = client_ip(request)
    audit(db, "auth.setup", ip=ip)
    token, csrf = create_session(db, ip)
    _set_cookie(response, token)
    return {"ok": True, "csrf": csrf}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response,
          db: Session = Depends(get_db)):
    ip = client_ip(request)
    if login_rate_limited(db, ip):
        audit(db, "auth.rate_limited", ip=ip)
        raise HTTPException(429, "Twòp tantativ — tann 15 minit")
    hashed = get_setting(db, "password_hash")
    if not hashed or not verify_password(body.password, hashed):
        record_login_attempt(db, ip, False)
        audit(db, "auth.login_failed", ip=ip)
        raise HTTPException(401, "Modpas la pa kòrèk")
    record_login_attempt(db, ip, True)
    token, csrf = create_session(db, ip)
    _set_cookie(response, token)
    audit(db, "auth.login", ip=ip)
    return {"ok": True, "csrf": csrf, "mock_mode": is_mock(db)}


@router.post("/logout")
def logout(request: Request, response: Response,
           session: SessionRow = Depends(current_session),
           db: Session = Depends(get_db)):
    destroy_session(db, request.cookies.get(COOKIE_NAME, ""))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


def _set_cookie(response: Response, token: str):
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax",
        secure=bool(config.IS_VERCEL), max_age=12 * 3600,
    )
