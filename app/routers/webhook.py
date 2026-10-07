"""Webhook PLOP — sou Vercel URL piblik la fè l mache san tinèl.

Sekirite: X-Webhook-Signature = 'sha256=' + HMAC-SHA256(kò JSON brit,
client_secret). Siyati envalid -> 401, pa fè anyen.
Webhook yo best-effort: pou konfime yon peman nou toujou rele
paiement-verify — webhook la sèl pa janm deklanche yon voye.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..models import Order
from ..security import audit, verify_webhook_signature
from .. import worker

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


@router.post("/plop")
async def plop_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("x-webhook-signature", "")
    if not verify_webhook_signature(raw, sig, config.PLOP_CLIENT_SECRET):
        raise HTTPException(401, "Siyati envalid")

    try:
        event = json.loads(raw)
    except ValueError:
        return {"ok": True}  # reponn 2xx men pa trete

    event_type = event.get("event") or event.get("type") or ""
    tx = event.get("transaction") or {}
    reference = tx.get("reference_id")
    new_status = (tx.get("new_status") or tx.get("status") or "").lower()

    if reference:
        audit(db, "webhook.received", f"{event_type} ref={reference} st={new_status}")
        order = db.query(Order).filter(
            Order.reference_id == reference).first()
        if order and new_status == "ok":
            # Toujou verifye via paiement-verify anvan anyen
            worker.process_order_payment(db, order)

    return {"ok": True}
