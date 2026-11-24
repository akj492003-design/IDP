
from datetime import datetime, timedelta, timezone

from flask import current_app
from pymongo import ReturnDocument


# =========================================================
# QUOTA CONFIGURATION
# =========================================================

FREE_UPLOAD_LIMIT = 5
INTERMEDIATE_UPLOAD_LIMIT = 20

FREE_COOLDOWN = timedelta(hours=24)
SUBSCRIPTION_PERIOD = timedelta(days=30)


# =========================================================
# EXCEPTIONS
# =========================================================

class QuotaExceeded(Exception):

    def __init__(
        self,
        cooldown_until,
        message=(
            "Get a subscription for more uploads "
            "or wait 24 hours to upload again."
        ),
    ):
        self.cooldown_until = cooldown_until
        super().__init__(message)


# =========================================================
# DATE HELPERS
# =========================================================

def _utc(value):
    """Normalize datetime or ISO date strings to UTC."""

    if value is None:
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "Invalid ISO date value in quota data."
            ) from exc

    if not isinstance(value, datetime):
        raise ValueError(
            "Quota date must be a datetime or ISO string."
        )

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def _isoformat(value):
    """Return a consistent ISO-formatted UTC date."""

    normalized = _utc(value)

    return (
        normalized.isoformat()
        if normalized is not None
        else None
    )


def _safe_count(value):
    """Convert a stored usage counter into a nonnegative integer."""

    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# =========================================================
# SUBSCRIPTION HELPERS
# =========================================================

def _active_subscription(db, uid, now):
    """Find the latest currently active subscription."""

    return db.subscriptions.find_one(
        {
            "user_id": uid,
            "status": "active",
            "$or": [
                {"ends_at": None},
                {"ends_at": {"$gt": now}},
            ],
        },
        sort=[
            ("created_at", -1),
        ],
    )


def _subscription_period(db, sub, now):
    """
    Return the current 30-day subscription period start.

    Uses compare-and-update behavior to reduce concurrent
    period rollover conflicts.
    """

    raw_start = sub.get("period_started_at")

    start = _utc(
        raw_start
        or sub.get("created_at")
        or now
    )

    if now < start + SUBSCRIPTION_PERIOD:
        return start

    # Preserve the original 30-day billing cycle instead
    # of shifting it to the date of the first request.
    elapsed = now - start

    elapsed_periods = (
        elapsed // SUBSCRIPTION_PERIOD
    )

    new_start = (
        start
        + elapsed_periods * SUBSCRIPTION_PERIOD
    )

    if raw_start is None:

        period_filter = {
            "$or": [
                {"period_started_at": {"$exists": False}},
                {"period_started_at": None},
            ]
        }

    else:

        period_filter = {
            "period_started_at": raw_start
        }

    update_filter = {
        "_id": sub["_id"],
        **period_filter,
    }

    db.subscriptions.update_one(
        update_filter,
        {
            "$set": {
                "period_started_at": new_start
            }
        },
    )

    refreshed = db.subscriptions.find_one(
        {"_id": sub["_id"]}
    ) or sub

    return _utc(
        refreshed.get("period_started_at")
        or new_start
    )


# =========================================================
# BASIC PLAN QUOTA
# =========================================================

def _consume_basic_slot(db, uid, now):

    # Ensure one usage record per user.
    db.upload_usage.update_one(
        {"user_id": uid},
        {
            "$setOnInsert": {
                "user_id": uid,
                "used": 0,
                "created_at": now,
            }
        },
        upsert=True,
    )

    usage = db.upload_usage.find_one(
        {"user_id": uid}
    ) or {"used": 0}

    used = _safe_count(
        usage.get("used")
    )

    cooldown_until = _utc(
        usage.get("cooldown_until")
    )

    # Existing cooldown is still active.
    if cooldown_until and now < cooldown_until:

        raise QuotaExceeded(
            cooldown_until
        )

    # Reset an expired cooldown.
    if cooldown_until and now >= cooldown_until:

        db.upload_usage.update_one(
            {
                "user_id": uid,
                "cooldown_until": {
                    "$lte": now
                },
            },
            {
                "$set": {
                    "used": 0,
                    "updated_at": now,
                },
                "$unset": {
                    "cooldown_until": "",
                },
            },
        )

    # Atomically consume an available upload.
    updated = db.upload_usage.find_one_and_update(
        {
            "user_id": uid,
            "used": {
                "$lt": FREE_UPLOAD_LIMIT
            },
            "$or": [
                {
                    "cooldown_until": {
                        "$exists": False
                    }
                },
                {
                    "cooldown_until": None
                    }
            ],
        },
        {
            "$inc": {
                "used": 1
            },
            "$set": {
                "updated_at": now
            },
        },
        return_document=ReturnDocument.AFTER,
    )

    # No available upload slot.
    if not updated:

        latest = db.upload_usage.find_one(
            {"user_id": uid}
        ) or {}

        latest_used = _safe_count(
            latest.get("used")
        )

        until = _utc(
            latest.get("cooldown_until")
        )

        if (
            latest_used >= FREE_UPLOAD_LIMIT
            and until is None
        ):

            until = now + FREE_COOLDOWN

            db.upload_usage.update_one(
                {
                    "user_id": uid,
                    "used": {
                        "$gte": FREE_UPLOAD_LIMIT
                    },
                    "$or": [
                        {
                            "cooldown_until": {
                                "$exists": False
                            }
                        },
                        {
                            "cooldown_until": None
                        },
                    ],
                },
                {
                    "$set": {
                        "cooldown_until": until
                    }
                },
            )

        if until is None:
            until = now + FREE_COOLDOWN

        raise QuotaExceeded(until)

    used = _safe_count(
        updated.get("used")
    )

    # The fifth accepted upload starts the 24-hour cooldown.
    if used >= FREE_UPLOAD_LIMIT:

        cooldown_until = now + FREE_COOLDOWN

        db.upload_usage.update_one(
            {
                "user_id": uid,
                "used": {
                    "$gte": FREE_UPLOAD_LIMIT
                },
                "$or": [
                    {
                        "cooldown_until": {
                            "$exists": False
                        }
                    },
                    {
                        "cooldown_until": None
                    },
                ],
            },
            {
                "$set": {
                    "cooldown_until": cooldown_until
                }
            },
        )

    else:
        cooldown_until = None

    return {
        "plan": "basic",
        "limit": FREE_UPLOAD_LIMIT,
        "used": used,
        "remaining": max(
            0,
            FREE_UPLOAD_LIMIT - used
        ),
        "cooldown_until": _isoformat(
            cooldown_until
        ),
        "locked": used >= FREE_UPLOAD_LIMIT,
    }


# =========================================================
# INTERMEDIATE PLAN QUOTA
# =========================================================

def _consume_intermediate_slot(db, uid, sub, now):

    start = _subscription_period(
        db,
        sub,
        now
    )

    # Ensure a usage counter exists.
    db.subscription_usage.update_one(
        {
            "user_id": uid
        },
        {
            "$setOnInsert": {
                "user_id": uid,
                "period_started_at": start,
                "used": 0,
            }
        },
        upsert=True,
    )

    counter = db.subscription_usage.find_one(
        {"user_id": uid}
    ) or {
        "used": 0,
        "period_started_at": start,
    }

    counter_start = _utc(
        counter.get("period_started_at")
    )

    # Reset usage only when the billing period changes.
    if counter_start != start:

        previous_start = counter.get(
            "period_started_at"
        )

        db.subscription_usage.update_one(
            {
                "user_id": uid,
                "period_started_at": previous_start,
            },
            {
                "$set": {
                    "period_started_at": start,
                    "used": 0,
                    "updated_at": now,
                }
            },
        )

    # Atomically consume one upload in this period.
    updated = db.subscription_usage.find_one_and_update(
        {
            "user_id": uid,
            "period_started_at": start,
            "used": {
                "$lt": INTERMEDIATE_UPLOAD_LIMIT
            },
        },
        {
            "$inc": {
                "used": 1
            },
            "$set": {
                "updated_at": now
            },
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated:

        latest = db.subscription_usage.find_one(
            {"user_id": uid}
        ) or {}

        latest_start = _utc(
            latest.get("period_started_at")
        )

        latest_used = _safe_count(
            latest.get("used")
        )

        if (
            latest_start == start
            and latest_used >= INTERMEDIATE_UPLOAD_LIMIT
        ):

            raise QuotaExceeded(
                start + SUBSCRIPTION_PERIOD,
                (
                    "The Intermediate plan has reached its "
                    "20-upload billing-period limit. "
                    "Upgrade your plan or wait until "
                    "the next billing period."
                ),
            )

        raise RuntimeError(
            "Could not reserve an Intermediate upload slot. "
            "Please retry the upload."
        )

    used = _safe_count(
        updated.get("used")
    )

    return {
        "plan": "intermediate",
        "limit": INTERMEDIATE_UPLOAD_LIMIT,
        "used": used,
        "remaining": max(
            0,
            INTERMEDIATE_UPLOAD_LIMIT - used
        ),
        "cooldown_until": None,
        "locked": used >= INTERMEDIATE_UPLOAD_LIMIT,

        # Internal reservation metadata.
        # Used to prevent rollback affecting a newer period.
        "reservation_period_started_at": start.isoformat(),
    }


# =========================================================
# CONSUME UPLOAD SLOT
# =========================================================

def consume_upload_slot(uid):
    """
    Atomically consume one upload slot according to plan.

    Basic:
        5 uploads, then 24-hour cooldown.

    Intermediate:
        20 uploads per 30-day period.

    Unlimited:
        No upload limit.
    """

    db = current_app.extensions["mongo_db"]

    now = datetime.now(timezone.utc)

    sub = _active_subscription(
        db,
        uid,
        now
    )

    plan = (
        str(sub.get("plan_id") or "basic").lower()
        if sub
        else "basic"
    )

    # Unlimited plan.
    if plan == "unlimited":

        return {
            "plan": "unlimited",
            "limit": None,
            "used": None,
            "remaining": None,
            "cooldown_until": None,
            "locked": False,
        }

    # Intermediate plan.
    if plan == "intermediate":

        return _consume_intermediate_slot(
            db,
            uid,
            sub,
            now
        )

    # Unknown subscription plans do not receive
    # unlimited access. They use Basic restrictions.
    if plan not in {"basic", "intermediate", "unlimited"}:

        current_app.logger.warning(
            "Unknown subscription plan %r for user %s; "
            "applying Basic upload restrictions.",
            plan,
            uid,
        )

    return _consume_basic_slot(
        db,
        uid,
        now
    )


# =========================================================
# RELEASE UPLOAD SLOT
# =========================================================

def release_upload_slot(uid, reservation):
    """
    Roll back a consumed upload slot after upload failure.

    Intermediate rollback is restricted to the exact
    billing period in which the reservation was made.
    """

    if not isinstance(reservation, dict):
        return

    plan = str(
        reservation.get("plan") or ""
    ).lower()

    db = current_app.extensions["mongo_db"]

    now = datetime.now(timezone.utc)

    if plan == "basic":

        db.upload_usage.update_one(
            {
                "user_id": uid,
                "used": {
                    "$gt": 0
                },
            },
            {
                "$inc": {
                    "used": -1
                },
                "$set": {
                    "updated_at": now
                },
                "$unset": {
                    "cooldown_until": ""
                },
            },
        )

        return

    if plan == "intermediate":

        raw_start = reservation.get(
            "reservation_period_started_at"
        )

        if not raw_start:
            current_app.logger.error(
                "Cannot safely release Intermediate quota: "
                "reservation period is missing for user %s.",
                uid,
            )
            return

        period_start = _utc(raw_start)

        db.subscription_usage.update_one(
            {
                "user_id": uid,
                "period_started_at": period_start,
                "used": {
                    "$gt": 0
                },
            },
            {
                "$inc": {
                    "used": -1
                },
                "$set": {
                    "updated_at": now
                },
            },
        )


# =========================================================
# CURRENT QUOTA
# =========================================================

def current_quota(uid):
    """Return the user's current upload entitlement and usage."""

    db = current_app.extensions["mongo_db"]

    now = datetime.now(timezone.utc)

    sub = _active_subscription(
        db,
        uid,
        now
    )

    plan = (
        str(sub.get("plan_id") or "basic").lower()
        if sub
        else "basic"
    )

    # Unlimited plan.
    if plan == "unlimited":

        return {
            "plan": "unlimited",
            "used": None,
            "limit": None,
            "remaining": None,
            "locked": False,
            "cooldown_until": None,
        }

    # Intermediate plan.
    if plan == "intermediate":

        start = _subscription_period(
            db,
            sub,
            now
        )

        counter = db.subscription_usage.find_one(
            {"user_id": uid}
        ) or {}

        counter_start = _utc(
            counter.get("period_started_at")
        )

        if counter_start != start:
            used = 0
        else:
            used = _safe_count(
                counter.get("used")
            )

        return {
            "plan": "intermediate",
            "used": used,
            "limit": INTERMEDIATE_UPLOAD_LIMIT,
            "remaining": max(
                0,
                INTERMEDIATE_UPLOAD_LIMIT - used
            ),
            "locked": used >= INTERMEDIATE_UPLOAD_LIMIT,
            "cooldown_until": None,
        }

    # Unknown plans receive Basic restrictions.
    if plan not in {"basic", "intermediate", "unlimited"}:

        current_app.logger.warning(
            "Unknown subscription plan %r for user %s; "
            "showing Basic quota.",
            plan,
            uid,
        )

    usage = db.upload_usage.find_one(
        {"user_id": uid}
    ) or {
        "used": 0
    }

    until = _utc(
        usage.get("cooldown_until")
    )

    # Reset expired cooldown.
    if until and now >= until:

        db.upload_usage.update_one(
            {
                "user_id": uid,
                "cooldown_until": {
                    "$lte": now
                },
            },
            {
                "$set": {
                    "used": 0,
                    "updated_at": now,
                },
                "$unset": {
                    "cooldown_until": ""
                },
            },
        )

        usage = db.upload_usage.find_one(
            {"user_id": uid}
        ) or {
            "used": 0
        }

        until = _utc(
            usage.get("cooldown_until")
        )

    used = _safe_count(
        usage.get("used")
    )

    # Repair old records that reached the upload limit
    # but do not contain a cooldown timestamp.
    if used >= FREE_UPLOAD_LIMIT and until is None:

        until = now + FREE_COOLDOWN

        db.upload_usage.update_one(
            {
                "user_id": uid,
                "used": {
                    "$gte": FREE_UPLOAD_LIMIT
                },
                "$or": [
                    {
                        "cooldown_until": {
                            "$exists": False
                        }
                    },
                    {
                        "cooldown_until": None
                    },
                ],
            },
            {
                "$set": {
                    "cooldown_until": until
                }
            },
        )

    locked = bool(
        until and now < until
    )

    return {
        "plan": "basic",
        "used": used,
        "limit": FREE_UPLOAD_LIMIT,
        "remaining": max(
            0,
            FREE_UPLOAD_LIMIT - used
        ),
        "locked": locked,
        "cooldown_until": (
            _isoformat(until)
            if locked
            else None
        ),
    }
