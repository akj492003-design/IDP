import hashlib
import hmac
import uuid

from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request
from pymongo.errors import DuplicateKeyError

from app.security import get_db, login_required
from app.blueprints.subscriptions import PLANS


bp = Blueprint("payments", __name__)


def _now():
    return datetime.now(timezone.utc)


def _get_plan(plan_id):
    return next(
        (
            plan for plan in PLANS
            if plan.get("id") == plan_id and plan_id != "basic"
        ),
        None,
    )


def _amount(plan, cycle):
    if cycle not in ("monthly", "yearly"):
        raise ValueError("Invalid billing cycle.")

    amount = int(
        plan["monthly_price"]
        if cycle == "monthly"
        else plan["yearly_price"]
    )

    if amount <= 0:
        raise ValueError("Invalid payment amount.")

    return amount


def _client():
    key_id = current_app.config.get("RAZORPAY_KEY_ID")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET")

    if not key_id or not key_secret:
        return None

    import razorpay

    return razorpay.Client(
        auth=(key_id, key_secret)
    )


def _ensure_indexes(db):
    db.payments.create_index(
        [("order_id", 1)],
        unique=True,
        name="uniq_razorpay_order",
    )

    db.subscriptions.create_index(
        [("payment_id", 1)],
        unique=True,
        partialFilterExpression={
            "payment_id": {"$type": "string"}
        },
        name="uniq_razorpay_subscription_payment",
    )


def _subscription_dates(record):
    started = (
        record.get("subscription_started_at")
        or record.get("paid_at")
        or _now()
    )

    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)

    ended = record.get("subscription_ends_at")

    if ended is None:
        days = 365 if record.get("cycle") == "yearly" else 30
        ended = started + timedelta(days=days)

    return started, ended


def _activate_subscription(record, payment_id):
    db = get_db()

    record = db.payments.find_one({
        "_id": record["_id"],
        "status": "paid",
        "payment_id": payment_id,
    })

    if not record:
        raise RuntimeError("Verified payment record not found.")

    _ensure_indexes(db)

    started, ended = _subscription_dates(record)

    subscription = {
        "user_id": record["user_id"],
        "plan_id": record["plan_id"],
        "cycle": record["cycle"],
        "status": "active",
        "created_at": started,
        "period_started_at": started,
        "ends_at": ended,
        "payment_id": payment_id,
        "provider": "razorpay",
    }

    try:
        db.subscriptions.update_one(
            {"payment_id": payment_id},
            {"$setOnInsert": subscription},
            upsert=True,
        )
    except DuplicateKeyError:
        # A concurrent callback/webhook already created this subscription.
        pass

    active = db.subscriptions.find_one({
        "payment_id": payment_id,
        "user_id": record["user_id"],
    })

    if not active:
        raise RuntimeError("Subscription creation could not be confirmed.")

    # Do not reactivate an older subscription superseded by a newer payment.
    if active.get("status") == "superseded":
        return active

    # Supersede older active subscriptions without extending this payment.
    db.subscriptions.update_many(
        {
            "user_id": record["user_id"],
            "status": "active",
            "payment_id": {"$ne": payment_id},
        },
        {
            "$set": {
                "status": "superseded",
                "updated_at": _now(),
            }
        },
    )

    db.subscriptions.update_one(
        {"payment_id": payment_id},
        {
            "$set": {
                "status": "active",
                "ends_at": ended,
                "period_started_at": started,
            }
        },
    )

    db.payments.update_one(
        {"_id": record["_id"], "payment_id": payment_id},
        {
            "$set": {
                "activation_status": "active",
                "activated_at": _now(),
            }
        },
    )

    return db.subscriptions.find_one({
        "payment_id": payment_id
    })


def _save_captured_payment(record, entity):
    db = get_db()

    payment_id = entity.get("id")

    if (
        not payment_id
        or entity.get("order_id") != record.get("order_id")
    ):
        return None, 400

    try:
        actual_amount = int(entity.get("amount", -1))
    except (TypeError, ValueError):
        actual_amount = -1

    expected_amount = int(record["amount"]) * 100

    if (
        actual_amount != expected_amount
        or entity.get("currency") != record.get("currency", "INR")
    ):
        return None, 400

    if (
        entity.get("status") != "captured"
        and entity.get("captured") is not True
    ):
        return None, 409

    started = record.get("subscription_started_at") or _now()

    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)

    days = 365 if record["cycle"] == "yearly" else 30
    ended = record.get("subscription_ends_at") or (
        started + timedelta(days=days)
    )

    # Atomic claim: an order cannot be linked to two different payments.
    db.payments.update_one(
        {
            "_id": record["_id"],
            "status": {
                "$in": [
                    "created",
                    "authorized",
                    "verification_failed",
                    "failed",
                    "paid",
                ]
            },
            "$or": [
                {"payment_id": {"$exists": False}},
                {"payment_id": None},
                {"payment_id": payment_id},
            ],
        },
        {
            "$set": {
                "status": "paid",
                "payment_id": payment_id,
                "payment_method": entity.get("method"),
                "paid_at": record.get("paid_at") or _now(),
                "subscription_started_at": started,
                "subscription_ends_at": ended,
                "updated_at": _now(),
            }
        },
    )

    refreshed = db.payments.find_one({
        "_id": record["_id"],
        "status": "paid",
        "payment_id": payment_id,
    })

    if not refreshed:
        return None, 409

    subscription = _activate_subscription(
        refreshed,
        payment_id,
    )

    return subscription, None


def _subscription_response(subscription):
    return {
        "plan_id": subscription["plan_id"],
        "cycle": subscription["cycle"],
        "status": subscription["status"],
        "ends_at": subscription["ends_at"].isoformat(),
    }


# --------------------------------------------------
# CREATE ORDER
# --------------------------------------------------

@bp.post("/order")
@login_required
def create_order():
    data = request.get_json(silent=True) or {}

    plan = _get_plan(data.get("plan_id"))

    if not plan:
        return jsonify({
            "error": "invalid_plan",
            "message": "Select a paid subscription plan.",
        }), 400

    cycle = data.get("cycle", "monthly")

    try:
        amount = _amount(plan, cycle)
    except (ValueError, TypeError) as exc:
        return jsonify({
            "error": "validation_error",
            "message": str(exc),
        }), 400

    try:
        client = _client()
    except ImportError:
        current_app.logger.exception("Razorpay SDK missing")
        return jsonify({
            "error": "payment_sdk_missing",
            "message": "Razorpay SDK is unavailable.",
        }), 503

    if client is None:
        return jsonify({
            "error": "payment_not_configured",
            "message": "Configure Razorpay Test Mode credentials.",
        }), 503

    db = get_db()

    try:
        _ensure_indexes(db)

        receipt = "idp_" + uuid.uuid4().hex[:24]

        order = client.order.create({
            "amount": amount * 100,
            "currency": "INR",
            "receipt": receipt,
            "notes": {
                "user_id": g.user_id,
                "plan_id": plan["id"],
                "cycle": cycle,
            },
        })

        if (
            not order.get("id")
            or int(order.get("amount", -1)) != amount * 100
            or order.get("currency") != "INR"
        ):
            raise RuntimeError("Razorpay order mismatch.")

        db.payments.insert_one({
            "user_id": g.user_id,
            "provider": "razorpay",
            "order_id": order["id"],
            "receipt": receipt,
            "plan_id": plan["id"],
            "cycle": cycle,
            "amount": amount,
            "currency": "INR",
            "status": "created",
            "activation_status": "pending",
            "created_at": _now(),
        })

    except Exception:
        current_app.logger.exception(
            "Razorpay order creation failed"
        )
        return jsonify({
            "error": "payment_provider_error",
            "message": "Could not create payment order.",
        }), 502

    return jsonify({
        "order_id": order["id"],
        "amount": amount * 100,
        "currency": "INR",
        "key_id": current_app.config["RAZORPAY_KEY_ID"],
        "plan": plan,
    })


# --------------------------------------------------
# VERIFY PAYMENT
# --------------------------------------------------

@bp.post("/verify")
@login_required
def verify_payment():
    data = request.get_json(silent=True) or {}

    order_id = data.get("razorpay_order_id")
    payment_id = data.get("razorpay_payment_id")
    signature = data.get("razorpay_signature")

    if not all(
        isinstance(value, str) and value.strip()
        for value in (order_id, payment_id, signature)
    ):
        return jsonify({
            "error": "invalid_request",
            "message": "Payment verification details are incomplete.",
        }), 400

    db = get_db()

    record = db.payments.find_one({
        "user_id": g.user_id,
        "order_id": order_id,
    })

    if not record:
        return jsonify({
            "error": "invalid_payment",
            "message": "Payment order was not found.",
        }), 404

    secret = current_app.config.get("RAZORPAY_KEY_SECRET")

    if not secret:
        return jsonify({
            "error": "payment_not_configured",
            "message": "Razorpay verification is not configured.",
        }), 503

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return jsonify({
            "error": "invalid_signature",
            "message": "Payment signature verification failed.",
        }), 400

    # A previously paid record can recover a missed subscription activation.
    if (
        record.get("status") == "paid"
        and record.get("payment_id") == payment_id
    ):
        try:
            subscription = _activate_subscription(
                record,
                payment_id,
            )
            return jsonify({
                "message": "Payment already verified.",
                "subscription": _subscription_response(subscription),
            })
        except Exception:
            current_app.logger.exception(
                "Subscription activation recovery failed"
            )
            return jsonify({
                "error": "activation_pending",
                "message": "Payment is verified; activation recovery is pending.",
            }), 503

    try:
        client = _client()

        if client is None:
            return jsonify({
                "error": "payment_not_configured",
                "message": "Razorpay credentials are unavailable.",
            }), 503

        # Ask Razorpay for the authoritative payment status.
        entity = client.payment.fetch(payment_id)

    except Exception:
        current_app.logger.exception(
            "Razorpay payment status fetch failed"
        )
        return jsonify({
            "error": "payment_status_unavailable",
            "message": "Could not confirm payment with Razorpay. Retry shortly.",
        }), 502

    if entity.get("order_id") != order_id:
        return jsonify({
            "error": "payment_mismatch",
            "message": "Payment does not belong to this order.",
        }), 400

    try:
        actual_amount = int(entity.get("amount", -1))
    except (TypeError, ValueError):
        actual_amount = -1

    if (
        actual_amount != int(record["amount"]) * 100
        or entity.get("currency") != record.get("currency", "INR")
    ):
        return jsonify({
            "error": "payment_mismatch",
            "message": "Payment amount or currency does not match the order.",
        }), 400

    if (
        entity.get("status") != "captured"
        and entity.get("captured") is not True
    ):
        return jsonify({
            "error": "payment_not_captured",
            "message": "Payment has not been captured yet.",
        }), 409

    try:
        subscription, error_status = _save_captured_payment(
            record,
            entity,
        )

        if error_status:
            return jsonify({
                "error": "payment_reconciliation_failed",
                "message": "Captured payment could not be reconciled with the order.",
            }), error_status

    except Exception:
        current_app.logger.exception(
            "Subscription activation failed"
        )
        return jsonify({
            "error": "activation_pending",
            "message": "Payment is captured; subscription activation is pending. Retry shortly.",
        }), 503

    return jsonify({
        "message": "Payment verified and subscription activated.",
        "subscription": _subscription_response(subscription),
    })


# --------------------------------------------------
# RAZORPAY WEBHOOK
# --------------------------------------------------

@bp.post("/webhook")
def razorpay_webhook():
    secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET")

    if not secret:
        return jsonify({
            "error": "webhook_not_configured"
        }), 503

    body = request.get_data(cache=True)

    supplied = request.headers.get(
        "X-Razorpay-Signature",
        "",
    )

    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not supplied or not hmac.compare_digest(supplied, expected):
        return jsonify({
            "error": "invalid_signature"
        }), 400

    event_id = request.headers.get(
        "X-Razorpay-Event-Id",
        "",
    ).strip()

    if not event_id or len(event_id) > 255:
        return jsonify({
            "error": "missing_event_id"
        }), 400

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({
            "error": "invalid_payload"
        }), 400

    db = get_db()
    now = _now()

    try:
        db.razorpay_webhook_events.insert_one({
            "_id": event_id,
            "event": payload.get("event"),
            "status": "received",
            "received_at": now,
            "attempts": 1,
        })

    except DuplicateKeyError:
        previous = db.razorpay_webhook_events.find_one({
            "_id": event_id
        }) or {}

        if previous.get("status") == "processed":
            return jsonify({
                "received": True,
                "duplicate": True,
            }), 200

        db.razorpay_webhook_events.update_one(
            {"_id": event_id},
            {
                "$set": {"status": "received"},
                "$inc": {"attempts": 1},
            },
        )

    try:
        event_type = payload.get("event")

        if event_type == "payment.captured":
            entity = (
                payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
            )

            order_id = entity.get("order_id")

            record = (
                db.payments.find_one({"order_id": order_id})
                if order_id
                else None
            )

            if record:
                subscription, error_status = _save_captured_payment(
                    record,
                    entity,
                )

                if error_status:
                    db.razorpay_webhook_events.update_one(
                        {"_id": event_id},
                        {"$set": {"status": "failed"}},
                    )
                    return jsonify({
                        "error": "payment_reconciliation_failed"
                    }), error_status

            else:
                current_app.logger.warning(
                    "Webhook references unknown IDP order: %s",
                    order_id,
                )

        elif event_type == "payment.failed":
            entity = (
                payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
            )

            order_id = entity.get("order_id")

            if order_id:
                # A failed attempt does not necessarily invalidate the order.
                db.payments.update_one(
                    {
                        "order_id": order_id,
                        "status": {"$in": ["created", "failed"]},
                    },
                    {
                        "$set": {
                            "last_payment_failure": {
                                "payment_id": entity.get("id"),
                                "error_code": entity.get("error_code"),
                                "error_description": entity.get("error_description"),
                                "recorded_at": _now(),
                            },
                            "updated_at": _now(),
                        }
                    },
                )

        db.razorpay_webhook_events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "status": "processed",
                    "processed_at": _now(),
                }
            },
        )

        return jsonify({
            "received": True
        }), 200

    except Exception:
        current_app.logger.exception(
            "Razorpay webhook processing failed"
        )

        db.razorpay_webhook_events.update_one(
            {"_id": event_id},
            {
                "$set": {
                    "status": "failed",
                    "updated_at": _now(),
                }
            },
        )

        return jsonify({
            "error": "webhook_processing_failed"
        }), 500

