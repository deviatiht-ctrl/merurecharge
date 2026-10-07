"""Worker — flux otomatik la.

Sou lokal: yon thread rele run_tick() chak WORKER_INTERVAL_S segonn.
Sou Vercel (serverless): run_tick() rele pa /api/cron/tick, pa webhook la,
epi pa frontend la lè li konsilte yon lòd aktif (lazy polling).

Idempotans: transisyon 'paid'->'sending' la se yon UPDATE atomik
(WHERE status='paid') — de worker/reqèt ka pa janm voye de fwa.
"""
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update

from . import config, events
from .calc import float_totals
from .db import SessionLocal, get_setting, is_mock, set_setting
from .models import FloatRefill, Order, utcnow
from .security import audit
from .services import notify
from .services.plop import PlopClient, PlopError
from .services.plop_mock import PlopMockClient
from .services.wallet import WalletError, get_real_wallet
from .services.wallet_mock import MockWallet, sent_usdt_total

D = Decimal


def get_plop(db):
    if is_mock(db):
        return PlopMockClient(db)
    return PlopClient()


def get_wallet(db):
    if is_mock(db):
        return MockWallet(db, get_setting(db, "wallet_network", "stellar"))
    return get_real_wallet(
        get_setting(db, "wallet_asset", config.WALLET_ASSET),
        get_setting(db, "wallet_network", config.WALLET_NETWORK),
    )


def float_units(db) -> Decimal:
    """Inite USDT float la dapre kontablite a (menm sou mòd reyèl)."""
    refills = db.query(FloatRefill).all()
    return float_totals(refills, sent_usdt_total(db))["units"]


def _dec(v) -> Decimal:
    return D(str(v))


def _publish_order(order: Order):
    events.publish("order", {
        "id": order.id, "reference_id": order.reference_id,
        "status": order.status, "tx_hash": order.tx_hash,
        "failure_reason": order.failure_reason,
    })


# ---------- kreyasyon peman ----------

def start_payment(db, order: Order, plop=None) -> tuple[bool, str]:
    """Kreye tranzaksyon PLOP la -> 'awaiting_payment'."""
    plop = plop or get_plop(db)
    try:
        res = plop.create_payment(order.reference_id, order.amount_htg,
                                  order.method, order.phone)
    except PlopError as e:
        order.status = "failed_payment"
        order.failure_reason = f"PLOP: {e}"
        db.commit()
        audit(db, "order.payment_failed", f"{order.reference_id}: {e}")
        _publish_order(order)
        return False, str(e)
    order.plop_tx_id = res.get("transaction_id")
    order.payment_url = res.get("url")
    order.status = "awaiting_payment"
    order.next_verify_at = utcnow()
    db.commit()
    audit(db, "order.awaiting_payment", order.reference_id)
    _publish_order(order)
    return True, res.get("url") or ""


# ---------- konfimasyon peman ----------

def process_order_payment(db, order: Order, plop=None):
    """Verifye peman an; si 'ok' + montan/ref matche -> paid -> voye.
    Webhook la pa janm ase — toujou rele paiement-verify."""
    if order.status not in ("created", "awaiting_payment"):
        return
    plop = plop or get_plop(db)
    try:
        res = plop.verify_payment(order.reference_id)
    except PlopError as e:
        order.next_verify_at = utcnow() + timedelta(seconds=config.POLL_INTERVAL_S)
        order.last_error = f"verify: {e}"
        db.commit()
        return

    trans_status = str(res.get("trans_status", "no")).lower()
    if trans_status == "ok":
        paid_amount = _dec(res.get("montant") or 0)
        if paid_amount != _dec(order.amount_htg):
            order.status = "failed_payment"
            order.failure_reason = (
                f"Montan pa koresponn: atann {order.amount_htg}, "
                f"resevwa {paid_amount}")
            db.commit()
            audit(db, "order.amount_mismatch",
                  f"{order.reference_id}: {paid_amount} != {order.amount_htg}")
            _publish_order(order)
            return
        # Tranzisyon atomik awaiting_payment -> paid (yon sèl patwon)
        n = db.execute(
            update(Order).where(Order.id == order.id,
                                Order.status.in_(("created", "awaiting_payment")))
            .values(status="paid", paid_at=utcnow(), updated_at=utcnow())
        ).rowcount
        db.commit()
        if n == 1:
            db.refresh(order)
            audit(db, "order.paid", order.reference_id)
            _publish_order(order)
            notify.notify(f"Peman konfime: {order.reference_id} ({order.amount_htg} HTG)")
            try_send(db, order)
    elif trans_status == "failed":
        order.status = "failed_payment"
        order.failure_reason = "Peman an echwe"
        db.commit()
        audit(db, "order.failed_payment", order.reference_id)
        _publish_order(order)
        notify.notify(f"Peman echwe: {order.reference_id}")
    else:
        order.next_verify_at = utcnow() + timedelta(seconds=config.POLL_INTERVAL_S)
        db.commit()


# ---------- voye crypto ----------

def try_send(db, order: Order, wallet=None):
    """paid -> sending -> sent. Atomik, idempotent."""
    if get_setting(db, "kill_switch", False):
        return  # rete 'paid' — voye lè switch la dekoche

    # Verifye float la anvan tranzisyon an
    send_amount = _dec(order.usdt_send or 0)
    reserve = _dec(get_setting(db, "float_reserve", "5"))
    units = float_units(db)
    if units < send_amount + reserve:
        order.status = "send_failed"
        order.failure_reason = (
            f"Float ensifizan: rete {units} USDT, bezwen {send_amount} + rezèv {reserve}")
        db.commit()
        audit(db, "order.send_failed_float", order.reference_id)
        _publish_order(order)
        notify.notify(f"FLOAT ENSIFIZAN: {order.reference_id} pa voye")
        return

    # Tranzisyon atomik — si yon lòt pwosesis deja pran l, rowcount = 0
    n = db.execute(
        update(Order).where(Order.id == order.id, Order.status == "paid")
        .values(status="sending", updated_at=utcnow())
    ).rowcount
    db.commit()
    if n != 1:
        return
    db.refresh(order)
    _publish_order(order)
    _do_send(db, order, wallet)


def _do_send(db, order: Order, wallet=None):
    try:
        wallet = wallet or get_wallet(db)
        tx_hash = wallet.send(_dec(order.usdt_send), order.dest_address,
                              order.dest_memo or None)
        order.tx_hash = tx_hash
        order.status = "sent"
        order.sent_at = utcnow()
        db.commit()
        audit(db, "order.sent", f"{order.reference_id} tx={tx_hash}")
        _publish_order(order)
        notify.notify(f"Voye reyisi: {order.usdt_send} {order.asset} -> Meru ({order.reference_id})")
    except WalletError as e:
        order.send_attempts = (order.send_attempts or 0) + 1
        order.last_error = str(e)
        if order.send_attempts >= 3:
            order.status = "send_failed"
            order.failure_reason = str(e)
            notify.notify(f"VOYE ECHWE x3: {order.reference_id} — {e}")
        else:
            order.status = "paid"  # retry nan pwochen tick la (backoff)
        db.commit()
        audit(db, "order.send_error", f"{order.reference_id}: {e}")
        _publish_order(order)


def resume_sending(db):
    """Rekòmansman/krach: lòd 'sending' san tx_hash — tcheke on-chain anvan
    reeseye, pou pa janm voye doub."""
    stale = utcnow() - timedelta(seconds=30)
    orders = db.query(Order).filter(
        Order.status == "sending", Order.updated_at < stale).all()
    if not orders:
        return
    try:
        wallet = get_wallet(db)
    except WalletError:
        return
    for order in orders:
        found = wallet.find_outgoing(
            order.dest_address, _dec(order.usdt_send or 0),
            order.dest_memo, order.sent_at or order.updated_at)
        if found:
            order.tx_hash = found
            order.status = "sent"
            order.sent_at = utcnow()
        else:
            order.send_attempts = (order.send_attempts or 0) + 1
            if order.send_attempts >= 3:
                order.status = "send_failed"
                order.failure_reason = "Echwe apre reprise (sending)"
            else:
                order.status = "paid"  # reeseye via try_send
        db.commit()
        _publish_order(order)


def confirm_sent(db, order: Order, wallet=None):
    """sent -> confirmed lè tranzaksyon on-chain la konfime."""
    try:
        wallet = wallet or get_wallet(db)
        status = wallet.tx_status(order.tx_hash)
    except WalletError:
        return
    if status == "confirmed":
        order.status = "confirmed"
        order.confirmed_at = utcnow()
        db.commit()
        audit(db, "order.confirmed", f"{order.reference_id} tx={order.tx_hash}")
        _publish_order(order)
        notify.notify(f"Rechaj KONFIME: {order.reference_id} ({order.usdt_send} {order.asset})")
    elif status == "failed":
        order.status = "send_failed"
        order.failure_reason = "Tranzaksyon on-chain echwe"
        db.commit()
        _publish_order(order)
        notify.notify(f"TX ECHWE on-chain: {order.reference_id}")


def expire_orders(db):
    timeout_min = int(get_setting(db, "payment_timeout_min",
                                  config.PAYMENT_TIMEOUT_MIN))
    limit = utcnow() - timedelta(minutes=timeout_min)
    orders = db.query(Order).filter(
        Order.status.in_(("created", "awaiting_payment")),
        Order.created_at < limit).all()
    for o in orders:
        o.status = "expired"
        o.failure_reason = "Peman pa konfime nan tan an"
        db.commit()
        _publish_order(o)


def apply_pending_meru(db):
    """Delè 10 minit pou chanjman adrès Meru a."""
    pending = get_setting(db, "pending_meru")
    if pending and utcnow() >= datetime.fromisoformat(pending["activate_at"]):
        set_setting(db, "meru_address", pending["address"])
        set_setting(db, "meru_memo", pending.get("memo", ""))
        set_setting(db, "meru_verified", pending.get("verified", False))
        set_setting(db, "pending_meru", None)
        db.commit()
        audit(db, "meru.activated", pending["address"])
        notify.notify(f"Nouvo adrès Meru aktive: {pending['address'][:12]}...")


def check_float_alert(db):
    units = float_units(db)
    threshold = _dec(get_setting(db, "float_alert_below", "25"))
    alerted = get_setting(db, "float_alerted", False)
    if units < threshold and not alerted:
        set_setting(db, "float_alerted", True)
        db.commit()
        audit(db, "float.low", f"{units} USDT rete")
        notify.notify(f"Float ba: {units} USDT rete (sèy {threshold})")
    elif units >= threshold and alerted:
        set_setting(db, "float_alerted", False)
        db.commit()


# ---------- tick prensipal ----------

def run_tick(db=None):
    own = db is None
    db = db or SessionLocal()
    try:
        _tick(db)
    finally:
        if own:
            db.close()


def _tick(db):
    apply_pending_meru(db)
    expire_orders(db)
    resume_sending(db)
    plop = wallet = None

    now = utcnow()
    waiting = db.query(Order).filter(
        Order.status.in_(("created", "awaiting_payment")),
        (Order.next_verify_at.is_(None)) | (Order.next_verify_at <= now),
    ).all()
    for o in waiting:
        if plop is None:
            plop = get_plop(db)
        process_order_payment(db, o, plop)

    for o in db.query(Order).filter(Order.status == "paid").all():
        try_send(db, o)

    for o in db.query(Order).filter(Order.status == "sent").all():
        if wallet is None:
            try:
                wallet = get_wallet(db)
            except WalletError:
                break
        confirm_sent(db, o, wallet)

    check_float_alert(db)


# ---------- thread lokal ----------

def start_background_worker():
    """Thread — lokalman sèlman (serverless pa konsève threads)."""
    def loop():
        while True:
            try:
                run_tick()
            except Exception:
                pass
            time.sleep(config.WORKER_INTERVAL_S)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def daily_htg_total(db) -> Decimal:
    """Total HTG lòd jodi a (pou limit jou a) — sòm Decimal egzak."""
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.execute(
        select(Order.amount_htg).where(
            Order.created_at >= start,
            ~Order.status.in_(("cancelled", "rejected")),
        )
    ).scalars().all()
    return sum((D(str(v)) for v in rows), D(0))
