"""Tès flux konplè an mòd mock + anti-pèt (kritè akseptasyon 2-10)."""
import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal

import pytest

from app.db import SessionLocal, get_setting, set_setting
from app.models import MockPlopTx, Order, utcnow
from app import worker

from .conftest import h


def _mk_refill(client, csrf, usdt=100, rate=130):
    r = client.post("/api/float/refills", headers=h(csrf), json={
        "htg_spent": float(usdt * rate), "usdt_received": float(usdt),
        "source": "p2p"})
    assert r.status_code == 200, r.text
    return r.json()


def _mk_order(client, csrf, amount=500, method="moncash_ussd",
              phone="50937000000", **kw):
    body = {"amount_htg": amount, "method": method, "phone": phone, **kw}
    return client.post("/api/orders", headers=h(csrf), json=body)


def _wait_confirmed(client, csrf, oid, timeout=8):
    """Simile tan an: mock konfime tx apre 3 s — fòse via run_tick."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        o = client.get(f"/api/orders/{oid}").json()
        if o["status"] == "confirmed":
            return o
        time.sleep(0.3)
        worker.run_tick()
    return client.get(f"/api/orders/{oid}").json()


# ---------- tès 2: flux konplè ----------

def test_full_flow_mock(auth):
    c, csrf = auth["client"], auth["csrf"]
    _mk_refill(c, csrf, usdt=100, rate=130)

    r = _mk_order(c, csrf, 500)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["status"] == "awaiting_payment"
    assert o["reference_id"].startswith("RCH-")

    # Akselere peman an -> verify ok -> voye -> confirmed
    r = c.post(f"/api/orders/{o['id']}/mock-accelerate", headers=h(csrf))
    o = r.json()
    assert o["status"] in ("sent", "sending"), o["status"]
    assert o["tx_hash"] and o["tx_hash"].startswith("MOCKTX")

    o = _wait_confirmed(c, csrf, o["id"])
    assert o["status"] == "confirmed", o
    assert o["tx_hash"]

    # Solde float la bese EGZAKTEMAN pa kantite a voye
    fl = c.get("/api/float").json()
    assert Decimal(fl["units"]) == Decimal("100") - Decimal(o["usdt_send"])


def test_redirect_methods_mockpay(auth):
    """natcash/kashpaw/carte/all -> URL mockpay; peman sou paj la konfime lòd la."""
    c, csrf = auth["client"], auth["csrf"]
    _mk_refill(c, csrf, usdt=100, rate=130)

    r = _mk_order(c, csrf, 500, method="natcash", phone=None, confirm_loss=True)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["status"] == "awaiting_payment"
    assert o["payment_url"].startswith("/mockpay.html?tx=")

    tx = o["payment_url"].split("tx=")[1]
    r = c.get(f"/api/mock/pay/{tx}")
    assert r.status_code == 200 and r.json()["trans_status"] == "no"

    r = c.post(f"/api/mock/pay/{tx}", json={"result": "ok"})
    assert r.status_code == 200

    o = _wait_confirmed(c, csrf, o["id"])
    assert o["status"] == "confirmed", o
    assert o["tx_hash"]

    # Rejwe sou menm tranzaksyon an refize
    assert c.post(f"/api/mock/pay/{tx}", json={"result": "ok"}).status_code == 400


# ---------- tès 3-5: webhook ----------

def _signed_body(payload: dict, secret="mock-secret"):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Webhook-Signature": sig, "Content-Type": "application/json"}


def _set_mock_tx_stuck(reference_id):
    """Mete peman mock la lwen nan tan pou verify retounen 'no'."""
    db = SessionLocal()
    tx = db.query(MockPlopTx).filter_by(reference_id=reference_id).first()
    tx.auto_ok_at = utcnow() + timedelta(hours=1)
    db.commit()
    db.close()


def test_webhook_valid_accepted(auth):
    c, csrf = auth["client"], auth["csrf"]
    o = _mk_order(c, csrf, 300).json()
    _set_mock_tx_stuck(o["reference_id"])
    raw, hdrs = _signed_body({
        "event": "transaction.status_changed",
        "transaction": {"reference_id": o["reference_id"], "new_status": "ok",
                        "amount": "300", "method": "moncash_ussd"},
        "merchant": {"id": "m1", "client_id": "mock-client"},
    })
    r = c.post("/api/webhook/plop", content=raw, headers=hdrs)
    assert r.status_code == 200
    # Webhook ok MEN verify di 'no' -> pa voye (tès 4)
    o2 = c.get(f"/api/orders/{o['id']}").json()
    assert o2["status"] == "awaiting_payment"


def test_webhook_invalid_signature(auth):
    c, csrf = auth["client"], auth["csrf"]
    o = _mk_order(c, csrf, 300).json()
    raw = json.dumps({"event": "transaction.status_changed",
                      "transaction": {"reference_id": o["reference_id"],
                                      "new_status": "ok"}}).encode()
    r = c.post("/api/webhook/plop", content=raw,
               headers={"X-Webhook-Signature": "sha256=badbad", "Content-Type": "application/json"})
    assert r.status_code == 401
    o2 = c.get(f"/api/orders/{o['id']}").json()
    assert o2["status"] == "awaiting_payment"
    assert o2["tx_hash"] is None


def test_webhook_ok_then_verify_ok_and_double(auth):
    """Webhook 'ok' + verify 'ok' -> voye; webhook double -> yon sèl voye."""
    c, csrf = auth["client"], auth["csrf"]
    o = _mk_order(c, csrf, 400).json()

    def webhook():
        raw, hdrs = _signed_body({
            "event": "transaction.status_changed",
            "transaction": {"reference_id": o["reference_id"], "new_status": "ok"}})
        return c.post("/api/webhook/plop", content=raw, headers=hdrs)

    # Fòse peman an 'ok' touswit
    db = SessionLocal()
    tx = db.query(MockPlopTx).filter_by(reference_id=o["reference_id"]).first()
    tx.auto_ok_at = utcnow() - timedelta(seconds=1)
    db.commit(); db.close()

    assert webhook().status_code == 200
    o2 = c.get(f"/api/orders/{o['id']}").json()
    assert o2["status"] in ("sent", "sending", "confirmed")
    tx_hash = o2["tx_hash"]
    assert tx_hash

    # Webhook double — pa gen dezyèm voye
    assert webhook().status_code == 200
    o3 = c.get(f"/api/orders/{o['id']}").json()
    assert o3["tx_hash"] == tx_hash


def test_webhook_amount_mismatch(auth):
    c, csrf = auth["client"], auth["csrf"]
    o = _mk_order(c, csrf, 500).json()
    # Modifye montan nan tx mock la
    db = SessionLocal()
    tx = db.query(MockPlopTx).filter_by(reference_id=o["reference_id"]).first()
    tx.amount = "999"
    tx.auto_ok_at = utcnow() - timedelta(seconds=1)
    db.commit(); db.close()

    raw, hdrs = _signed_body({
        "event": "transaction.status_changed",
        "transaction": {"reference_id": o["reference_id"], "new_status": "ok"}})
    c.post("/api/webhook/plop", content=raw, headers=hdrs)
    o2 = c.get(f"/api/orders/{o['id']}").json()
    assert o2["status"] == "failed_payment"
    assert o2["tx_hash"] is None
    assert "koresponn" in o2["failure_reason"]


# ---------- tès 6: float ensifizan ----------

def test_insufficient_float_rejected(auth):
    c, csrf = auth["client"], auth["csrf"]
    r = _mk_order(c, csrf, 10_000_000)  # plwaye plis pase float la
    assert r.status_code == 400
    assert "ensifizan" in r.json()["detail"].lower()


def test_insufficient_float_during_send(auth):
    """Si float la disparèt ant peman ak voye -> send_failed, pa gen doub."""
    c, csrf = auth["client"], auth["csrf"]
    # Ranpli jisteman ase pou lòd la, retire l anvan voye a
    _mk_refill(c, csrf, usdt=10, rate=130)
    o = _mk_order(c, csrf, 500).json()
    # Vide float la nan baz done a (simile depans ekstèn)
    db = SessionLocal()
    from app.models import FloatRefill
    db.query(FloatRefill).delete()
    db.commit(); db.close()
    r = c.post(f"/api/orders/{o['id']}/mock-accelerate", headers=h(csrf))
    o2 = r.json()
    assert o2["status"] in ("send_failed", "paid"), o2["status"]
    if o2["status"] == "paid":  # retry pase pa tick la
        worker.run_tick()
        o2 = c.get(f"/api/orders/{o['id']}").json()
        assert o2["status"] == "send_failed"
    assert o2["tx_hash"] is None
    # restore float
    _mk_refill(c, csrf, usdt=100, rate=130)


# ---------- tès 7: peman echwe/ekspire ----------

def test_payment_failed_no_send(auth):
    c, csrf = auth["client"], auth["csrf"]
    o = _mk_order(c, csrf, 250).json()
    r = c.post(f"/api/orders/{o['id']}/mock-fail", headers=h(csrf))
    o2 = r.json()
    assert o2["status"] == "failed_payment"
    assert o2["tx_hash"] is None


def test_payment_expired_no_send(auth):
    c, csrf = auth["client"], auth["csrf"]
    o = _mk_order(c, csrf, 250).json()
    _set_mock_tx_stuck(o["reference_id"])
    db = SessionLocal()
    order = db.get(Order, o["id"])
    order.created_at = utcnow() - timedelta(hours=2)
    db.commit(); db.close()
    worker.run_tick()
    o2 = c.get(f"/api/orders/{o['id']}").json()
    assert o2["status"] == "expired"
    assert o2["tx_hash"] is None


# ---------- tès 8: limit -> needs_approval ----------

def test_limits_needs_approval(auth):
    c, csrf = auth["client"], auth["csrf"]
    c.post("/api/settings", headers=h(csrf), json={"max_order_htg": "1000"})
    try:
        r = _mk_order(c, csrf, 5000)
        assert r.status_code == 200
        o = r.json()
        assert o["status"] == "needs_approval"
        assert o["tx_hash"] is None
        # Rejte
        r = c.post(f"/api/orders/{o['id']}/reject", headers=h(csrf))
        assert r.json()["status"] == "cancelled"
        # Apwouve
        r = _mk_order(c, csrf, 5000)
        oid = r.json()["id"]
        assert r.json()["status"] == "needs_approval"
        r = c.post(f"/api/orders/{oid}/approve", headers=h(csrf))
        assert r.json()["status"] == "awaiting_payment"
    finally:
        c.post("/api/settings", headers=h(csrf), json={"max_order_htg": "50000"})


def test_loss_limit_rejected_then_confirmed(auth):
    c, csrf = auth["client"], auth["csrf"]
    _mk_refill(c, csrf, usdt=50, rate=160)  # move taux -> gwo pèt
    r = _mk_order(c, csrf, 1000)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "LOSS_LIMIT"
    r = _mk_order(c, csrf, 1000, confirm_loss=True)
    assert r.status_code == 200
    assert r.json()["status"] in ("awaiting_payment", "needs_approval")


# ---------- tès 9: kill switch ----------

def test_kill_switch_blocks_send(auth):
    c, csrf = auth["client"], auth["csrf"]
    c.post("/api/settings/kill-switch", headers=h(csrf), json={"on": True})
    try:
        o = _mk_order(c, csrf, 300, confirm_loss=True).json()
        c.post(f"/api/orders/{o['id']}/mock-accelerate", headers=h(csrf))
        o2 = c.get(f"/api/orders/{o['id']}").json()
        assert o2["status"] == "paid"  # peye men pa voye
        assert o2["tx_hash"] is None
    finally:
        c.post("/api/settings/kill-switch", headers=h(csrf), json={"on": False})


# ---------- tès 10: rekòmansman pandan 'sending' ----------

def test_restart_during_sending_no_double(auth):
    c, csrf = auth["client"], auth["csrf"]
    o = _mk_order(c, csrf, 300, confirm_loss=True).json()
    # Simile krach: lòd 'sending', san tx_hash, updated_at fin pase
    db = SessionLocal()
    order = db.get(Order, o["id"])
    order.status = "sending"
    order.tx_hash = None
    order.send_attempts = 0
    order.updated_at = utcnow() - timedelta(minutes=5)
    order.dest_address = "MERU_ADDR_TEST"
    db.commit()
    worker.resume_sending(db)
    order = db.get(Order, o["id"])
    db.close()
    # Pa te gen tx sou chèn -> retounen 'paid' pou reeseye (pa doub)
    assert order.status in ("paid", "sent")
    # Yon sèl voye apre tick
    worker.run_tick()
    db = SessionLocal()
    order = db.get(Order, o["id"])
    assert order.status == "sent"
    first_hash = order.tx_hash
    db.close()
    worker.run_tick()
    db = SessionLocal()
    order = db.get(Order, o["id"])
    assert order.tx_hash == first_hash
    db.close()
