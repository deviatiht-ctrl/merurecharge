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

# Connect args + pool selon dialect
if _url.startswith("sqlite:///"):
    _connect_args = {"check_same_thread": False}
    _pool_kw = {}
elif "psycopg" in _url:
    # Supabase/Supavisor (port 6543, transaction mode) pa sipòte prepared
    # statements — prepare_threshold=None dezaktive yo nan psycopg3.
    _connect_args = {"prepare_threshold": None}
    _pool_kw = {}
else:
    _connect_args = {}
    _pool_kw = {}

# Sou serverless, ti pool + resikle koneksyon yo (Supabase limite koneksyon)
if config.IS_VERCEL and "sqlite" not in _url:
    _pool_kw.update(pool_size=1, max_overflow=1, pool_recycle=300)

engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=True,
                       **_pool_kw)
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
    from sqlalchemy import text

    Base.metadata.create_all(engine)
    _harden_postgres(text)
    seed_settings()


def _harden_postgres(text):
    """Sou Supabase/Postgres: aktive RLS sou tout tablo yo.

    Kle 'anon' PostgREST la piblik — san RLS li ta ka li/ekri tablo yo.
    Role 'postgres' (pwopriyetè) pa afekte pa RLS, donk backend la mache nòmal;
    kle anon/service pa jwenn anyen nan /rest/v1/."""
    if engine.dialect.name != "postgresql":
        return
    try:
        with engine.begin() as conn:
            for name in Base.metadata.tables:
                conn.execute(
                    text(f'ALTER TABLE public."{name}" '
                         f"ENABLE ROW LEVEL SECURITY"))
    except Exception:
        pass  # pa kraze si nou pa pwopriyetè tablo yo


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
