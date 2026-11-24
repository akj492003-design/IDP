
import hashlib
import hmac
import re
import secrets

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
    session,
    g,
)

from argon2 import PasswordHasher
from argon2 import exceptions as argon_exceptions

from bson import ObjectId

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.security import (
    get_db,
    safe_user,
    login_required,
)

from app.services.brevo import (
    send_sms,
    send_email,
)

from app import limiter


# =========================================================
# BLUEPRINT CONFIGURATION
# =========================================================

bp = Blueprint("auth", __name__)

_hasher = PasswordHasher()
_DUMMY_PASSWORD_HASH = _hasher.hash(secrets.token_urlsafe(32))

OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256

EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$",
    re.IGNORECASE,
)

PHONE_PATTERN = re.compile(
    r"^\+?[0-9][0-9\s().-]{6,30}$"
)


# =========================================================
# REQUEST HELPERS
# =========================================================

def _json_body():
    """Return a JSON object or an empty dictionary."""

    data = request.get_json(silent=True)

    return data if isinstance(data, dict) else {}


def _valid_email(email):
    """Validate email format and maximum length."""

    return (
        isinstance(email, str)
        and len(email) <= 254
        and bool(EMAIL_PATTERN.fullmatch(email.strip()))
    )


def _valid_password(password):
    """Validate password type and length."""

    return (
        isinstance(password, str)
        and MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH
    )


def _utc(value):
    """Normalize datetime values to timezone-aware UTC."""

    if not isinstance(value, datetime):
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _valid_otp(code):
    """Check that an OTP contains exactly six digits."""

    return (
        isinstance(code, str)
        and bool(re.fullmatch(r"\d{6}", code))
    )


def _validate_google_popup_request():
    """Validate the Google GIS popup custom header and calling-page origin."""

    supplied_header = request.headers.get("X-Requested-With", "")
    supplied_origin = request.headers.get("Origin", "").strip().rstrip("/")
    allowed_origins = current_app.extensions.get("allowed_origins", set())

    if (
        supplied_header != "XmlHttpRequest"
        or not supplied_origin
        or supplied_origin not in allowed_origins
    ):
        return jsonify({
            "error": "untrusted_oauth_request",
            "message": "The Google sign-in request could not be verified.",
        }), 403

    return None


def _establish_user_session(user):
    """Rotate the Flask-Session ID and establish a new authenticated session."""

    version = user.get("session_version", 0)
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 0
    ):
        raise RuntimeError("The user session_version value is invalid.")

    session.clear()
    session["user_id"] = str(user["_id"])
    session["session_version"] = version
    session["csrf_token"] = secrets.token_urlsafe(32)

    regenerate = getattr(
        current_app.session_interface,
        "regenerate",
        None,
    )
    if not callable(regenerate):
        session.clear()
        raise RuntimeError(
            "Flask-Session session ID regeneration is unavailable. "
            "Use a supported Flask-Session version."
        )

    regenerate(session)
    return session["csrf_token"]


# =========================================================
# OTP SECURITY
# =========================================================

def _otp_digest(code, salt):
    """
    Generate an OTP digest using the existing pepper-and-salt
    scheme to preserve compatibility with existing challenges.
    """

    pepper = current_app.config["SECRET_KEY"]

    message = f"{pepper}:{salt}:{code}"

    return hashlib.sha256(
        message.encode("utf-8")
    ).hexdigest()


def _deliver(channel, destination, code, purpose):
    """Deliver OTP through the configured email or SMS provider."""

    message = (
        f"Your IDP System {purpose} code is {code}. "
        f"It expires in 10 minutes. Do not share it."
    )

    if channel == "sms":

        send_sms(
            destination,
            message,
        )

    elif channel == "email":

        send_email(
            destination,
            f"IDP System {purpose.title()} code",
            f"<p>{message}</p>",
            message,
        )

    else:
        raise ValueError("Unsupported OTP delivery channel.")


def _new_otp_challenge(
    user_id,
    channel,
    destination,
    purpose,
):
    """Create and store a salted OTP challenge."""

    now = datetime.now(timezone.utc)

    challenge_id = secrets.token_urlsafe(24)

    code = f"{secrets.randbelow(1000000):06d}"

    salt = secrets.token_hex(16)

    destination_hash = hashlib.sha256(
        destination.lower().encode("utf-8")
    ).hexdigest()

    challenge = {
        "challenge_id": challenge_id,
        "user_id": (
            str(user_id)
            if user_id is not None
            else None
        ),
        "channel": channel,
        "destination_hash": destination_hash,
        "purpose": purpose,
        "code_hash": _otp_digest(code, salt),
        "salt": salt,
        "attempts": 0,
        "expires_at": now + timedelta(
            minutes=OTP_TTL_MINUTES
        ),
        "created_at": now,
    }

    get_db().otp_challenges.insert_one(challenge)

    return challenge_id, code


def _consume_otp_attempt(challenge, code, purpose, require_user=False):
    """
    Validate and atomically consume a challenge.

    A challenge cannot be successfully consumed twice.
    Wrong codes increment the attempt counter atomically.
    """

    now = datetime.now(timezone.utc)

    expires_at = _utc(
        challenge.get("expires_at")
    )

    if (
        not expires_at
        or expires_at <= now
        or challenge.get("attempts", 0) >= OTP_MAX_ATTEMPTS
    ):
        return None

    if require_user and not challenge.get("user_id"):
        return None

    expected = challenge.get("code_hash", "")

    salt = challenge.get("salt", "")

    actual = _otp_digest(
        code,
        salt,
    )

    if not _valid_otp(code) or not hmac.compare_digest(
        expected,
        actual,
    ):

        # Only increment attempts while the challenge is valid.
        get_db().otp_challenges.update_one(
            {
                "_id": challenge["_id"],
                "purpose": purpose,
                "attempts": {
                    "$lt": OTP_MAX_ATTEMPTS
                },
                "expires_at": {
                    "$gt": now
                },
            },
            {
                "$inc": {
                    "attempts": 1
                }
            },
        )

        return None

    # Atomically claim the successful challenge.
    consumed = get_db().otp_challenges.find_one_and_delete(
        {
            "_id": challenge["_id"],
            "purpose": purpose,
            "code_hash": expected,
            "attempts": {
                "$lt": OTP_MAX_ATTEMPTS
            },
            "expires_at": {
                "$gt": now
            },
        }
    )

    return consumed


def _generic_reset_response(challenge_id=None):
    """
    Keep known and unknown account responses consistent.

    This prevents account-existence disclosure through response
    status and message differences.
    """

    response = {
        "message": (
            "If the account exists, a verification code "
            "or reset link has been sent."
        )
    }

    if challenge_id:
        response["challenge_id"] = challenge_id
        response["expires_in"] = OTP_TTL_MINUTES * 60

    return jsonify(response)


# =========================================================
# CSRF TOKEN
# =========================================================

@bp.get("/csrf")
def csrf_token():
    """Create or return the current session-bound CSRF token."""

    token = session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token

    return jsonify({"csrf_token": token})


# =========================================================
# REGISTRATION
# =========================================================

@bp.post("/register")
@limiter.limit("5 per hour")
def register():

    data = _json_body()

    name = data.get("full_name") or data.get("name") or ""

    email = data.get("email") or ""

    phone = data.get("phone") or ""

    password = data.get("password") or ""

    if not isinstance(name, str):
        return jsonify({
            "error": "validation_error",
            "message": "Enter a valid full name.",
        }), 400

    if not isinstance(email, str):
        return jsonify({
            "error": "validation_error",
            "message": "Enter a valid email address.",
        }), 400

    if not isinstance(phone, str):
        return jsonify({
            "error": "validation_error",
            "message": "Enter a valid phone number.",
        }), 400

    name = name.strip()

    email = email.strip().lower()

    phone = phone.strip()

    if len(name) < 2 or len(name) > 100:
        return jsonify({
            "error": "validation_error",
            "message": "Name must contain between 2 and 100 characters.",
        }), 400

    if not _valid_email(email):
        return jsonify({
            "error": "validation_error",
            "message": "Enter a valid email address.",
        }), 400

    if not _valid_password(password):
        return jsonify({
            "error": "validation_error",
            "message": (
                "Password must contain between 10 and 256 characters."
            ),
        }), 400

    if phone and not PHONE_PATTERN.fullmatch(phone):
        return jsonify({
            "error": "validation_error",
            "message": "Enter a valid phone number with country code.",
        }), 400

    now = datetime.now(timezone.utc)

    user = {
        "full_name": name,
        "email": email,
        "password_hash": _hasher.hash(password),
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "role": "user",
        "email_verified": False,
        "phone_verified": False,
        "auth_provider": "password",
        "session_version": 0,
    }

    if phone:
        user["phone"] = phone

    try:

        result = get_db().users.insert_one(user)

    except DuplicateKeyError:

        return jsonify({
            "error": "account_exists",
            "message": (
                "An account with this email or phone already exists."
            ),
        }), 409

    except PyMongoError:

        current_app.logger.exception(
            "User registration database operation failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": "Registration is temporarily unavailable.",
        }), 503

    user["_id"] = result.inserted_id

    return jsonify({
        "message": "Account created. Sign in to continue.",
        "user": safe_user(user),
    }), 201


# =========================================================
# LOGIN
# =========================================================

@bp.post("/login")
@limiter.limit("10 per 15 minutes")
def login():

    data = _json_body()

    email = data.get("email") or ""

    password = data.get("password") or ""

    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify({
            "error": "invalid_credentials",
            "message": "Email or password is incorrect.",
        }), 401

    email = email.strip().lower()

    if not email or len(email) > 254:
        return jsonify({
            "error": "invalid_credentials",
            "message": "Email or password is incorrect.",
        }), 401

    if len(password) > MAX_PASSWORD_LENGTH:
        return jsonify({
            "error": "invalid_credentials",
            "message": "Email or password is incorrect.",
        }), 401

    try:

        user = get_db().users.find_one({
            "email": email
        })

    except PyMongoError:

        current_app.logger.exception(
            "Login database lookup failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": "Sign-in is temporarily unavailable.",
        }), 503

    stored_hash = (
        user.get("password_hash")
        if user and isinstance(user.get("password_hash"), str)
        else _DUMMY_PASSWORD_HASH
    )

    try:

        valid = _hasher.verify(
            stored_hash,
            password,
        )

    except (
        argon_exceptions.VerifyMismatchError,
        argon_exceptions.VerificationError,
        argon_exceptions.InvalidHashError,
    ):

        valid = False

    if (
        not user
        or not user.get("password_hash")
        or user.get("status", "active") != "active"
        or not valid
    ):

        return jsonify({
            "error": "invalid_credentials",
            "message": "Email or password is incorrect.",
        }), 401

    # Rehash outdated Argon2 hashes.
    try:

        if _hasher.check_needs_rehash(user["password_hash"]):

            get_db().users.update_one(
                {
                    "_id": user["_id"]
                },
                {
                    "$set": {
                        "password_hash": _hasher.hash(password)
                    }
                },
            )

    except Exception:

        # Rehash failure must not expose credentials or
        # incorrectly report that a valid password is wrong.
        current_app.logger.exception(
            "Password hash upgrade failed"
        )

    csrf = _establish_user_session(user)

    return jsonify({
        "message": "Signed in.",
        "user": safe_user(user),
        "csrf_token": csrf,
    })


# =========================================================
# CURRENT USER
# =========================================================

@bp.get("/me")
@login_required
def me():

    return jsonify({
        "authenticated": True,
        "user": safe_user(g.user),
    })


# =========================================================
# LOGOUT
# =========================================================

@bp.post("/logout")
def logout():

    session.clear()

    return jsonify({
        "message": "Signed out.",
    })


# =========================================================
# GOOGLE OAUTH
# =========================================================

@bp.post("/google")
@limiter.limit("10 per 15 minutes")
def google_login():

    rejection = _validate_google_popup_request()
    if rejection is not None:
        return rejection

    data = _json_body()

    code = data.get("code")

    client_id = current_app.config[
        "GOOGLE_OAUTH_CLIENT_ID"
    ]

    client_secret = current_app.config[
        "GOOGLE_OAUTH_CLIENT_SECRET"
    ]

    if not client_id or not client_secret:

        return jsonify({
            "error": "oauth_not_configured",
            "message": (
                "Google OAuth is not configured on the server."
            ),
        }), 503

    if not isinstance(code, str) or not code or len(code) > 8192:

        return jsonify({
            "error": "validation_error",
            "message": "Google authorization code is required.",
        }), 400

    # -----------------------------------------------------
    # EXCHANGE AUTHORIZATION CODE
    # -----------------------------------------------------

    try:

        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": current_app.config[
                    "GOOGLE_REDIRECT_URI"
                ],
                "grant_type": "authorization_code",
            },
            timeout=15,
        )

    except requests.RequestException:

        current_app.logger.exception(
            "Google OAuth token endpoint unavailable"
        )

        return jsonify({
            "error": "oauth_unavailable",
            "message": (
                "Google sign-in is temporarily unavailable. Try again."
            ),
        }), 503

    if token_response.status_code != 200:

        try:
            google_error = token_response.json()
        except ValueError:
            google_error = {}

        current_app.logger.warning(
            "Google OAuth token exchange rejected: status=%s error=%s",
            token_response.status_code,
            str(google_error.get("error", "unknown"))[:100],
        )

        status_code = (
            503
            if token_response.status_code >= 500
            else 401
        )

        return jsonify({
            "error": "oauth_failed",
            "message": "Google sign-in could not be verified.",
        }), status_code

    # -----------------------------------------------------
    # VERIFY GOOGLE IDENTITY TOKEN
    # -----------------------------------------------------

    try:

        token_data = token_response.json()

        identity_token = token_data["id_token"]

        from google.oauth2 import id_token

        from google.auth.transport import requests as google_requests

        claims = id_token.verify_oauth2_token(
            identity_token,
            google_requests.Request(),
            client_id,
        )

    except Exception:

        current_app.logger.exception(
            "Google identity token validation failed"
        )

        return jsonify({
            "error": "oauth_failed",
            "message": (
                "Google returned an invalid identity token."
            ),
        }), 401

    email = str(
        claims.get("email") or ""
    ).strip().lower()

    if (
        not claims.get("sub")
        or not _valid_email(email)
        or claims.get("email_verified") is not True
    ):

        return jsonify({
            "error": "oauth_failed",
            "message": (
                "Google account email is not verified."
            ),
        }), 401

    # -----------------------------------------------------
    # FIND OR CREATE USER
    # -----------------------------------------------------

    now = datetime.now(timezone.utc)

    try:

        # Google subject is the stable provider identity. Resolve it first so
        # returning users are not accidentally treated as a new account when
        # their Google email alias changes.
        user = get_db().users.find_one({"google_sub": claims["sub"]})
        if not user:
            user = get_db().users.find_one({"email": email})

        if not user:

            user_data = {
                "full_name": (
                    claims.get("name")
                    or email.split("@")[0]
                ),
                "email": email,
                "photo_url": claims.get("picture"),
                "google_sub": claims["sub"],
                "password_hash": None,
                "created_at": now,
                "updated_at": now,
                "status": "active",
                "role": "user",
                "email_verified": True,
                "auth_provider": "google",
                "session_version": 0,
            }

            try:

                user_id = get_db().users.insert_one(
                    user_data
                ).inserted_id

                user = {
                    **user_data,
                    "_id": user_id,
                }

            except DuplicateKeyError:

                # Handle concurrent Google sign-ins.
                user = get_db().users.find_one({
                    "$or": [{"google_sub": claims["sub"]}, {"email": email}]
                })
                if not user:
                    raise

        else:

            existing_sub = user.get("google_sub")
            if existing_sub and existing_sub != claims["sub"]:
                return jsonify({
                    "error": "google_account_conflict",
                    "message": "This email is already linked to a different Google account. Sign in using its linked account or contact support.",
                }), 409

            if user.get("status", "active") != "active":

                return jsonify({
                    "error": "account_disabled",
                    "message": (
                        "This account is currently unavailable."
                    ),
                }), 403

            get_db().users.update_one(
                {
                    "_id": user["_id"]
                },
                {
                    "$set": {
                        "email_verified": True,
                        "photo_url": claims.get("picture"),
                        "google_sub": claims["sub"],
                        "updated_at": now,
                    }
                },
            )

            user.update({
                "email_verified": True,
                "photo_url": claims.get("picture"),
                "google_sub": claims["sub"],
                "updated_at": now,
            })

    except PyMongoError:

        current_app.logger.exception(
            "Google account database operation failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": (
                "Google sign-in is temporarily unavailable."
            ),
        }), 503

    if not user or user.get("status", "active") != "active":

        return jsonify({
            "error": "account_disabled",
            "message": (
                "This account is currently unavailable."
            ),
        }), 403

    csrf = _establish_user_session(user)

    return jsonify({
        "message": "Signed in with Google.",
        "user": safe_user(user),
        "csrf_token": csrf,
    })


# =========================================================
# PASSWORD RESET - EMAIL LINK
# =========================================================

@bp.post("/password-reset/request-link")
@limiter.limit("5 per 15 minutes")
def request_password_reset_link():

    data = _json_body()

    email = data.get("email") or ""

    if not isinstance(email, str):
        return jsonify({
            "error": "validation_error",
            "message": "Enter a valid email address.",
        }), 400

    email = email.strip().lower()

    if not _valid_email(email):

        return jsonify({
            "error": "validation_error",
            "message": "Enter a valid email address.",
        }), 400

    # Identical public response for known and unknown accounts.
    generic_response = {
        "message": (
            "If an account matches that address, "
            "a password reset link has been sent."
        )
    }

    try:

        user = get_db().users.find_one({
            "email": email
        })

        if user:

            token = secrets.token_urlsafe(32)

            token_hash = hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest()

            now = datetime.now(timezone.utc)

            ttl_minutes = current_app.config[
                "RESET_TOKEN_TTL_MINUTES"
            ]

            get_db().reset_tokens.delete_many({
                "user_id": str(user["_id"]),
                "used": False,
            })

            get_db().reset_tokens.insert_one({
                "token_hash": token_hash,
                "user_id": str(user["_id"]),
                "expires_at": (
                    now + timedelta(minutes=ttl_minutes)
                ),
                "created_at": now,
                "used": False,
                "purpose": "password_reset_link",
            })

            reset_url = (
                f"{current_app.config['FRONTEND_URL']}"
                f"/reset-password?token={quote(token, safe='')}"
            )

            subject = "Reset your IDP account password"

            html = (
                "<p>We received a request to reset your "
                "IDP account password.</p>"
                f'<p><a href="{reset_url}">Reset password</a></p>'
                f"<p>This link expires in {ttl_minutes} minutes "
                "and can only be used once.</p>"
                "<p>If you did not request this change, "
                "ignore this email.</p>"
            )

            text = (
                "Reset your IDP password using this link: "
                + reset_url
                + "\n"
                + f"This link expires in {ttl_minutes} minutes "
                "and can only be used once.\n"
                + "If you did not request this change, "
                "ignore this email."
            )

            try:

                send_email(
                    email,
                    subject,
                    html,
                    text,
                )

            except Exception:

                current_app.logger.exception(
                    "Password reset email delivery failed"
                )

                get_db().reset_tokens.delete_one({
                    "token_hash": token_hash
                })

                # Keep the response identical for account security.

    except PyMongoError:

        current_app.logger.exception(
            "Password reset request database operation failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": (
                "Password reset is temporarily unavailable."
            ),
        }), 503

    return jsonify(generic_response)


# =========================================================
# PASSWORD RESET - REQUEST OTP
# =========================================================

@bp.post("/password-reset/request-otp")
@limiter.limit("5 per 15 minutes")
def request_password_reset_otp():

    data = _json_body()

    channel = data.get("channel")

    destination = data.get("destination") or ""

    if (
        channel not in ("email", "sms")
        or not isinstance(destination, str)
    ):

        return jsonify({
            "error": "validation_error",
            "message": (
                "Choose email or SMS and provide a destination."
            ),
        }), 400

    destination = destination.strip()

    if channel == "email":

        destination = destination.lower()

        if not _valid_email(destination):
            return jsonify({
                "error": "validation_error",
                "message": "Enter a valid email address.",
            }), 400

        query = {
            "email": destination
        }

    else:

        if not PHONE_PATTERN.fullmatch(destination):
            return jsonify({
                "error": "validation_error",
                "message": (
                    "Enter a valid phone number with country code."
                ),
            }), 400

        query = {
            "phone": destination
        }

    try:

        user = get_db().users.find_one(query)

        user_id = (
            user["_id"]
            if user
            else None
        )

        challenge_id, code = _new_otp_challenge(
            user_id=user_id,
            channel=channel,
            destination=destination,
            purpose="password_reset",
        )

        if user:

            try:

                _deliver(
                    channel,
                    destination,
                    code,
                    "password reset",
                )

            except Exception:

                current_app.logger.exception(
                    "Password reset OTP delivery failed"
                )

                get_db().otp_challenges.delete_one({
                    "challenge_id": challenge_id
                })

                # Do not return a different HTTP status for a known account.

    except PyMongoError:

        current_app.logger.exception(
            "Password reset OTP request failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": (
                "Password reset is temporarily unavailable."
            ),
        }), 503

    return _generic_reset_response(
        challenge_id
    )


# =========================================================
# PASSWORD RESET - VERIFY OTP
# =========================================================

@bp.post("/password-reset/verify-otp")
@limiter.limit("10 per 15 minutes")
def verify_password_reset_otp():

    data = _json_body()

    challenge_id = data.get("challenge_id")

    code = data.get("otp")

    if (
        not isinstance(challenge_id, str)
        or not challenge_id
        or len(challenge_id) > 128
    ):

        return jsonify({
            "error": "invalid_otp",
            "message": (
                "The code is invalid or expired. Request a new code."
            ),
        }), 400

    if not isinstance(code, str):
        code = ""

    try:

        challenge = get_db().otp_challenges.find_one({
            "challenge_id": challenge_id,
            "purpose": "password_reset",
        })

        if not challenge:

            return jsonify({
                "error": "invalid_otp",
                "message": (
                    "The code is invalid or expired. "
                    "Request a new code."
                ),
            }), 400

        consumed = _consume_otp_attempt(
            challenge,
            code,
            "password_reset",
            require_user=True,
        )

        if not consumed:

            return jsonify({
                "error": "invalid_otp",
                "message": (
                    "The verification code is invalid or expired."
                ),
            }), 400

        token = secrets.token_urlsafe(32)

        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        now = datetime.now(timezone.utc)

        get_db().reset_tokens.insert_one({
            "token_hash": token_hash,
            "user_id": consumed["user_id"],
            "expires_at": (
                now + timedelta(minutes=10)
            ),
            "created_at": now,
            "used": False,
            "purpose": "password_reset_otp",
        })

        return jsonify({
            "reset_token": token,
            "expires_in": 600,
        })

    except PyMongoError:

        current_app.logger.exception(
            "Password reset OTP verification failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": (
                "OTP verification is temporarily unavailable."
            ),
        }), 503


# =========================================================
# PASSWORD RESET - CONFIRM
# =========================================================

@bp.post("/password-reset/confirm")
@limiter.limit("5 per 15 minutes")
def confirm_password_reset():

    data = _json_body()

    token = (
        data.get("token")
        or data.get("reset_token")
        or ""
    )

    password = data.get("new_password") or ""

    if not isinstance(token, str) or not _valid_password(password):

        return jsonify({
            "error": "validation_error",
            "message": (
                "A valid reset token and a password "
                "with at least 10 characters are required."
            ),
        }), 400

    token = token.strip()

    if not token or len(token) > 512:

        return jsonify({
            "error": "invalid_reset_token",
            "message": (
                "The reset link is invalid or expired. "
                "Request a new link."
            ),
        }), 400

    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    now = datetime.now(timezone.utc)

    try:

        # Atomically claim the reset token.
        reset = get_db().reset_tokens.find_one_and_update(
            {
                "token_hash": token_hash,
                "purpose": {
                    "$in": [
                        "password_reset_link",
                        "password_reset_otp",
                    ]
                },
                "used": False,
                "expires_at": {
                    "$gt": now
                },
            },
            {
                "$set": {
                    "used": True,
                    "used_at": now,
                }
            },
            return_document=ReturnDocument.BEFORE,
        )

        if not reset:

            return jsonify({
                "error": "invalid_reset_token",
                "message": (
                    "The reset link is invalid, expired, "
                    "or has already been used. Request a new link."
                ),
            }), 400

        if not ObjectId.is_valid(reset.get("user_id", "")):

            current_app.logger.error(
                "Password reset token has an invalid user ID"
            )

            return jsonify({
                "error": "invalid_reset_token",
                "message": (
                    "The account associated with this reset link "
                    "could not be verified."
                ),
            }), 400

        result = get_db().users.update_one(
            {
                "_id": ObjectId(reset["user_id"]),
            },
            {
                "$set": {
                    "password_hash": _hasher.hash(password),
                    "updated_at": now,
                },
                "$inc": {
                    "session_version": 1,
                },
            },
        )

        if result.matched_count != 1:

            current_app.logger.error(
                "Password reset token referenced a missing user"
            )

            return jsonify({
                "error": "invalid_reset_token",
                "message": (
                    "The account associated with this reset link "
                    "no longer exists."
                ),
            }), 400

        # Invalidate remaining reset artifacts for this user.
        get_db().reset_tokens.delete_many({
            "user_id": reset["user_id"],
        })

        get_db().otp_challenges.delete_many({
            "user_id": reset["user_id"],
            "purpose": "password_reset",
        })

    except PyMongoError:

        current_app.logger.exception(
            "Password reset database operation failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": (
                "Password reset is temporarily unavailable."
            ),
        }), 503

    return jsonify({
        "message": (
            "Password updated. Sign in with your new password."
        ),
    })


# =========================================================
# SEND ACCOUNT VERIFICATION OTP
# =========================================================

@bp.post("/verification/send")
@login_required
@limiter.limit("3 per hour")
def send_verification_code():

    data = _json_body()

    channel = data.get("channel", "sms")

    if channel == "email":

        destination = g.user.get("email")

        already_verified = g.user.get(
            "email_verified",
            False,
        )

    elif channel == "sms":

        destination = g.user.get("phone")

        already_verified = g.user.get(
            "phone_verified",
            False,
        )

    else:

        return jsonify({
            "error": "validation_error",
            "message": "Channel must be email or sms.",
        }), 400

    if not destination:

        return jsonify({
            "error": "missing_destination",
            "message": (
                "Add an email address or phone number first."
            ),
        }), 400

    if already_verified:

        return jsonify({
            "message": "This contact is already verified."
        })

    try:

        challenge_id, code = _new_otp_challenge(
            user_id=g.user_id,
            channel=channel,
            destination=destination,
            purpose="account_verification",
        )

        try:

            _deliver(
                channel,
                destination,
                code,
                "verification",
            )

        except Exception:

            current_app.logger.exception(
                "Account verification OTP delivery failed"
            )

            get_db().otp_challenges.delete_one({
                "challenge_id": challenge_id
            })

            return jsonify({
                "error": "delivery_failed",
                "message": (
                    "Verification message could not be sent."
                ),
            }), 503

    except PyMongoError:

        current_app.logger.exception(
            "Account verification request failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": (
                "Account verification is temporarily unavailable."
            ),
        }), 503

    return jsonify({
        "challenge_id": challenge_id,
        "expires_in": 600,
    })


# =========================================================
# CONFIRM ACCOUNT VERIFICATION OTP
# =========================================================

@bp.post("/verification/confirm")
@login_required
@limiter.limit("10 per 15 minutes")
def confirm_verification_code():

    data = _json_body()

    challenge_id = data.get("challenge_id")

    code = data.get("otp")

    if (
        not isinstance(challenge_id, str)
        or not challenge_id
        or len(challenge_id) > 128
    ):

        return jsonify({
            "error": "invalid_otp",
            "message": (
                "The verification code is invalid or expired."
            ),
        }), 400

    if not isinstance(code, str):
        code = ""

    try:

        challenge = get_db().otp_challenges.find_one({
            "challenge_id": challenge_id,
            "user_id": g.user_id,
            "purpose": "account_verification",
        })

        if not challenge:

            return jsonify({
                "error": "invalid_otp",
                "message": (
                    "The verification code is invalid or expired."
                ),
            }), 400

        consumed = _consume_otp_attempt(
            challenge,
            code,
            "account_verification",
            require_user=True,
        )

        if not consumed:

            return jsonify({
                "error": "invalid_otp",
                "message": (
                    "The verification code is invalid or expired."
                ),
            }), 400

        field = (
            "email_verified"
            if consumed["channel"] == "email"
            else "phone_verified"
        )

        get_db().users.update_one(
            {
                "_id": g.user["_id"],
            },
            {
                "$set": {
                    field: True,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

    except PyMongoError:

        current_app.logger.exception(
            "Account verification confirmation failed"
        )

        return jsonify({
            "error": "service_unavailable",
            "message": (
                "Account verification is temporarily unavailable."
            ),
        }), 503

    return jsonify({
        "message": "Contact verified.",
    })
