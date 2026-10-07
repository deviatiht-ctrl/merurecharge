import asyncio
import queue
import time
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, events, worker
from ..db import get_db, get_setting, is_mock
from ..models import AuditLog, MockPlopTx, Order, utcnow
from ..security import check_cron_secret, current_session

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    """Sèvi pou diagnostik — pa janm krache, rapòte eta baz done a."""
    from ..db import ENGINE_ERROR, engine
    from sqlalchemy import text
    out = {"ok": True, "time": utcnow().isoformat(),
           "vercel": config.IS_VERCEL, "db_error": None, "mock": None}
    if ENGINE_ERROR:
        out["ok"] = False
        out["db_error"] = ENGINE_ERROR
        return out
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        out["ok"] = False
        out["db_error"] = str(e)[:300]
        return out
    db = next(get_db())
    try:
        out["mock"] = is_mock(db)
    finally:
        db.close()
    return out


@router.get("/system/status")
def status(db: Session = Depends(get_db), _s=Depends(current_session)):
    today = utcnow() - timedelta(days=30)
    orders = db.query(Order).filter(
        Order.status == "confirmed", Order.confirmed_at >= today).all()
    total_htg = sum(Decimal(o.amount_htg) for o in orders)
    total_usdt = sum(Decimal(o.usdt_send or 0) for o in orders)
    avg_eff = (total_htg / total_usdt) if total_usdt > 0 else None
    # Pèt kimile vs referans
    ref = Decimal(str(get_setting(db, "reference_rate", "129")))
    loss = total_htg - total_usdt * ref
    return {
        "mock": is_mock(db),
        "kill_switch": bool(get_setting(db, "kill_switch", False)),
        "worker_local": not config.IS_VERCEL,
        "month": {
            "orders": len(orders),
            "total_htg": str(total_htg),
            "total_usdt": str(total_usdt),
            "avg_effective_rate": str(avg_eff.quantize(Decimal("0.0001"))) if avg_eff else None,
            "loss_htg": str(loss.quantize(Decimal("0.01"))),
        },
    }


@router.get("/system/monthly-loss")
def monthly_loss(db: Session = Depends(get_db), _s=Depends(current_session)):
    """Pèt kimile pa mwa pou grafik la."""
    orders = db.query(Order).filter(Order.status == "confirmed").all()
    by_month: dict[str, dict] = {}
    for o in orders:
        key = (o.confirmed_at or o.created_at).strftime("%Y-%m")
        m = by_month.setdefault(key, {"loss_htg": Decimal(0), "htg": Decimal(0),
                                      "usdt": Decimal(0), "count": 0})
        m["loss_htg"] += Decimal(str(o.loss_htg or 0))
        m["htg"] += Decimal(str(o.amount_htg))
        m["usdt"] += Decimal(str(o.usdt_send or 0))
        m["count"] += 1
    out = []
    for k in sorted(by_month):
        m = by_month[k]
        out.append({
            "month": k,
            "loss_htg": float(m["loss_htg"]),
            "avg_rate": float(m["htg"] / m["usdt"]) if m["usdt"] > 0 else None,
            "count": m["count"],
        })
    return out


@router.get("/events")
async def sse(request: Request):
    """Server-Sent Events — frontend la gen fallback polling si sa tonbe
    (serverless ka fèmen koneksyon an apre maxDuration)."""
    q = events.subscribe()

    async def gen():
        start = time.time()
        try:
            yield ": connected\n\n"
            while time.time() - start < 50:  # rekonèt apre 50 s
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.to_thread(q.get, True, 20)
                    yield events.format_sse(msg)
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.post("/cron/tick")
def cron_tick(request: Request, db: Session = Depends(get_db)):
    """Endpoint pou Vercel Cron — voye Authorization: Bearer $CRON_SECRET."""
    check_cron_secret(request)
    worker.run_tick(db)
    return {"ok": True}


# ---------- Paj peman mock (piblik, mock sèlman) ----------

def _get_mock_tx(db, tx_id: str) -> MockPlopTx:
    if not is_mock(db):
        raise HTTPException(403, "Disponib an mòd similasyon sèlman")
    tx = db.query(MockPlopTx).filter(
        MockPlopTx.transaction_id == tx_id).first()
    if tx is None:
        raise HTTPException(404, "Tranzaksyon pa jwenn")
    return tx


@router.get("/mock/pay/{tx_id}")
def mock_pay_info(tx_id: str, db: Session = Depends(get_db)):
    tx = _get_mock_tx(db, tx_id)
    return {"transaction_id": tx.transaction_id,
            "reference_id": tx.reference_id, "amount": tx.amount,
            "method": tx.method, "trans_status": tx.trans_status}


class MockPayBody(BaseModel):
    result: str  # ok | failed


@router.post("/mock/pay/{tx_id}")
def mock_pay_complete(tx_id: str, body: MockPayBody,
                      db: Session = Depends(get_db)):
    tx = _get_mock_tx(db, tx_id)
    if tx.trans_status != "no":
        raise HTTPException(400, "Tranzaksyon sa a deja fini")
    if body.result not in ("ok", "failed"):
        raise HTTPException(400, "result dwe 'ok' oswa 'failed'")
    tx.trans_status = body.result
    db.commit()
    # Trete lòd la tousuit — pa bezwen tann tick la
    try:
        worker.run_tick(db)
    except Exception:
        pass
    return {"ok": True, "trans_status": tx.trans_status}


@router.get("/system/tables")
def db_tables(db: Session = Depends(get_db), _s=Depends(current_session)):
    """Diagnostik: lis tablo yo ki egziste nan baz done a."""
    from sqlalchemy import inspect
    from ..db import engine
    return {"tables": sorted(inspect(engine).get_table_names()),
            "dialect": engine.dialect.name}


@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db),
              _s=Depends(current_session)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return [{"id": r.id, "action": r.action, "detail": r.detail, "ip": r.ip,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]
