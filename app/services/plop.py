"""Kliyan API PLOP PLOP (mòd reyèl).

Base URL: https://plopplop.solutionip.app/
Repons: JSON UTF-8. Timeout 20 s. Retry sèlman sou erè rezo.
"""
import time

import httpx

from .. import config
from ..calc import amount_str
from ..security import withdrawal_signature


def _num(s: str):
    """Valè JSON ki matche egzakteman fòma tèks la (500 pa 500.0)."""
    return int(s) if "." not in s else float(s)

TIMEOUT = 20.0

# Kòd erè dokimante -> mesaj kreyòl
ERROR_MESSAGES = {
    "INVALID_SIGNATURE": "Siyati retrè a envalid (verifye client_secret ak fòma valè yo)",
    "TIMESTAMP_EXPIRED": "Timestamp la ekspire (±5 minit) — eseye ankò",
    "NO_CLIENT_SECRET": "client_secret pa konfigire sou kont marchan an",
    "INVALID_TOKEN_TYPE": "Move tip jeton — rekomans flux retrè a",
    "PARAMETER_MISMATCH": "Paramèt retrè a pa koresponn ak siyati a",
    "TOKEN_ALREADY_USED": "Jeton retrè a deja itilize — chak retrè bezwen nouvo jeton",
    "WITHDRAWAL_COOLDOWN": "Cooldown: tann 120 segonn ant de retrè",
    "METHOD_NOT_CONFIGURED": "Metòd retrè a pa aktive sou kont marchan an",
    "INSUFFICIENT_BALANCE": "Balans PLOP ensifizan pou retrè sa a",
    "API_TRANSFER_FAILED": "Transfè PLOP la echwe — eseye ankò oswa kontakte sipò",
    "DUPLICATE_REFERENCE": "Referans retrè a deja itilize",
}


class PlopError(Exception):
    def __init__(self, code: str, message: str = "", http_status: int = 0):
        self.code = code
        self.http_status = http_status
        super().__init__(message or ERROR_MESSAGES.get(code, f"Erè PLOP: {code}"))


class PlopClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 base_url: str | None = None):
        self.client_id = client_id or config.PLOP_CLIENT_ID
        self.client_secret = client_secret or config.PLOP_CLIENT_SECRET
        self.base_url = (base_url or config.PLOP_BASE_URL).rstrip("/") + "/"
        self._token = None
        self._token_exp = 0.0

    def _post(self, path: str, json: dict | None = None,
              headers: dict | None = None) -> dict:
        url = self.base_url + path.lstrip("/")
        last_exc = None
        for attempt in range(2):  # retry yon sèl fwa, sèlman sou erè rezo
            try:
                r = httpx.post(url, json=json, headers=headers or {}, timeout=TIMEOUT)
                break
            except httpx.TransportError as e:
                last_exc = e
                if attempt == 1:
                    raise PlopError("NETWORK", f"Erè rezo: {e}")
                time.sleep(0.5)
        else:
            raise PlopError("NETWORK", f"Erè rezo: {last_exc}")

        try:
            data = r.json()
        except ValueError:
            raise PlopError("BAD_RESPONSE", f"Repons pa JSON (HTTP {r.status_code})", r.status_code)

        if r.status_code >= 400:
            code = ""
            if isinstance(data, dict):
                code = data.get("code") or data.get("error") or ""
            raise PlopError(code or f"HTTP_{r.status_code}",
                            (data.get("message") if isinstance(data, dict) else None) or "",
                            r.status_code)
        return data if isinstance(data, dict) else {"status": "ok", "data": data}

    # ---------- peman ----------

    def create_payment(self, reference_id: str, montant, method: str,
                       phone_number: str | None = None) -> dict:
        body = {
            "client_id": self.client_id,
            "refference_id": reference_id,
            "montant": float(montant),
            "payment_method": method,
        }
        if method == "moncash_ussd":
            body["phone_number"] = phone_number
        return self._post("api/paiement-marchand", body)

    def verify_payment(self, reference_id: str) -> dict:
        return self._post("api/paiement-verify", {
            "client_id": self.client_id,
            "refference_id": reference_id,
        })

    # ---------- retrè (3 etap) ----------

    def merchant_token(self) -> str:
        """Etap 1: jeton marchan (valab ~1 minit — pa fè konfyans sou expires_in)."""
        if self._token and time.time() < self._token_exp - 10:
            return self._token
        data = self._post("api/auth/marchand", {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        token = data.get("token") or data.get("access_token") or data.get("data", {}).get("token")
        if not token:
            raise PlopError("NO_TOKEN", "PLOP pa retounen jeton marchan")
        self._token = token
        self._token_exp = time.time() + 45  # konsèvatif: ~1 minit
        return token

    def withdrawal_token(self, amount: str, method: str, recipient: str,
                         reference: str) -> str:
        """Etap 2: jeton retrè siyen HMAC (2 minit, yon sèl itilizasyon)."""
        amount = amount_str(amount)
        ts = int(time.time())
        sig = withdrawal_signature(amount, method, recipient, reference, ts,
                                   self.client_secret)
        data = self._post("api/auth/marchand/withdrawal-token", {
            "amount": _num(amount),
            "method": method,
            "recipient": recipient,
            "reference": reference,
            "timestamp": ts,
            "withdrawal_signature": sig,
        }, headers={"Authorization": f"Bearer {self.merchant_token()}"})
        token = data.get("withdrawal_token") or data.get("token") or data.get("data", {}).get("withdrawal_token")
        if not token:
            raise PlopError("NO_WD_TOKEN", "PLOP pa retounen jeton retrè")
        return token

    def withdraw(self, amount: str, method: str, recipient: str,
                 reference: str) -> dict:
        """Etap 3 (ak 1+2 anndan): egzekite retrè a."""
        amount = amount_str(amount)
        wd_token = self.withdrawal_token(amount, method, recipient, reference)
        return self._post("api/withdraw/marchand", {
            "amount": _num(amount),
            "method": method,
            "recipient": recipient,
            "reference": reference,
        }, headers={"Authorization": f"Bearer {wd_token}"})

    def verify_withdrawal(self, reference: str) -> dict:
        return self._post("api/withdraw/marchand/verify", {"reference": reference},
                          headers={"Authorization": f"Bearer {self.merchant_token()}"})

    def merchant_info(self) -> dict:
        """Enfo marchan (balans si disponib). Best-effort."""
        return self._post("api/auth/marchand", {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
