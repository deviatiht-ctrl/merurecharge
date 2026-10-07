"""Tès sekirite — sesyon, CSRF, rate limit, siyati webhook ak retrè."""
import hashlib
import hmac
import json
import time

from app.db import SessionLocal, set_setting
from app.security import (verify_webhook_signature, withdrawal_signature)
from app.services.plop_mock import PlopMockClient
from app.services.plop import PlopError

from .conftest import h


# ---------- sesyon ----------

def test_no_session_no_access():
    # Kliyan nouvo san cookie sesyon
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/api/orders")
    assert r.status_code == 401
    r = c.get("/api/settings")
    assert r.status_code == 401
    r = c.post("/api/orders", json={"amount_htg": 500, "method": "moncash_ussd"})
    assert r.status_code == 401


def test_csrf_required(auth):
    c = auth["client"]
    r = c.post("/api/settings/kill-switch", json={"on": True})  # san CSRF
    assert r.status_code == 403


def test_bad_password(auth):
    r = auth["client"].post("/api/auth/login", json={"password": "move"})
    assert r.status_code == 401


def test_login_rate_limit(auth):
    """Apre 5 echèk -> 429 (fenèt 15 min)."""
    c = auth["client"]
    for _ in range(5):
        c.post("/api/auth/login", json={"password": "move"})
    r = c.post("/api/auth/login", json={"password": "move"})
    assert r.status_code == 429


# ---------- siyati webhook ----------

def test_webhook_signature_helper():
    body = b'{"event":"transaction.status_changed"}'
    sig = "sha256=" + hmac.new(b"sec", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, sig, "sec")
    assert not verify_webhook_signature(body, sig, "lot-sekre")
    assert not verify_webhook_signature(body, "sha256=00", "sec")
    assert not verify_webhook_signature(body, "", "sec")
    assert not verify_webhook_signature(b'"x"', sig, "sec")


# ---------- siyati retrè (vektè konnen) ----------

def test_withdrawal_signature_known_vector():
    """HMAC-SHA256('amount|method|recipient|reference|timestamp', secret)."""
    secret = "s3cret-test"
    amount, method, recipient = "500", "moncash", "50937000000"
    reference, ts = "WD-20250101-abcd", 1735689600
    expected = hmac.new(
        secret.encode(),
        f"{amount}|{method}|{recipient}|{reference}|{ts}".encode(),
        hashlib.sha256).hexdigest()
    assert withdrawal_signature(amount, method, recipient, reference, ts,
                                secret) == expected
    # Fòma montan an enpòtan: "500" != "500.00"
    assert withdrawal_signature("500.00", method, recipient, reference, ts,
                                secret) != expected


def test_withdrawal_timestamp_expired_rejected(auth):
    db = SessionLocal()
    client = PlopMockClient(db)
    try:
        import pytest
        with pytest.raises(PlopError) as exc:
            client.verify_withdrawal_signature(
                "500", "moncash", "50937000000", "WD-x",
                int(time.time()) - 400, "sig")  # > 5 minit
        assert exc.value.code == "TIMESTAMP_EXPIRED"
    finally:
        db.close()


def test_withdrawal_mock_flow(auth):
    """Flux retrè mock konplè + cooldown + balans ensifizan."""
    c, csrf = auth["client"], auth["csrf"]
    db = SessionLocal()
    set_setting(db, "mock_plop_balance", "100000")
    set_setting(db, "mock_last_wd_ts", 0)
    db.commit(); db.close()

    r = c.post("/api/float/withdraw", headers=h(csrf),
               json={"amount": 500, "method": "moncash",
                     "recipient": "50937000000"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert r.json()["reference"].startswith("WD-")

    # Cooldown 120 s -> 429/400
    r = c.post("/api/float/withdraw", headers=h(csrf),
               json={"amount": 100, "method": "moncash",
                     "recipient": "50937000000"})
    assert r.status_code == 400
    assert "cooldown" in r.json()["detail"].lower()

    # Balans ensifizan
    db = SessionLocal()
    set_setting(db, "mock_last_wd_ts", 0)
    set_setting(db, "mock_plop_balance", "10")
    db.commit(); db.close()
    r = c.post("/api/float/withdraw", headers=h(csrf),
               json={"amount": 9999, "method": "natcash",
                     "recipient": "50937000000"})
    assert r.status_code == 400


def test_cron_requires_secret():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.post("/api/cron/tick")
    assert r.status_code == 401
    r = client.post("/api/cron/tick",
                    headers={"Authorization": "Bearer cron-test"})
    assert r.status_code == 200


def test_audit_log_written(auth):
    rows = auth["client"].get("/api/audit").json()
    actions = {r["action"] for r in rows}
    assert "auth.setup" in actions or "order.created" in actions
    # pa janm gen modpas/sekrè nan detay yo
    assert all("password" not in (r["detail"] or "").lower() for r in rows)
