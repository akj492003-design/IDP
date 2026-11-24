
from functools import wraps

from flask import (
    current_app,
    jsonify,
    session,
    g,
)

from bson import ObjectId
from pymongo.errors import PyMongoError


# =========================================================
# DATABASE ACCESS
# =========================================================

def get_db():
    """Return the MongoDB database registered by create_app()."""

    db = current_app.extensions.get("mongo_db")

    if db is None:
        raise RuntimeError(
            "MongoDB has not been initialized."
        )

    return db


# =========================================================
# SESSION USER IDENTIFICATION
# =========================================================

def user_id():
    """
    Retrieve and validate the authenticated user's session ID.

    Returns a normalized ObjectId string or None.
    """

    value = session.get("user_id")

    if isinstance(value, ObjectId):
        return str(value)

    if not isinstance(value, str):
        return None

    if not ObjectId.is_valid(value):
        return None

    return str(ObjectId(value))


# =========================================================
# AUTHENTICATION DECORATOR
# =========================================================

def login_required(view):
    """Protect routes that require an authenticated user."""

    @wraps(view)
    def wrapped(*args, **kwargs):

        uid = user_id()

        # -------------------------------------------------
        # CHECK SESSION
        # -------------------------------------------------

        if uid is None:

            # Remove malformed or stale session identifiers.
            if session.get("user_id") is not None:
                session.clear()

            return jsonify({
                "error": "unauthorized",
                "message": "Please sign in to continue.",
            }), 401

        # -------------------------------------------------
        # VERIFY DATABASE USER
        # -------------------------------------------------

        try:

            user = get_db().users.find_one({
                "_id": ObjectId(uid)
            })

        except PyMongoError:

            current_app.logger.exception(
                "MongoDB authentication lookup failed"
            )

            return jsonify({
                "error": "service_unavailable",
                "message": (
                    "Authentication is temporarily unavailable. "
                    "Please try again."
                ),
            }), 503

        # -------------------------------------------------
        # VERIFY USER STATUS
        # -------------------------------------------------

        if not user or user.get("status", "active") != "active":

            session.clear()

            return jsonify({
                "error": "unauthorized",
                "message": (
                    "Your session is no longer valid. "
                    "Please sign in again."
                ),
            }), 401

        # -------------------------------------------------
        # SESSION VERSION VALIDATION
        # -------------------------------------------------

        stored_version = user.get("session_version", 0)
        active_version = session.get("session_version", 0)

        versions_valid = (
            isinstance(stored_version, int)
            and not isinstance(stored_version, bool)
            and stored_version >= 0
            and isinstance(active_version, int)
            and not isinstance(active_version, bool)
            and active_version >= 0
            and stored_version == active_version
        )

        if not versions_valid:
            session.clear()
            return jsonify({
                "error": "unauthorized",
                "message": (
                    "Your session is no longer valid. "
                    "Please sign in again."
                ),
            }), 401

        # -------------------------------------------------
        # ATTACH AUTHENTICATED USER TO REQUEST
        # -------------------------------------------------

        g.user = user
        g.user_id = uid

        return view(*args, **kwargs)

    return wrapped


# =========================================================
# SAFE USER SERIALIZATION
# =========================================================

def safe_user(user):
    """
    Return public user information.

    Never expose password hashes, authentication tokens,
    OTP data or internal database credentials.
    """

    return {
        "id": str(user["_id"]),
        "full_name": user.get("full_name", ""),
        "name": user.get("full_name", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "photo_url": user.get("photo_url"),
        "role": user.get("role", "user"),
    }
