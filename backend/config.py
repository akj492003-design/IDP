
import os
import math
from pathlib import Path

from dotenv import load_dotenv


# =========================================================
# BASE DIRECTORY AND ENVIRONMENT
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
if APP_ENV == "prod":
    APP_ENV = "production"


# =========================================================
# ENVIRONMENT HELPERS
# =========================================================

def _env_int(name, default, minimum=None, maximum=None):
    """Read and validate an integer environment variable."""

    raw_value = os.getenv(name, str(default))

    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a valid integer."
        ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"{name} must be at least {minimum}."
        )

    if maximum is not None and value > maximum:
        raise ValueError(
            f"{name} must not exceed {maximum}."
        )

    return value


def _env_float(name, default, minimum=None, maximum=None):
    """Read and validate a finite floating-point environment variable."""

    raw_value = os.getenv(name, str(default))

    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a valid number."
        ) from exc

    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")

    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")

    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}.")

    return value


def _env_bool(name, default=False):
    """Parse a boolean environment variable."""

    value = os.getenv(
        name,
        str(default)
    ).strip().lower()

    if value in {"true", "1", "yes", "on"}:
        return True

    if value in {"false", "0", "no", "off"}:
        return False

    raise ValueError(
        f"{name} must be a valid boolean value."
    )


def _env_list(name, default):
    """Read a comma-separated environment variable."""

    raw_value = os.getenv(name, default)

    return [
        item.strip().rstrip("/")
        for item in raw_value.split(",")
        if item.strip()
    ]


# =========================================================
# UPLOAD CONFIGURATION
# =========================================================

MAX_UPLOAD_MB = _env_int(
    "MAX_UPLOAD_MB",
    10,
    minimum=1,
    maximum=100
)

MAX_CONTENT_LENGTH = (
    MAX_UPLOAD_MB * 1024 * 1024
)


# =========================================================
# SESSION COOKIE VALIDATION
# =========================================================

COOKIE_SECURE = _env_bool(
    "COOKIE_SECURE",
    APP_ENV == "production",
)

COOKIE_SAMESITE = (
    os.getenv("COOKIE_SAMESITE", "Lax").strip().capitalize() or "Lax"
)

if COOKIE_SAMESITE not in {"Lax", "Strict", "None"}:
    raise ValueError("COOKIE_SAMESITE must be Lax, Strict, or None.")

if COOKIE_SAMESITE == "None" and not COOKIE_SECURE:
    raise ValueError(
        "COOKIE_SECURE must be True when COOKIE_SAMESITE=None."
    )

if APP_ENV == "production" and not COOKIE_SECURE:
    raise RuntimeError(
        "COOKIE_SECURE must be True in production."
    )


# =========================================================
# APPLICATION CONFIGURATION
# =========================================================

class Config:

    APP_ENV = APP_ENV

    # -----------------------------------------------------
    # FLASK
    # -----------------------------------------------------

    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        ""
    )

    JSON_SORT_KEYS = False

    # -----------------------------------------------------
    # UPLOAD CONFIGURATION
    # -----------------------------------------------------

    # Expose module-validated values to Flask's from_object(Config).
    MAX_UPLOAD_MB = MAX_UPLOAD_MB
    MAX_CONTENT_LENGTH = MAX_CONTENT_LENGTH

    # -----------------------------------------------------
    # MONGODB
    # -----------------------------------------------------

    # Host-run development uses port 27018.
    # Docker Compose overrides this with mongo:27017.
    MONGODB_URI = os.getenv(
        "MONGODB_URI",
        "mongodb://localhost:27018/?retryWrites=true&w=majority"
    )

    MONGODB_DB = os.getenv(
        "MONGODB_DB",
        "idp_system"
    )

    # -----------------------------------------------------
    # REDIS
    # -----------------------------------------------------

    REDIS_URL = os.getenv(
        "REDIS_URL",
        "redis://localhost:6379/0"
    )

    CELERY_BROKER_URL = os.getenv(
        "CELERY_BROKER_URL",
        "redis://localhost:6379/1"
    )

    CELERY_RESULT_BACKEND = os.getenv(
        "CELERY_RESULT_BACKEND",
        "redis://localhost:6379/2"
    )

    # -----------------------------------------------------
    # FRONTEND AND CORS
    # -----------------------------------------------------

    FRONTEND_ORIGINS = _env_list(
        "FRONTEND_ORIGINS",
        "http://localhost:5173"
    )

    FRONTEND_URL = os.getenv(
        "FRONTEND_URL",
        "http://localhost:5173"
    ).rstrip("/")

    # -----------------------------------------------------
    # FILE STORAGE
    # -----------------------------------------------------

    FILE_STORAGE_PATH = Path(
        os.getenv(
            "FILE_STORAGE_PATH",
            str(BASE_DIR / "storage")
        )
    ).resolve()

    EFFICIENTNET_MODEL_PATH = Path(
        os.getenv(
            "EFFICIENTNET_MODEL_PATH",
            str(BASE_DIR / "app" / "models" / "efficientnetb0_idp_classifier")
        )
    ).resolve()

    ORIENTATION_CONFIDENCE_THRESHOLD = _env_float(
        "ORIENTATION_CONFIDENCE_THRESHOLD",
        0.80,
        minimum=0.0,
        maximum=1.0,
    )

    # -----------------------------------------------------
    # FLASK SESSION
    # -----------------------------------------------------

    SESSION_TYPE = "mongodb"

    SESSION_PERMANENT = False

    SESSION_USE_SIGNER = True

    SESSION_COOKIE_HTTPONLY = True

    SESSION_COOKIE_SECURE = COOKIE_SECURE

    SESSION_COOKIE_SAMESITE = COOKIE_SAMESITE

    SESSION_MONGODB_DB = MONGODB_DB

    SESSION_MONGODB_COLLECT = "flask_sessions"

    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 7

    # -----------------------------------------------------
    # GOOGLE OAUTH
    # -----------------------------------------------------

    GOOGLE_OAUTH_CLIENT_ID = os.getenv(
        "GOOGLE_CLIENT_ID",
        os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    )

    GOOGLE_OAUTH_CLIENT_SECRET = os.getenv(
        "GOOGLE_CLIENT_SECRET",
        os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    )

    GOOGLE_REDIRECT_URI = os.getenv(
        "GOOGLE_REDIRECT_URI",
        FRONTEND_URL
    ).strip()

    # -----------------------------------------------------
    # BREVO EMAIL AND SMS
    # -----------------------------------------------------

    BREVO_API_KEY = os.getenv(
        "BREVO_API_KEY",
        ""
    )

    BREVO_SMS_SENDER = os.getenv(
        "BREVO_SMS_SENDER",
        "IDP System"
    )

    BREVO_EMAIL_SENDER = os.getenv(
        "BREVO_SENDER_EMAIL",
        os.getenv("BREVO_EMAIL_SENDER", "")
    )

    BREVO_EMAIL_SENDER_NAME = os.getenv(
        "BREVO_EMAIL_SENDER_NAME",
        "IDP System"
    )

    # -----------------------------------------------------
    # PASSWORD RESET
    # -----------------------------------------------------

    RESET_TOKEN_TTL_MINUTES = _env_int(
        "RESET_TOKEN_TTL_MINUTES",
        30,
        minimum=5,
        maximum=120
    )

    # -----------------------------------------------------
    # RAZORPAY
    # -----------------------------------------------------

    RAZORPAY_KEY_ID = os.getenv(
        "RAZORPAY_KEY_ID",
        ""
    )

    RAZORPAY_KEY_SECRET = os.getenv(
        "RAZORPAY_KEY_SECRET",
        ""
    )

    RAZORPAY_WEBHOOK_SECRET = os.getenv(
        "RAZORPAY_WEBHOOK_SECRET",
        ""
    )

    # -----------------------------------------------------
    # OCR CONFIGURATION
    # -----------------------------------------------------

    PADDLEOCR_LANG = os.getenv(
        "PADDLEOCR_LANG",
        "en"
    )

    ENABLE_IMAGE_CLASSIFIER = _env_bool(
        "ENABLE_IMAGE_CLASSIFIER",
        True
    )

    # -----------------------------------------------------
    # RATE LIMITING
    # -----------------------------------------------------

    RATELIMIT_STORAGE_URI = REDIS_URL
