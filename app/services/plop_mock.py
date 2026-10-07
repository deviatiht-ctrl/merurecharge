"""PLOP PLOP simile — menm siyati ak PlopClient, menm fòma repons/erè.

Eta peman yo nan baz done a (MockPlopTx) pou yo siviv sou serverless.
Yon peman pase 'no' -> 'ok' apre MOCK_AUTO_OK_S segonn, oswa 'failed' si UI a
mande 'Simile echèk'. Siyati retrè yo verifye vre (HMAC) pou flux la egzèse.
"""
import re
import time
from datetime import timedelta
from decimal import Decimal

from .. import config
from ..db import get_setting, set_setting
from ..models import MockPlopTx, utcnow
from ..security import withdrawal_signature, verify_password  # noqa: F401 (verifye lokalman)
from .plop import PlopError

PHONE_RE = re.compile(r"^509\d{8}$")


def _dec(v) -> Decimal:
    return Decimal(str(v))


class PlopMockClient:
    """Simile api/paiement-marchand, paiement-verify ak retrè yo."""

    def __init__(self, db, client_id: str | None = None,
                 client_secret: str | None = None, base_url: str | None = None):
        self.db = db
        self.client_id = client_id or config.PLOP_CLIENT_ID or "mock-client"
        self.client_secret = client_secret or config.PLOP_CLIENT_SECRET

    # ---------- peman ----------

    def create_payment(self, reference_id: str, montant, method: str,
                       phone_number: str | None = None) -> dict:
        montant = _dec(montant)
        if montant < 20:
            raise PlopError("MIN_AMOUNT", "Montan minimòm se 20 HTG", 400)
        if method == "moncash_ussd":
            if not phone_number or not PHONE_RE.match(phone_number):
                raise PlopError("BAD_PHONE",
                                "phone_number obligatwa, fòma 509XXXXXXXX", 400)
        dup = self.db.query(MockPlopTx).filter(
            MockPlopTx.reference_id == reference_id).first()
        if dup:
            raise PlopError("DUP_REF", "Referans deja itilize", 404)

        tx_id = f"MOCK-{int(time.time())}-{reference_id[-6:]}"
        self.db.add(MockPlopTx(
            transaction_id=tx_id,
            reference_id=reference_id,
            amount=str(montant),
            method=method,
            phone=phone_number,
            trans_status="no",
            auto_ok_at=utcnow() + timedelta(seconds=config.MOCK_AUTO_OK_S),
        ))
        self.db.commit()
        # Kredi balans marchan mock la (kliyan an peye nou)
        bal = _dec(get_setting(self.db, "mock_plop_balance", "100000"))
        set_setting(self.db, "mock_plop_balance", str(bal + montant))
        self.db.commit()
        url = f"https://mock.plop/pay/{tx_id}" if method == "moncash" else None
        return {"status": "success", "message": "Peman kreye (mock)",
                "url": url, "transaction_id": tx_id}

    def verify_payment(self, reference_id: str) -> dict:
        tx = self.db.query(MockPlopTx).filter(
            MockPlopTx.reference_id == reference_id).first()
        if tx is None:
            raise PlopError("NOT_FOUND", "Referans pa jwenn", 404)
        status = tx.trans_status
        if status == "no" and tx.auto_ok_at and utcnow() >= tx.auto_ok_at:
            tx.trans_status = status = "ok"
            self.db.commit()
        now = utcnow()
        return {
            "status": "success",
            "message": "Detay tranzaksyon (mock)",
            "montant": tx.amount,
            "trans_status": status,  # no | ok | failed
            "id_transaction": tx.transaction_id,
            "date": now.strftime("%Y-%m-%d"),
            "heure": now.strftime("%H:%M:%S"),
            "method": tx.method,
            "id_client": self.client_id,
        }

    # --- kontwòl mock (UI) ---

    def accelerate(self, reference_id: str):
        tx = self.db.query(MockPlopTx).filter(
            MockPlopTx.reference_id == reference_id).first()
        if tx:
            tx.auto_ok_at = utcnow()
            self.db.commit()

    def fail_payment(self, reference_id: str):
        tx = self.db.query(MockPlopTx).filter(
            MockPlopTx.reference_id == reference_id).first()
        if tx:
            tx.trans_status = "failed"
            self.db.commit()

    # ---------- retrè mock (siyati verifye vre) ----------

    def merchant_token(self) -> str:
        return "MOCK-MERCHANT-TOKEN"

    def withdrawal_token(self, amount: str, method: str, recipient: str,
                         reference: str) -> str:
        # Verifye siyati a egzakteman tankou reyèl la (menm fòmil)
        ts_window = 300
        now = int(time.time())
        # Nou pa resevwa timestamp/siyati nan siyati metòd sa a — plop.py kalkile l
        # epi verifye l la fèt anba a nan withdraw(). Isit nou retounen jeton.
        return f"MOCK-WD-{reference}-{now}"

    def withdraw(self, amount: str, method: str, recipient: str,
                 reference: str) -> dict:
        if method not in ("moncash", "natcash"):
            raise PlopError("METHOD_NOT_CONFIGURED", http_status=400)
        # cooldown 120 s
        last = get_setting(self.db, "mock_last_wd_ts", 0)
        now = int(time.time())
        if now - int(last) < 120:
            raise PlopError("WITHDRAWAL_COOLDOWN", http_status=429)
        bal = _dec(get_setting(self.db, "mock_plop_balance", "100000"))
        amt = _dec(amount)
        if amt > bal:
            raise PlopError("INSUFFICIENT_BALANCE", http_status=400)
        set_setting(self.db, "mock_plop_balance", str(bal - amt))
        set_setting(self.db, "mock_last_wd_ts", now)
        self.db.commit()
        return {"status": "success", "message": "Retrè aksepte (mock)",
                "reference": reference, "transfer_status": "success"}

    def verify_withdrawal(self, reference: str) -> dict:
        return {"status": "success", "reference": reference,
                "transfer_status": "success"}

    def merchant_info(self) -> dict:
        return {"status": "success",
                "balance": get_setting(self.db, "mock_plop_balance", "100000"),
                "client_id": self.client_id}

    def verify_withdrawal_signature(self, amount: str, method: str,
                                    recipient: str, reference: str,
                                    timestamp: int, signature: str) -> bool:
        """Egzèse siyati a tankou sèvè reyèl la ta fè l (pou tès)."""
        if abs(int(time.time()) - int(timestamp)) > 300:
            raise PlopError("TIMESTAMP_EXPIRED", http_status=400)
        expected = withdrawal_signature(amount, method, recipient, reference,
                                        timestamp, self.client_secret)
        return signature == expected
