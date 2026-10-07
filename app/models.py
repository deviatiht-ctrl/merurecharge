from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text

from .db import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Estati lòd
ORDER_ACTIVE = ("created", "awaiting_payment", "paid", "sending", "sent")
ORDER_FINAL = ("confirmed", "failed_payment", "expired", "needs_approval",
               "send_failed", "cancelled", "rejected")


class Setting(Base):
    __tablename__ = "meru_settings"
    key = Column(String(64), primary_key=True)
    value = Column(JSON)


class Order(Base):
    __tablename__ = "meru_orders"

    id = Column(Integer, primary_key=True)
    reference_id = Column(String(40), unique=True, nullable=False, index=True)
    amount_htg = Column(String(32), nullable=False)          # HTG peye (Decimal string)
    method = Column(String(20), nullable=False)              # moncash | moncash_ussd
    phone = Column(String(20), nullable=True)
    status = Column(String(24), nullable=False, default="created", index=True)
    failure_reason = Column(Text, nullable=True)
    approved = Column(Boolean, default=False)

    # Kalkil verouye
    locked_rate = Column(String(32), nullable=True)          # taux_float
    fee_pct = Column(String(16), nullable=True)
    fee_model = Column(String(12), nullable=True)
    htg_net = Column(String(32), nullable=True)
    usdt_gross = Column(String(32), nullable=True)
    usdt_send = Column(String(32), nullable=True)            # kantite ki voye
    effective_rate = Column(String(32), nullable=True)
    reference_rate = Column(String(32), nullable=True)
    loss_pct = Column(String(32), nullable=True)
    loss_htg = Column(String(32), nullable=True)

    # Destinasyon (whiteliste — pa janm soti nan request la)
    asset = Column(String(10), nullable=True)
    network = Column(String(12), nullable=True)
    dest_address = Column(String(80), nullable=True)
    dest_memo = Column(String(64), nullable=True)

    # PLOP
    plop_tx_id = Column(String(80), nullable=True)
    payment_url = Column(Text, nullable=True)
    next_verify_at = Column(DateTime, nullable=True)

    # Voye on-chain
    tx_hash = Column(String(120), nullable=True, unique=True)
    send_attempts = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)


class FloatRefill(Base):
    __tablename__ = "meru_float_refills"
    id = Column(Integer, primary_key=True)
    htg_spent = Column(String(32), nullable=False)
    usdt_received = Column(String(32), nullable=False)
    rate = Column(String(32), nullable=False)  # htg_spent / usdt_received
    source = Column(String(40), default="p2p")
    tx_hash = Column(String(120), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Withdrawal(Base):
    __tablename__ = "meru_withdrawals"
    id = Column(Integer, primary_key=True)
    reference = Column(String(40), unique=True, nullable=False)
    amount = Column(String(32), nullable=False)
    method = Column(String(20), nullable=False)  # moncash | natcash
    recipient = Column(String(40), nullable=False)
    status = Column(String(20), default="pending")  # pending|failed|success|rembourse
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AuditLog(Base):
    __tablename__ = "meru_audit_log"
    id = Column(Integer, primary_key=True)
    action = Column(String(60), nullable=False, index=True)
    detail = Column(Text, nullable=True)
    ip = Column(String(60), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class Session(Base):
    __tablename__ = "meru_sessions"
    token_hash = Column(String(80), primary_key=True)
    csrf = Column(String(80), nullable=False)
    ip = Column(String(60), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class LoginAttempt(Base):
    __tablename__ = "meru_login_attempts"
    id = Column(Integer, primary_key=True)
    ip = Column(String(60), nullable=False, index=True)
    success = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class MockPlopTx(Base):
    """Tranzaksyon peman PLOP simile — nan baz done pou siviv sou serverless."""
    __tablename__ = "meru_mock_plop_tx"
    id = Column(Integer, primary_key=True)
    transaction_id = Column(String(80), unique=True, nullable=False)
    reference_id = Column(String(40), unique=True, nullable=False, index=True)
    amount = Column(String(32), nullable=False)
    method = Column(String(20), nullable=False)
    phone = Column(String(20), nullable=True)
    trans_status = Column(String(10), default="no")  # no | ok | failed
    auto_ok_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
