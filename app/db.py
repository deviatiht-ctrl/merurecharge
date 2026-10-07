from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("libsql://"):
        rest = url[len("libsql://"):]
        token = config.TURSO_AUTH_TOKEN
        sep = "&" if "?" in rest else "?"
        return f"sqlite+libsql://{rest}{sep}authToken={token}&secure=true"
    return url


_url = _normalize_url(config.DATABASE_URL)
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite:///") else {}
engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    seed_settings()


def seed_settings():
    """Valè default yo — env la se sous inisyal la, baz done a otorite apre sa."""
    from .models import Setting

    defaults = {
        "mock_mode": config.MOCK_MODE_DEFAULT,
        "reference_rate": "129",
        "fee_pct": "3",
        "fee_model": "dedwi",  # dedwi | sou_tet
        "fee_network": "0",
        "fee_meru": "0",
        "min_order_htg": "20",
        "max_order_htg": "50000",
        "max_daily_htg": "200000",
        "loss_limit_pct": "4",
        "float_reserve": "5",
        "float_alert_below": "25",
        "kill_switch": False,
        "meru_address": config.MERU_ADDRESS,
        "meru_memo": config.MERU_MEMO,
        "meru_verified": False,
        "pending_meru": None,
        "wallet_asset": config.WALLET_ASSET,
        "wallet_network": config.WALLET_NETWORK,
        "payment_timeout_min": config.PAYMENT_TIMEOUT_MIN,
        "mock_plop_balance": "100000",
        "preflight": {},
        "notify_enabled": True,
    }
    db = SessionLocal()
    try:
        for k, v in defaults.items():
            if db.get(Setting, k) is None:
                db.add(Setting(key=k, value=v))
        db.commit()
    finally:
        db.close()


def get_setting(db, key, default=None):
    from .models import Setting

    row = db.get(Setting, key)
    return row.value if row is not None else default


def set_setting(db, key, value):
    from .models import Setting

    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def is_mock(db) -> bool:
    return bool(get_setting(db, "mock_mode", True))
