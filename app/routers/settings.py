from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db, get_setting, is_mock, set_setting
from ..models import utcnow
from ..security import (audit, client_ip, current_session, require_csrf,
                        verify_password)
from ..services.plop import PlopError
from ..services.wallet import WalletError
from .. import worker

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Kle yo jwenn an retou sèlman — PA JANM sekrè
SAFE_KEYS = [
    "reference_rate", "fee_pct", "fee_model", "fee_network", "fee_meru",
    "min_order_htg", "max_order_htg", "max_daily_htg", "loss_limit_pct",
    "float_reserve", "float_alert_below", "kill_switch", "mock_mode",
    "meru_address", "meru_memo", "meru_verified", "pending_meru",
    "wallet_asset", "wallet_network", "payment_timeout_min",
    "notify_enabled", "preflight", "typical_order_usdt",
]

EDITABLE_KEYS = {
    "reference_rate", "fee_pct", "fee_model", "fee_network", "fee_meru",
    "min_order_htg", "max_order_htg", "max_daily_htg", "loss_limit_pct",
    "float_reserve", "float_alert_below", "wallet_asset", "wallet_network",
    "payment_timeout_min", "notify_enabled", "typical_order_usdt",
}


@router.get("")
def get_settings(db: Session = Depends(get_db), _s=Depends(current_session)):
    out = {k: get_setting(db, k) for k in SAFE_KEYS}
    out["has_plop_credentials"] = bool(config.PLOP_CLIENT_ID)
    out["has_wallet_secret"] = bool(config.WALLET_SECRET)
    return out


@router.post("")
def update_settings(body: dict, request: Request,
                    db: Session = Depends(get_db), _s=Depends(require_csrf)):
    for k, v in body.items():
        if k not in EDITABLE_KEYS:
            continue
        if k == "fee_model" and v not in ("dedwi", "sou_tet"):
            raise HTTPException(400, "fee_model: 'dedwi' oswa 'sou_tet'")
        if k == "wallet_network" and v not in ("stellar", "trc20"):
            raise HTTPException(400, "wallet_network: 'stellar' oswa 'trc20'")
        set_setting(db, k, v)
    db.commit()
    audit(db, "settings.updated", ",".join(k for k in body if k in EDITABLE_KEYS),
          client_ip(request))
    return {"ok": True}


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/password")
def change_password(body: PasswordBody, request: Request,
                    db: Session = Depends(get_db), _s=Depends(require_csrf)):
    if not verify_password(body.current_password,
                           get_setting(db, "password_hash") or ""):
        raise HTTPException(401, "Modpas aktyèl la pa kòrèk")
    from ..security import hash_password
    set_setting(db, "password_hash", hash_password(body.new_password))
    db.commit()
    audit(db, "settings.password_changed", ip=client_ip(request))
    return {"ok": True}


class KillSwitchBody(BaseModel):
    on: bool


@router.post("/kill-switch")
def kill_switch(body: KillSwitchBody, request: Request,
                db: Session = Depends(get_db), _s=Depends(require_csrf)):
    set_setting(db, "kill_switch", body.on)
    db.commit()
    audit(db, "kill_switch.on" if body.on else "kill_switch.off",
          ip=client_ip(request))
    return {"ok": True, "kill_switch": body.on}


class MeruBody(BaseModel):
    address: str = Field(min_length=5, max_length=80)
    memo: str = Field(default="", max_length=64)
    verified: bool
    password: str


@router.post("/meru-address")
def set_meru(body: MeruBody, request: Request, db: Session = Depends(get_db),
             _s=Depends(require_csrf)):
    """Whitelist adrès Meru — mande modpas; chanjman an gen delè 10 minit."""
    ip = client_ip(request)
    if not verify_password(body.password, get_setting(db, "password_hash") or ""):
        audit(db, "meru.change_denied", ip=ip)
        raise HTTPException(401, "Modpas la pa kòrèk")
    if not get_setting(db, "meru_address"):
        # Premye adrès — aplike imedyatman
        set_setting(db, "meru_address", body.address)
        set_setting(db, "meru_memo", body.memo)
        set_setting(db, "meru_verified", body.verified)
        db.commit()
        audit(db, "meru.set", body.address, ip)
        return {"ok": True, "pending": False}
    # Chanjman — delè 10 minit + alèt
    pending = {"address": body.address, "memo": body.memo,
               "verified": body.verified,
               "activate_at": (utcnow() + timedelta(minutes=10)).isoformat()}
    set_setting(db, "pending_meru", pending)
    db.commit()
    audit(db, "meru.change_pending", body.address, ip)
    from ..services import notify
    notify.notify(f"ALÈT: chanjman adrès Meru pwograme -> {body.address[:12]}...")
    return {"ok": True, "pending": True, "activate_at": pending["activate_at"]}


def run_preflight(db) -> list[dict]:
    """Tès pre-vòl anvan mòd reyèl — retounen lis chèk yo."""
    from .. import config
    checks = []

    # 1. Kredansyèl PLOP yo mache
    try:
        real = worker.PlopClient()
        real.merchant_token()
        checks.append({"id": "plop_auth", "ok": True,
                       "label": "Koneksyon PLOP (auth/marchand)"})
    except PlopError as e:
        checks.append({"id": "plop_auth", "ok": False,
                       "label": "Koneksyon PLOP (auth/marchand)",
                       "error": str(e)})

    # 2. Wallet float konekte, solde > 0
    try:
        w = worker.get_real_wallet(
            get_setting(db, "wallet_asset", config.WALLET_ASSET),
            get_setting(db, "wallet_network", config.WALLET_NETWORK))
        bal = w.get_balance()
        checks.append({"id": "wallet", "ok": bal > 0,
                       "label": f"Wallet float ({bal} {w.address[:8]}...)",
                       "error": None if bal > 0 else "Solde = 0"})
    except WalletError as e:
        checks.append({"id": "wallet", "ok": False,
                       "label": "Wallet float", "error": str(e)})

    # 3. Adrès Meru anrejistre + verifye
    addr = get_setting(db, "meru_address", "")
    verified = bool(get_setting(db, "meru_verified", False))
    checks.append({"id": "meru", "ok": bool(addr) and verified,
                   "label": "Adrès Meru + verifikasyon rezo/memo",
                   "error": None if (addr and verified)
                   else "Anrejistre adrès la epi koche checkbox la"})

    # 4. Sekirite debaz
    checks.append({"id": "secrets", "ok": bool(config.SESSION_SECRET),
                   "label": "SESSION_SECRET konfigire"})

    set_setting(db, "preflight",
                {"at": utcnow().isoformat(),
                 "checks": checks,
                 "all_ok": all(c["ok"] for c in checks)})
    db.commit()
    return checks


@router.post("/preflight")
def preflight(db: Session = Depends(get_db), _s=Depends(require_csrf)):
    checks = run_preflight(db)
    return {"all_ok": all(c["ok"] for c in checks), "checks": checks}


class ModeBody(BaseModel):
    mode: str = Field(pattern="^(mock|real)$")
    confirm: bool = False
    meru_verified: bool = False


@router.post("/mode")
def switch_mode(body: ModeBody, request: Request,
                db: Session = Depends(get_db), _s=Depends(require_csrf)):
    ip = client_ip(request)
    if body.mode == "mock":
        set_setting(db, "mock_mode", True)
        db.commit()
        audit(db, "mode.mock", ip=ip)
        return {"ok": True, "mock_mode": True}

    # Pase an REYÈL — egzije konfimasyon + tès pre-vòl + checkbox Meru
    if not body.confirm:
        raise HTTPException(400, "Konfimasyon eksplisit obligatwa")
    if not body.meru_verified:
        raise HTTPException(400, "Ou dwe verifye rezo a ak memo a nan app Meru a")
    set_setting(db, "meru_verified", True)
    checks = run_preflight(db)
    failed = [c for c in checks if not c["ok"]]
    if failed:
        db.commit()
        audit(db, "mode.real_blocked",
              "; ".join(c["id"] for c in failed), ip)
        raise HTTPException(400, {
            "code": "PREFLIGHT_FAILED",
            "message": "Tès pre-vòl la echwe — mòd reyèl rete bloke",
            "checks": checks,
        })
    set_setting(db, "mock_mode", False)
    db.commit()
    audit(db, "mode.real", ip=ip)
    from ..services import notify
    notify.notify("MERU AUTO: MOD REYÈL aktive")
    return {"ok": True, "mock_mode": False, "checks": checks}


class TestSendBody(BaseModel):
    amount: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("10"))
    password: str


@router.post("/test-send")
def test_send(body: TestSendBody, request: Request,
              db: Session = Depends(get_db), _s=Depends(require_csrf)):
    """Voye tès opsyonèl sou adrès Meru a (lajan REYÈL — mande modpas)."""
    if not verify_password(body.password, get_setting(db, "password_hash") or ""):
        raise HTTPException(401, "Modpas la pa kòrèk")
    addr = get_setting(db, "meru_address", "")
    if not addr:
        raise HTTPException(400, "Adrès Meru a pa anrejistre")
    try:
        w = worker.get_real_wallet(
            get_setting(db, "wallet_asset"), get_setting(db, "wallet_network"))
        tx_hash = w.send(body.amount, addr, get_setting(db, "meru_memo") or None)
    except WalletError as e:
        raise HTTPException(400, str(e))
    audit(db, "meru.test_send", f"{body.amount} -> {addr[:12]}... tx={tx_hash}",
          client_ip(request))
    return {"ok": True, "tx_hash": tx_hash}
