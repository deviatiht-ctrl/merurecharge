"""Wallet simile — solde a dérive nan refills ak lòd yo (sous verite inik).

Solde = total USDT ranpli - total USDT voye (lòd sent/confirmed).
Chak voye anrejistre yon hash fo ki pase 'confirmed' apre ~3 s.
"""
import hashlib
import time
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from ..db import get_setting, set_setting
from ..models import FloatRefill, Order
from .wallet import WalletError


def _dec(v) -> Decimal:
    return Decimal(str(v))


def sent_usdt_total(db) -> Decimal:
    # Sòm an Decimal nan Python — egzak sou tout baz done (pa float)
    rows = db.execute(
        select(Order.usdt_send).where(Order.status.in_(("sent", "confirmed")))
    ).scalars().all()
    return sum((_dec(v) for v in rows), Decimal(0))


class MockWallet:
    def __init__(self, db, network: str = "stellar"):
        self.db = db
        self.network = network
        self.address = "MOCKWALLET" + network.upper()

    def get_balance(self) -> Decimal:
        from ..calc import float_totals
        refills = self.db.query(FloatRefill).all()
        return _dec(float_totals(refills, sent_usdt_total(self.db))["units"])

    def send(self, amount: Decimal, address: str, memo: str | None = None) -> str:
        amount = _dec(amount)
        if self.get_balance() < amount:
            raise WalletError("Float ensifizan (mock)")
        tx_hash = "MOCKTX" + hashlib.sha256(
            f"{time.time()}-{address}-{amount}-{memo}".encode()).hexdigest()[:52]
        txs = get_setting(self.db, "mock_txs", {}) or {}
        txs[tx_hash] = {"ts": time.time(), "address": address,
                        "amount": str(amount), "memo": memo}
        set_setting(self.db, "mock_txs", txs)
        self.db.commit()
        return tx_hash

    def tx_status(self, tx_hash: str) -> str:
        txs = get_setting(self.db, "mock_txs", {}) or {}
        rec = txs.get(tx_hash)
        if not rec:
            return "pending"
        return "confirmed" if time.time() - rec["ts"] > 3 else "pending"

    def find_outgoing(self, address: str, amount: Decimal, memo: str | None,
                      since: datetime) -> str | None:
        txs = get_setting(self.db, "mock_txs", {}) or {}
        amount = _dec(amount)
        for h, rec in txs.items():
            if (rec.get("address") == address
                    and _dec(rec.get("amount", 0)) == amount
                    and rec.get("ts", 0) >= since.timestamp()):
                return h
        return None

    def network_fee(self) -> Decimal:
        return Decimal("0")
