import csv
import io
import re
import time
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import config
from ..calc import CalcError, compute_quote, float_totals
from ..db import get_db, get_setting, is_mock
from ..models import FloatRefill, Order, utcnow
from ..security import audit, client_ip, current_session, require_csrf
from .. import worker
from ..services.wallet_mock import sent_usdt_total

router = APIRouter(prefix="/api/orders", tags=["orders"])

PHONE_RE = re.compile(r"^509\d{8}$")


def _dec(v, default="0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(default)


def _ref() -> str:
    return "RCH-" + utcnow().strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 10000:04d}"


def order_dict(o: Order) -> dict:
    return {
        "id": o.id,
        "reference_id": o.reference_id,
        "amount_htg": o.amount_htg,
        "method": o.method,
        "phone": o.phone,
        "status": o.status,
        "failure_reason": o.failure_reason,
        "locked_rate": o.locked_rate,
        "fee_pct": o.fee_pct,
        "fee_model": o.fee_model,
        "htg_net": o.htg_net,
        "usdt_gross": o.usdt_gross,
        "usdt_send": o.usdt_send,
        "effective_rate": o.effective_rate,
        "reference_rate": o.reference_rate,
        "loss_pct": o.loss_pct,
        "loss_htg": o.loss_htg,
        "asset": o.asset,
        "network": o.network,
        "dest_address": o.dest_address,
        "payment_url": o.payment_url,
        "plop_tx_id": o.plop_tx_id,
        "tx_hash": o.tx_hash,
        "send_attempts": o.send_attempts,
        "last_error": o.last_error,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        "sent_at": o.sent_at.isoformat() if o.sent_at else None,
        "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
        "expires_at": o.expires_at.isoformat() if o.expires_at else None,
    }


class QuoteBody(BaseModel):
    amount_htg: Decimal = Field(gt=0, le=Decimal("100000000"))


class CreateBody(BaseModel):
    amount_htg: Decimal = Field(gt=0, le=Decimal("100000000"))
    method: str = Field(pattern="^(moncash|moncash_ussd|natcash|kashpaw|carte|all)$")
    phone: str | None = None
    confirm_loss: bool = False


def _build_quote(db, amount: Decimal):
    refills = db.query(FloatRefill).all()
    totals = float_totals(refills, sent_usdt_total(db))
    if totals["avg_rate"] is None:
        raise HTTPException(400, "Float la vid — ranpli l anvan ou fè rechaj")
    network = get_setting(db, "wallet_network", "stellar")
    try:
        quote = compute_quote(
            amount, totals["avg_rate"],
            fee_pct=_dec(get_setting(db, "fee_pct", "3")),
            fee_model=get_setting(db, "fee_model", "dedwi"),
            fee_network=_dec(get_setting(db, "fee_network", "0")),
            fee_meru=_dec(get_setting(db, "fee_meru", "0")),
            reference=_dec(get_setting(db, "reference_rate", "129")),
            loss_limit_pct=_dec(get_setting(db, "loss_limit_pct", "4")),
            network=network,
        )
    except CalcError as e:
        raise HTTPException(400, str(e))
    return quote, totals


@router.post("/quote")
def quote(body: QuoteBody, db: Session = Depends(get_db),
          _s=Depends(current_session)):
    q, totals = _build_quote(db, body.amount_htg)
    reserve = _dec(get_setting(db, "float_reserve", "5"))
    out = q.as_dict()
    out["float_units"] = str(totals["units"])
    out["float_sufficient"] = totals["units"] >= q.usdt_send + reserve
    return out


@router.post("")
def create_order(body: CreateBody, request: Request,
                 db: Session = Depends(get_db), _s=Depends(require_csrf)):
    ip = client_ip(request)
    min_htg = _dec(get_setting(db, "min_order_htg", "20"))
    if body.amount_htg < min_htg:
        raise HTTPException(400, f"Montan minimòm se {min_htg} HTG")
    if body.method == "moncash_ussd" and not (
            body.phone and PHONE_RE.match(body.phone)):
        raise HTTPException(400, "Nimewo telefòn obligatwa, fòma 509XXXXXXXX")

    quote, totals = _build_quote(db, body.amount_htg)

    # Pèt maksimòm — refize sof konfimasyon eksplisit
    if not quote.within_loss_limit and not body.confirm_loss:
        raise HTTPException(422, {
            "code": "LOSS_LIMIT",
            "message": f"Pèt la ({quote.loss_pct}%) depase limit la",
            "quote": quote.as_dict(),
        })

    # Float dwe kouvri voye a + rezèv la
    reserve = _dec(get_setting(db, "float_reserve", "5"))
    if totals["units"] < quote.usdt_send + reserve:
        raise HTTPException(
            400, f"Float ensifizan: {totals['units']} USDT disponib, "
                 f"bezwen {quote.usdt_send} + rezèv {reserve}")

    meru_address = get_setting(db, "meru_address", "")
    if not meru_address:
        raise HTTPException(400, "Adrès Meru a pa anrejistre (Paramèt)")

    order = Order(
        reference_id=_ref(),
        amount_htg=str(body.amount_htg),
        method=body.method,
        phone=body.phone,
        status="created",
        locked_rate=str(quote.taux_float),
        fee_pct=str(quote.fee_pct),
        fee_model=quote.fee_model,
        htg_net=str(quote.htg_net),
        usdt_gross=str(quote.usdt_gross),
        usdt_send=str(quote.usdt_send),
        effective_rate=str(quote.effective_rate),
        reference_rate=str(quote.reference),
        loss_pct=str(quote.loss_pct),
        loss_htg=str(quote.loss_htg),
        asset=get_setting(db, "wallet_asset", "USDC"),
        network=get_setting(db, "wallet_network", "stellar"),
        dest_address=meru_address,
        dest_memo=get_setting(db, "meru_memo", ""),
        expires_at=utcnow() + timedelta(
            minutes=int(get_setting(db, "payment_timeout_min",
                                    config.PAYMENT_TIMEOUT_MIN))),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    audit(db, "order.created",
          f"{order.reference_id} {order.amount_htg} HTG", ip)

    # Limit yo — depase -> needs_approval (pa kreye peman PLOP a ankò)
    max_order = _dec(get_setting(db, "max_order_htg", "50000"))
    max_daily = _dec(get_setting(db, "max_daily_htg", "200000"))
    reasons = []
    if body.amount_htg > max_order:
        reasons.append(f"depase limit pa lòd ({max_order} HTG)")
    if worker.daily_htg_total(db) > max_daily:
        reasons.append(f"depase limit pa jou ({max_daily} HTG)")
    if reasons:
        order.status = "needs_approval"
        order.failure_reason = "; ".join(reasons)
        db.commit()
        audit(db, "order.needs_approval", order.reference_id)
        worker._publish_order(order)
        return order_dict(order)

    worker.start_payment(db, order)
    return order_dict(order)


@router.get("")
def list_orders(status: str | None = Query(None),
                limit: int = Query(100, le=500),
                db: Session = Depends(get_db), _s=Depends(current_session)):
    q = db.query(Order).order_by(Order.id.desc())
    if status:
        q = q.filter(Order.status == status)
    return [order_dict(o) for o in q.limit(limit).all()]


@router.get("/export.csv")
def export_csv(db: Session = Depends(get_db), _s=Depends(current_session)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "reference_id", "amount_htg", "method", "status",
                "usdt_send", "effective_rate", "loss_pct", "loss_htg",
                "tx_hash", "created_at", "confirmed_at"])
    for o in db.query(Order).order_by(Order.id).all():
        w.writerow([o.id, o.reference_id, o.amount_htg, o.method, o.status,
                    o.usdt_send, o.effective_rate, o.loss_pct, o.loss_htg,
                    o.tx_hash, o.created_at, o.confirmed_at])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders.csv"})


def _get_order(db, oid: int) -> Order:
    o = db.get(Order, oid)
    if o is None:
        raise HTTPException(404, "Lòd pa jwenn")
    return o


@router.get("/{oid}")
def get_order(oid: int, db: Session = Depends(get_db),
              _s=Depends(current_session)):
    o = _get_order(db, oid)
    # Lazy polling — sou serverless sa ranplase worker la
    now = utcnow()
    if (o.status in ("created", "awaiting_payment")
            and (o.next_verify_at is None or o.next_verify_at <= now)):
        worker.process_order_payment(db, o)
        db.refresh(o)
    if o.status == "sent":
        worker.confirm_sent(db, o)
        db.refresh(o)
    return order_dict(o)


@router.post("/{oid}/approve")
def approve(oid: int, request: Request, db: Session = Depends(get_db),
            _s=Depends(require_csrf)):
    o = _get_order(db, oid)
    if o.status != "needs_approval":
        raise HTTPException(400, "Lòd sa a pa nan estati needs_approval")
    o.approved = True
    o.status = "created"
    o.failure_reason = None
    db.commit()
    audit(db, "order.approved", o.reference_id, client_ip(request))
    worker.start_payment(db, o)
    return order_dict(o)


@router.post("/{oid}/reject")
def reject(oid: int, request: Request, db: Session = Depends(get_db),
           _s=Depends(require_csrf)):
    o = _get_order(db, oid)
    if o.status != "needs_approval":
        raise HTTPException(400, "Lòd sa a pa nan estati needs_approval")
    o.status = "cancelled"
    o.failure_reason = "Rejte pa itilizatè a"
    db.commit()
    audit(db, "order.rejected", o.reference_id, client_ip(request))
    worker._publish_order(o)
    return order_dict(o)


@router.post("/{oid}/retry-send")
def retry_send(oid: int, request: Request, db: Session = Depends(get_db),
               _s=Depends(require_csrf)):
    o = _get_order(db, oid)
    if o.status != "send_failed":
        raise HTTPException(400, "Sèlman lòd 'send_failed' ka reeseye")
    o.status = "paid"
    o.failure_reason = None
    o.send_attempts = 0
    db.commit()
    audit(db, "order.retry_send", o.reference_id, client_ip(request))
    worker.try_send(db, o)
    db.refresh(o)
    return order_dict(o)


@router.post("/{oid}/mock-fail")
def mock_fail(oid: int, db: Session = Depends(get_db),
              _s=Depends(require_csrf)):
    if not is_mock(db):
        raise HTTPException(400, "Disponib an mòd similasyon sèlman")
    o = _get_order(db, oid)
    worker.get_plop(db).fail_payment(o.reference_id)
    worker.process_order_payment(db, o)
    db.refresh(o)
    return order_dict(o)


@router.post("/{oid}/mock-accelerate")
def mock_accelerate(oid: int, db: Session = Depends(get_db),
                    _s=Depends(require_csrf)):
    if not is_mock(db):
        raise HTTPException(400, "Disponib an mòd similasyon sèlman")
    o = _get_order(db, oid)
    worker.get_plop(db).accelerate(o.reference_id)
    worker.process_order_payment(db, o)
    db.refresh(o)
    return order_dict(o)
