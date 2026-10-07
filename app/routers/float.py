import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..calc import amount_str, float_totals
from ..db import get_db, get_setting, is_mock
from ..models import FloatRefill, Withdrawal, utcnow
from ..security import audit, client_ip, current_session, require_csrf
from ..services.plop import PlopError
from ..services.wallet_mock import sent_usdt_total
from .. import worker

router = APIRouter(prefix="/api/float", tags=["float"])


def _dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(default)


def refill_dict(r: FloatRefill) -> dict:
    return {
        "id": r.id, "htg_spent": r.htg_spent,
        "usdt_received": r.usdt_received, "rate": r.rate,
        "source": r.source, "tx_hash": r.tx_hash, "note": r.note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("")
def float_status(db: Session = Depends(get_db), _s=Depends(current_session)):
    refills = db.query(FloatRefill).all()
    totals = float_totals(refills, sent_usdt_total(db))
    threshold = _dec(get_setting(db, "float_alert_below", "25"))
    # Estimasyon konbyen rechaj tipik ki rete (baze sou dènye taux la)
    typical = _dec(get_setting(db, "typical_order_usdt", "5"))
    remaining = int(totals["units"] // typical) if typical > 0 else 0
    # Balans PLOP (mock oswa best-effort reyèl)
    plop_balance = None
    if is_mock(db):
        plop_balance = get_setting(db, "mock_plop_balance", "100000")
    else:
        try:
            info = worker.get_plop(db).merchant_info()
            plop_balance = info.get("balance")
        except Exception:
            plop_balance = None
    return {
        "units": str(totals["units"]),
        "cost_htg": str(totals["cost"]),
        "avg_rate": str(totals["avg_rate"]) if totals["avg_rate"] else None,
        "total_refilled_usdt": str(totals["total_usdt"]),
        "total_sent_usdt": str(sent_usdt_total(db)),
        "threshold": str(threshold),
        "low": totals["units"] < threshold,
        "remaining_recharges": remaining,
        "plop_balance": plop_balance,
        "asset": get_setting(db, "wallet_asset", "USDC"),
        "network": get_setting(db, "wallet_network", "stellar"),
        "mock": is_mock(db),
    }


class RefillBody(BaseModel):
    htg_spent: Decimal = Field(gt=0, le=Decimal("100000000"))
    usdt_received: Decimal = Field(gt=0, le=Decimal("10000000"))
    source: str = Field(default="p2p", max_length=40)
    tx_hash: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)


@router.post("/refills")
def add_refill(body: RefillBody, request: Request,
               db: Session = Depends(get_db), _s=Depends(require_csrf)):
    rate = (body.htg_spent / body.usdt_received).quantize(Decimal("0.0001"))
    r = FloatRefill(htg_spent=str(body.htg_spent),
                    usdt_received=str(body.usdt_received),
                    rate=str(rate), source=body.source,
                    tx_hash=body.tx_hash, note=body.note)
    db.add(r)
    db.commit()
    audit(db, "float.refill",
          f"{body.usdt_received} USDT @ {rate} ({body.source})",
          client_ip(request))
    return refill_dict(r)


@router.get("/refills")
def list_refills(db: Session = Depends(get_db), _s=Depends(current_session)):
    rows = db.query(FloatRefill).order_by(FloatRefill.id.desc()).limit(200).all()
    return [refill_dict(r) for r in rows]


class WithdrawBody(BaseModel):
    amount: Decimal = Field(gt=0, le=Decimal("100000000"))
    method: str = Field(pattern="^(moncash|natcash)$")
    recipient: str = Field(min_length=3, max_length=40)


@router.post("/withdraw")
def withdraw(body: WithdrawBody, request: Request,
             db: Session = Depends(get_db), _s=Depends(require_csrf)):
    """Retire HTG nan balans PLOP — flux 3 etap la, tout nan backend."""
    ip = client_ip(request)
    ref = "WD-" + utcnow().strftime("%Y%m%d") + "-" + secrets.token_hex(3)
    amt = amount_str(body.amount)
    w = Withdrawal(reference=ref, amount=amt,
                   method=body.method, recipient=body.recipient)
    db.add(w)
    db.commit()
    try:
        res = worker.get_plop(db).withdraw(amt, body.method, body.recipient, ref)
        w.status = res.get("transfer_status") or "pending"
        w.detail = res.get("message", "")
    except PlopError as e:
        w.status = "failed"
        w.detail = str(e)
    db.commit()
    audit(db, "float.withdraw", f"{ref} {body.amount} HTG -> {body.method}", ip)
    if w.status == "failed":
        raise HTTPException(400, w.detail)
    return {"reference": w.reference, "status": w.status, "detail": w.detail}


@router.get("/withdrawals")
def list_withdrawals(db: Session = Depends(get_db),
                     _s=Depends(current_session)):
    rows = db.query(Withdrawal).order_by(Withdrawal.id.desc()).limit(100).all()
    return [{"reference": w.reference, "amount": w.amount, "method": w.method,
             "recipient": w.recipient, "status": w.status,
             "detail": w.detail,
             "created_at": w.created_at.isoformat() if w.created_at else None}
            for w in rows]
