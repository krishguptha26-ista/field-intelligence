"""Central configuration. Reads .env; never logs secret values."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# --- Vertex AI route (alternative to GEMINI_API_KEY) ---
# GEMINI_VERTEX_SA_JSON may be a filesystem path OR the service-account JSON inline.
GEMINI_VERTEX_PROJECT = os.getenv("GEMINI_VERTEX_PROJECT", "")
GEMINI_VERTEX_LOCATION = os.getenv("GEMINI_VERTEX_LOCATION", "us-central1")
_SA_RAW = os.getenv("GEMINI_VERTEX_SA_JSON", "")


def _materialise_sa() -> str | None:
    """Make service-account credentials available to google-auth.

    Accepts inline JSON (written to a 0600 file under var/) or an existing
    path. Returns the credentials path or None. Never logs contents."""
    if not _SA_RAW:
        return None
    if _SA_RAW.strip().startswith("{"):
        path = Path(__file__).resolve().parent.parent / "var" / "gemini_sa.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(_SA_RAW)
        path.chmod(0o600)
    else:
        path = Path(_SA_RAW).expanduser()
        if not path.exists():
            return None
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    return str(path)


GEMINI_VERTEX_SA_PATH = _materialise_sa() if LLM_PROVIDER == "gemini" else None
GEMINI_CONFIGURED = bool(GEMINI_API_KEY or (GEMINI_VERTEX_PROJECT and GEMINI_VERTEX_SA_PATH))

# Optional latency tuning: gemini-2.5-flash "thinks" by default (~8s avg observed).
# Set LLM_THINKING_BUDGET=0 in .env to disable thinking for structured extraction
# (faster + cheaper). Unset = SDK default behaviour (proven working).
_tb = os.getenv("LLM_THINKING_BUDGET", "").strip()
LLM_THINKING_BUDGET: int | None = int(_tb) if _tb.lstrip("-").isdigit() else None

APP_VERSION = "0.2.0"
# Identifies this app to OSM/Nominatim, whose usage policy requires a real
# User-Agent and a contact. Override per deployment.
CONTACT_URL = os.getenv("CONTACT_URL", "https://github.com/fieldintel-poc")

# The adversarial challenge panel. On by default: it is the product's stated
# personality made operational. Off is for latency-sensitive demos and for
# measuring what the panel actually changes (see the eval harness).
ENABLE_CHALLENGE_PANEL = _bool("ENABLE_CHALLENGE_PANEL", True)
# Three live challenger calls are valuable before a binding decision, but make
# capture painfully slow on SQLite/POC. Keep the feature available on demand in
# review; opt into capture-time execution for production experiments.
CHALLENGE_PANEL_DURING_CAPTURE = _bool("CHALLENGE_PANEL_DURING_CAPTURE", False)

# Scraped public-web signals are OFF unless explicitly enabled. See ADR-010:
# the capability exists, the default does not assume anyone's legal position.
ENABLE_SCRAPED_SIGNALS = _bool("ENABLE_SCRAPED_SIGNALS", False)

APP_ENV = os.getenv("APP_ENV", "development")
APP_DEMO_MODE = _bool("APP_DEMO_MODE", True)
DEMO_USERNAME = os.getenv("DEMO_USERNAME", "demo-user").strip()
DEMO_PASSWORD = os.getenv(
    "DEMO_PASSWORD",
    "Broadpeak-demo-user" if APP_ENV != "production" else "",
)
SESSION_SECRET = os.getenv(
    "SESSION_SECRET",
    "local-fieldintel-session-secret-change-me" if APP_ENV != "production" else "",
)
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
LOGIN_NOTIFICATION_WEBHOOK_URL = os.getenv("LOGIN_NOTIFICATION_WEBHOOK_URL", "").strip()
CORS_ORIGINS = (["*"] if APP_ENV != "production" else
                [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",")
                 if origin.strip()])
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'var' / 'fieldintel.db'}")
# SQLAlchemy otherwise assumes the legacy psycopg2 driver for a plain
# postgresql:// URL. The deployment uses maintained psycopg 3.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_STORAGE_BUCKET = os.getenv(
    "SUPABASE_STORAGE_BUCKET", "field-intelligence-evidence"
).strip()
REMOTE_STORAGE_CONFIGURED = bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)
MAX_LLM_CALLS_PER_AUDIT = int(os.getenv("MAX_LLM_CALLS_PER_AUDIT", "25"))
MAX_LLM_CALLS_PER_HOUR = int(os.getenv("MAX_LLM_CALLS_PER_HOUR", "500"))
LLM_BUDGET_EXTENSION_CALLS = int(os.getenv("LLM_BUDGET_EXTENSION_CALLS", "15"))
MAX_LLM_BUDGET_ACKNOWLEDGEMENTS = int(os.getenv("MAX_LLM_BUDGET_ACKNOWLEDGEMENTS", "2"))

FIXTURES_DIR = ROOT / "data" / "fixtures"
GOLDEN_DIR = ROOT / "data" / "golden"
PROMPTS_DIR = ROOT / "prompts"
VAR_DIR = ROOT / "var"
VAR_DIR.mkdir(exist_ok=True)
# Uploaded photos live under var/ (gitignored). Digest-addressed, so the image a
# reviewer opens is provably the one the description was generated from.
UPLOADS_DIR = VAR_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


def validate_runtime() -> None:
    """Fail closed when a public deployment omits its access-control secrets."""
    if APP_ENV == "production":
        missing = [name for name, value in {
            "DEMO_PASSWORD": DEMO_PASSWORD,
            "SESSION_SECRET": SESSION_SECRET,
        }.items() if not value]
        if missing:
            raise RuntimeError(
                "production requires protected Render environment values: "
                + ", ".join(missing)
            )
        if len(SESSION_SECRET) < 32:
            raise RuntimeError("SESSION_SECRET must contain at least 32 characters")
        if (LOGIN_NOTIFICATION_WEBHOOK_URL
                and not LOGIN_NOTIFICATION_WEBHOOK_URL.startswith("https://")):
            raise RuntimeError("LOGIN_NOTIFICATION_WEBHOOK_URL must use https in production")
        supplied_storage = bool(SUPABASE_URL) + bool(SUPABASE_SERVICE_ROLE_KEY)
        if supplied_storage == 1:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be supplied together"
            )
        if SUPABASE_URL and not SUPABASE_URL.startswith("https://"):
            raise RuntimeError("SUPABASE_URL must use https in production")


def key_status() -> dict:
    """Report key EXISTENCE only — never values (spec §6)."""
    return {
        "gemini_key_present": bool(GEMINI_API_KEY),
        "gemini_vertex_configured": bool(GEMINI_VERTEX_PROJECT and GEMINI_VERTEX_SA_PATH),
        "gemini_configured": GEMINI_CONFIGURED,
        "database_backend": "postgresql" if DATABASE_URL.startswith("postgresql") else "sqlite",
        "persistent_media_configured": REMOTE_STORAGE_CONFIGURED,
        "maps_key_present": bool(GOOGLE_MAPS_API_KEY),
        "demo_mode": APP_DEMO_MODE,
        "llm_provider": LLM_PROVIDER,
        "llm_model": LLM_MODEL,
    }
