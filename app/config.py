import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return v if v not in (None, "") else default


IS_VERCEL = bool(os.environ.get("VERCEL"))

# --- PLOP PLOP ---
PLOP_CLIENT_ID = env("PLOP_CLIENT_ID")
PLOP_CLIENT_SECRET = env("PLOP_CLIENT_SECRET") or "mock-secret"
PLOP_BASE_URL = env("PLOP_BASE_URL", "https://plopplop.solutionip.app/").rstrip("/") + "/"

# --- Baz done ---
DATABASE_URL = env("DATABASE_URL")
if not DATABASE_URL:
    # Sou Vercel filesystem la read-only — /tmp sèl kote ki ekrivab
    # (ephemè! mete DATABASE_URL pou pwodiksyon). Lokalman: meru.db.
    _db_path = "/tmp/meru.db" if IS_VERCEL else str(BASE_DIR / "meru.db")
    DATABASE_URL = "sqlite:///" + _db_path
TURSO_AUTH_TOKEN = env("TURSO_AUTH_TOKEN")

# --- Wallet ---
WALLET_ASSET = env("WALLET_ASSET", "USDC")
WALLET_NETWORK = env("WALLET_NETWORK", "stellar")  # stellar | trc20
WALLET_SECRET = env("WALLET_SECRET")
USDC_ISSUER = env("USDC_ISSUER", "GA5ZSEJYB37JRC5AVCIA5MOP4RHTM335X2KGX3IHOJAPP5RE34K4KZVN")
HORIZON_URL = env("HORIZON_URL", "https://horizon.stellar.org")
TRONGRID_API_KEY = env("TRONGRID_API_KEY")

# --- Meru ---
MERU_ADDRESS = env("MERU_ADDRESS")
MERU_MEMO = env("MERU_MEMO")

# --- Sekirite ---
# Lokal: pwodwi yon sèl fwa epi konsève l nan .env pou sesyon yo siviv rekòmansman.
SESSION_SECRET = env("SESSION_SECRET")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    env_file = BASE_DIR / ".env"
    try:
        if env_file.exists() and "SESSION_SECRET=" in env_file.read_text():
            lines = env_file.read_text().splitlines()
            lines = [
                f"SESSION_SECRET={SESSION_SECRET}" if l.startswith("SESSION_SECRET=") else l
                for l in lines
            ]
            env_file.write_text("\n".join(lines) + "\n")
    except OSError:
        pass
CRON_SECRET = env("CRON_SECRET")

# --- Flux ---
MOCK_MODE_DEFAULT = env("MOCK_MODE", "true").lower() == "true"
PAYMENT_TIMEOUT_MIN = int(env("PAYMENT_TIMEOUT_MIN", "15"))
MOCK_AUTO_OK_S = int(env("MOCK_AUTO_OK_S", "15"))
POLL_INTERVAL_S = int(env("POLL_INTERVAL_S", "10"))
WORKER_INTERVAL_S = int(env("WORKER_INTERVAL_S", "10"))

# --- Notifikasyon ---
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")
SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASS = env("SMTP_PASS")
SMTP_FROM = env("SMTP_FROM")
SMTP_TO = env("SMTP_TO")
