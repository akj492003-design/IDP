
import hmac
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, session
from flask_cors import CORS
from flask_session import Session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pymongo import MongoClient, ASCENDING, DESCENDING
from gridfs import GridFSBucket
from redis import Redis
from werkzeug.exceptions import HTTPException

from config import Config


# =========================================================
# FLASK EXTENSIONS
# =========================================================

session_ext = Session()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
)


# =========================================================
# ORIGIN VALIDATION
# =========================================================

def _normalize_origin(value):
    """Normalize an HTTP origin for consistent comparison."""

    if not isinstance(value, str):
        return None

    value = value.strip()

    if not value or value == "null":
        return None

    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Reject malformed ports.
    except ValueError:
        return None

    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    if not parsed.netloc or parsed.username or parsed.password:
        return None

    if parsed.path or parsed.query or parsed.fragment:
        return None

    return (
        f"{parsed.scheme.lower()}://"
        f"{parsed.netloc.lower()}"
    )


def _get_allowed_origins(app):
    """Return normalized configured frontend origins."""

    configured = app.config.get("FRONTEND_ORIGINS") or []

    if isinstance(configured, str):
        configured = [
            item.strip()
            for item in configured.split(",")
            if item.strip()
        ]

    origins = set()

    for origin in configured:
        normalized = _normalize_origin(origin)

        if normalized is None:
            raise RuntimeError(
                f"Invalid frontend origin configured: {origin!r}"
            )

        if normalized == "*":
            raise RuntimeError(
                "Wildcard origins cannot be used with credentialed CORS."
            )

        origins.add(normalized)

    if not origins:
        raise RuntimeError(
            "At least one valid FRONTEND_ORIGINS entry is required."
        )

    return origins


# =========================================================
# DATABASE INDEX INITIALIZATION
# =========================================================

def _create_database_indexes(db):
    """Create the indexes required by the application."""

    # User account indexes.
    db.users.create_index(
        [("email", ASCENDING)],
        unique=True,
        sparse=True,
    )

    db.users.create_index(
        [("phone", ASCENDING)],
        unique=True,
        sparse=True,
    )

    # A Google subject must resolve to at most one local account.
    duplicate_google_subjects = list(
        db.users.aggregate([
            {
                "$match": {
                    "google_sub": {"$type": "string"}
                }
            },
            {
                "$group": {
                    "_id": "$google_sub",
                    "count": {"$sum": 1},
                }
            },
            {"$match": {"count": {"$gt": 1}}},
            {"$limit": 5},
        ])
    )
    if duplicate_google_subjects:
        raise RuntimeError(
            "Duplicate google_sub values exist in the users collection. "
            "Resolve them before creating the unique Google identity index."
        )

    db.users.create_index(
        [("google_sub", ASCENDING)],
        unique=True,
        partialFilterExpression={"google_sub": {"$type": "string"}},
        name="unique_google_sub",
    )

    # Document listing and filtering.
    db.documents.create_index(
        [
            ("user_id", ASCENDING),
            ("created_at", DESCENDING),
        ],
    )

    db.documents.create_index(
        [
            ("user_id", ASCENDING),
            ("status", ASCENDING),
        ],
    )

    db.documents.create_index(
        [
            ("user_id", ASCENDING),
            ("status", ASCENDING),
            ("processed_at", DESCENDING),
        ],
        name="user_status_processed_at",
    )

    db.documents.create_index(
        [("user_id", ASCENDING), ("file_sha256", ASCENDING)],
        name="user_document_hash_lookup",
    )

    # Activity history.
    db.activities.create_index(
        [
            ("user_id", ASCENDING),
            ("created_at", DESCENDING),
        ],
    )

    # Expiring OTP and password-reset records.
    db.otp_challenges.create_index(
        "expires_at",
        expireAfterSeconds=0,
    )

    db.reset_tokens.create_index(
        "expires_at",
        expireAfterSeconds=0,
    )

    # Unique quota records.
    db.upload_usage.create_index(
        [("user_id", ASCENDING)],
        unique=True,
    )

    db.subscription_usage.create_index(
        [("user_id", ASCENDING)],
        unique=True,
    )

    # Subscription lookup.
    db.subscriptions.create_index(
        [
            ("user_id", ASCENDING),
            ("status", ASCENDING),
        ],
    )

    # User settings lookup.
    db.settings.create_index(
        [("user_id", ASCENDING)],
        unique=True,
    )


# =========================================================
# REDIS HEALTH CHECK
# =========================================================

def _check_redis(url):
    """Check a Redis connection and always close its client."""

    if not url:
        return "unavailable"

    client = None

    try:
        client = Redis.from_url(
            url,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )

        client.ping()

        return "ok"

    except Exception:
        return "unavailable"

    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


# =========================================================
# APPLICATION FACTORY
# =========================================================

def create_app(test_config=None):

    app = Flask(__name__)

    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)

    # -----------------------------------------------------
    # REQUIRED CONFIGURATION
    # -----------------------------------------------------

    if not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "SECRET_KEY must be configured in backend/.env "
            "before the API starts."
        )

    if not app.config.get("MONGODB_URI"):
        raise RuntimeError(
            "MONGODB_URI must be configured."
        )

    if not app.config.get("MONGODB_DB"):
        raise RuntimeError(
            "MONGODB_DB must be configured."
        )

    allowed_origins = _get_allowed_origins(app)
    app.extensions["allowed_origins"] = allowed_origins

    # -----------------------------------------------------
    # MONGODB INITIALIZATION
    # -----------------------------------------------------

    mongo_client = MongoClient(
        app.config["MONGODB_URI"],
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
        maxPoolSize=50,
        minPoolSize=2,
        retryWrites=True,
        appname="IDP System",
    )

    try:

        # Explicit connectivity check.
        mongo_client.admin.command("ping")

        db = mongo_client[
            app.config["MONGODB_DB"]
        ]

        # Register database extensions.
        app.extensions["mongo_client"] = mongo_client
        app.extensions["mongo_db"] = db

        # -------------------------------------------------
        # FLASK SESSION INITIALIZATION
        # -------------------------------------------------

        app.config["SESSION_MONGODB"] = mongo_client

        app.config["SESSION_MONGODB_DB"] = (
            app.config["MONGODB_DB"]
        )

        app.config.setdefault(
            "SESSION_MONGODB_COLLECT",
            "flask_sessions",
        )

        session_ext.init_app(app)

        # -------------------------------------------------
        # GRIDFS INITIALIZATION
        # -------------------------------------------------

        gridfs_bucket = GridFSBucket(
            db,
            bucket_name="idp_document_files",
        )

        app.extensions["gridfs_bucket"] = gridfs_bucket

        # -------------------------------------------------
        # DATABASE INDEXES
        # -------------------------------------------------

        _create_database_indexes(db)

    except Exception as exc:

        mongo_client.close()

        raise RuntimeError(
            "Application initialization failed while "
            "connecting to MongoDB or initializing storage."
        ) from exc

    # =====================================================
    # CORS CONFIGURATION
    # =====================================================

    CORS(
        app,
        origins=sorted(allowed_origins),
        supports_credentials=True,
        allow_headers=[
            "Content-Type",
            "X-Requested-With",
            "X-CSRF-Token",
            "Authorization",
        ],
        methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ],
    )

    # =====================================================
    # UNTRUSTED ORIGIN PROTECTION
    # =====================================================

    @app.before_request
    def reject_untrusted_browser_origin():
        """Validate browser origins and session-bound CSRF tokens."""

        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None

        # Razorpay authenticates webhooks using their signature.
        if request.endpoint == "payments.razorpay_webhook":
            return None

        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")

        if not origin and referer:
            try:
                parsed = urlsplit(referer)
                if parsed.scheme and parsed.netloc:
                    origin = f"{parsed.scheme}://{parsed.netloc}"
            except ValueError:
                origin = None

        if origin:
            normalized_origin = _normalize_origin(origin)
            if (
                normalized_origin is None
                or normalized_origin not in allowed_origins
            ):
                return jsonify({
                    "error": "untrusted_origin",
                    "message": "This request origin is not allowed.",
                }), 403

        # Requests with an application session cookie must have a valid
        # session-bound token. Requests without cookies remain available to
        # non-browser service clients; browser-origin validation still applies.
        session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        has_session_cookie = bool(request.cookies.get(session_cookie_name))
        has_session_state = bool(
            session.get("user_id") or session.get("csrf_token")
        )

        if has_session_cookie and not origin:
            return jsonify({
                "error": "csrf_failed",
                "message": "The request origin could not be verified.",
            }), 403

        if has_session_cookie or has_session_state:
            expected_token = session.get("csrf_token")
            supplied_token = request.headers.get("X-CSRF-Token", "")

            if (
                not isinstance(expected_token, str)
                or not expected_token
                or not isinstance(supplied_token, str)
                or not supplied_token
                or not hmac.compare_digest(expected_token, supplied_token)
            ):
                return jsonify({
                    "error": "csrf_failed",
                    "message": (
                        "Your security token is missing or expired. "
                        "Refresh the page and try again."
                    ),
                }), 403

        return None

    # =====================================================
    # RATE LIMITER INITIALIZATION
    # =====================================================

    limiter.init_app(app)

    # =====================================================
    # BLUEPRINT REGISTRATION
    # =====================================================

    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.documents import bp as documents_bp
    from app.blueprints.dashboard import bp as dashboard_bp
    from app.blueprints.history import bp as history_bp
    from app.blueprints.classification import bp as classification_bp
    from app.blueprints.settings import bp as settings_bp
    from app.blueprints.subscriptions import bp as subscriptions_bp
    from app.blueprints.payments import bp as payments_bp

    app.register_blueprint(
        auth_bp,
        url_prefix="/api/auth",
    )

    app.register_blueprint(
        documents_bp,
        url_prefix="/api/documents",
    )

    app.register_blueprint(
        dashboard_bp,
        url_prefix="/api/dashboard",
    )

    app.register_blueprint(
        history_bp,
        url_prefix="/api/history",
    )

    app.register_blueprint(
        classification_bp,
        url_prefix="/api/classification",
    )

    app.register_blueprint(
        settings_bp,
        url_prefix="/api/settings",
    )

    app.register_blueprint(
        subscriptions_bp,
        url_prefix="/api/subscriptions",
    )

    app.register_blueprint(
        payments_bp,
        url_prefix="/api/payments",
    )

    # =====================================================
    # CELERY INITIALIZATION
    # =====================================================

    from app.tasks.document_tasks import register_tasks

    celery = register_tasks(app)

    app.extensions["celery"] = celery

    # =====================================================
    # HEALTH ENDPOINT
    # =====================================================

    @app.get("/api/health")
    def health():

        # MongoDB connectivity.
        try:
            db.command("ping")
            mongo_state = "ok"

        except Exception:
            app.logger.exception(
                "MongoDB health check failed"
            )

            mongo_state = "unavailable"

        # Application Redis connectivity.
        redis_state = _check_redis(
            app.config.get("REDIS_URL")
        )

        # Celery broker connectivity.
        celery_broker_state = _check_redis(
            app.config.get("CELERY_BROKER_URL")
        )

        # Celery result-backend connectivity.
        celery_backend_state = _check_redis(
            app.config.get("CELERY_RESULT_BACKEND")
        )

        services = {
            "mongodb": mongo_state,
            "redis": redis_state,
            "celery_broker": celery_broker_state,
            "celery_result_backend": celery_backend_state,
        }

        healthy = all(
            state == "ok"
            for state in services.values()
        )

        return jsonify({
            "ok": healthy,
            "services": services,
        }), 200 if healthy else 503

    # =====================================================
    # REQUEST TOO LARGE
    # =====================================================

    @app.errorhandler(413)
    def too_large(_error):

        return jsonify({
            "error": "file_too_large",
            "message": (
                f"Maximum upload size is "
                f"{app.config['MAX_UPLOAD_MB']} MB."
            ),
        }), 413

    # =====================================================
    # RESOURCE NOT FOUND
    # =====================================================

    @app.errorhandler(404)
    def not_found(_error):

        return jsonify({
            "error": "not_found",
            "message": (
                "The requested resource was not found."
            ),
        }), 404

    # =====================================================
    # REQUEST RATE LIMIT
    # =====================================================

    @app.errorhandler(429)
    def rate_limited(_error):

        return jsonify({
            "error": "rate_limited",
            "message": (
                "Too many requests. Please try again later."
            ),
        }), 429

    # =====================================================
    # GLOBAL EXCEPTION HANDLER
    # =====================================================

    @app.errorhandler(Exception)
    def api_error(error):

        if isinstance(error, HTTPException):

            return jsonify({
                "error": (
                    error.name.lower().replace(" ", "_")
                ),
                "message": error.description,
            }), error.code

        app.logger.error(
            "Unhandled API exception",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )

        return jsonify({
            "error": "internal_server_error",
            "message": (
                "An unexpected server error occurred."
            ),
        }), 500

    return app
