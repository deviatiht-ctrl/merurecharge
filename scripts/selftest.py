"""Selftest — lanse tout flux la an mòd mock, san navigatè.

Itilizasyon: python scripts/selftest.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'selftest.db')}")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("SESSION_SECRET", "selftest-secret")
os.environ.setdefault("MOCK_AUTO_OK_S", "1")
os.environ.setdefault("CRON_SECRET", "cron-test")
os.environ.setdefault("MERU_ADDRESS", "GTESTMERUADDRESSXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app import worker  # noqa: E402

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(f"[{PASS if cond else FAIL}] {name}" + (f" — {extra}" if extra and not cond else ""))


def main():
    with TestClient(app) as c:
        # 1. Setup + login
        r = c.post("/api/auth/setup", json={"password": "selftest-pass-1"})
        check("Setup modpas", r.status_code == 200, r.text)
        csrf = r.json().get("csrf", "")
        H = {"X-CSRF-Token": csrf}

        # 2. Ranpli float 100 USDC @ 130
        r = c.post("/api/float/refills", headers=H,
                   json={"htg_spent": 13000, "usdt_received": 100, "source": "p2p"})
        check("Ranpli float", r.status_code == 200, r.text)

        # 3. Rechaj 500 HTG
        r = c.post("/api/orders", headers=H, json={
            "amount_htg": 500, "method": "moncash_ussd", "phone": "50937000000"})
        check("Kreye lòd", r.status_code == 200, r.text)
        o = r.json()
        check("Estati awaiting_payment", o["status"] == "awaiting_payment", str(o))

        # 4. Akselere peman -> paid -> sent
        r = c.post(f"/api/orders/{o['id']}/mock-accelerate", headers=H)
        check("Peman -> voye", r.json()["status"] in ("sent", "sending", "confirmed"), r.text)

        # 5. Konfimasyon
        deadline = time.time() + 10
        final = {}
        while time.time() < deadline:
            worker.run_tick()
            final = c.get(f"/api/orders/{o['id']}").json()
            if final["status"] == "confirmed":
                break
            time.sleep(0.4)
        check("Estati confirmed", final.get("status") == "confirmed", str(final.get("status")))
        check("tx_hash prezan", bool(final.get("tx_hash")))

        # 6. Float bese egzakteman
        fl = c.get("/api/float").json()
        from decimal import Decimal
        check("Float debite egzakteman",
              Decimal(fl["units"]) == Decimal("100") - Decimal(final["usdt_send"]),
              fl["units"])

        # 7. Simile echèk
        r = c.post("/api/orders", headers=H, json={
            "amount_htg": 300, "method": "moncash_ussd", "phone": "50937000000"})
        o2 = r.json()
        r = c.post(f"/api/orders/{o2['id']}/mock-fail", headers=H)
        check("Simile echèk -> failed_payment",
              r.json()["status"] == "failed_payment", r.text)

        # 8. Kill switch
        c.post("/api/settings/kill-switch", headers=H, json={"on": True})
        r = c.post("/api/orders", headers=H, json={
            "amount_htg": 300, "method": "moncash_ussd", "phone": "50937000000"})
        o3 = r.json()
        c.post(f"/api/orders/{o3['id']}/mock-accelerate", headers=H)
        o3 = c.get(f"/api/orders/{o3['id']}").json()
        check("Kill switch bloke voye", o3["status"] == "paid" and not o3["tx_hash"])
        c.post("/api/settings/kill-switch", headers=H, json={"on": False})

        # 9. San sesyon -> 401
        from fastapi.testclient import TestClient as TC
        anon = TC(app)
        check("San sesyon -> 401", anon.get("/api/orders").status_code == 401)

        # 10. Health
        check("Health", c.get("/api/health").json()["ok"] is True)

    print("\n" + "=" * 50)
    ok = sum(1 for _, p in results if p)
    print(f"REZILTA: {ok}/{len(results)} tès pase")
    if ok < len(results):
        sys.exit(1)
    print("SELFTEST OK — sistèm nan mache de bout en bout (mock)")


if __name__ == "__main__":
    main()
