import json
import logging
import math
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median

from flask import Blueprint, current_app, jsonify, request, g

from app.security import login_required, get_db
from app.date_range import (
    parse_date_range_args,
    processed_or_created_date_clause,
)


bp = Blueprint("classification", __name__)
logger = logging.getLogger(__name__)

KNOWN_LABELS = ("form", "invoice", "resume")
ALLOWED_FILTERS = {"all", *KNOWN_LABELS, "unclassified"}
RESULT_LIMIT = 500
DEPLOYED_MODEL = "EfficientNetB0"


def _normalise_label(value):
    if not isinstance(value, str):
        return "unclassified"
    value = value.strip().lower()
    return value or "unclassified"


def _isoformat(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    method = getattr(value, "isoformat", None)
    if callable(method):
        try:
            return method()
        except (TypeError, ValueError):
            return None
    return None


def _extract_confidence(classification, expected_label=None):
    """Return visual confidence only when the visual class agrees with routing."""
    if not isinstance(classification, dict):
        return None

    if classification.get("classification_conflict"):
        return None

    if classification.get("score_type") == "hybrid_decision":
        return None

    scores = classification.get("scores")
    candidates = scores if isinstance(scores, list) else [scores]

    for item in candidates:
        if not isinstance(item, dict):
            continue

        if expected_label is not None:
            score_label = str(item.get("label", "")).strip().casefold()
            if score_label != str(expected_label).strip().casefold():
                continue

        value = item.get("score")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return float(value)

    return None


def _build_filter(base_query, filter_type):
    query = dict(base_query)
    if filter_type == "all":
        return query
    if filter_type == "unclassified":
        # $nin also includes documents where the field is absent or null.
        query["result.classification.label"] = {"$nin": list(KNOWN_LABELS)}
        return query
    query["result.classification.label"] = filter_type
    return query


def _logical_rows_for_document(doc):
    """Expand a parent upload into its individually classified documents.

    Multi-document uploads use result.documents[].classification.
    Single-document uploads retain result.classification.
    """
    result = doc.get("result")
    if not isinstance(result, dict):
        result = {}

    raw_segments = result.get("documents")
    segments = (
        [item for item in raw_segments if isinstance(item, dict)]
        if isinstance(raw_segments, list)
        else []
    )

    if segments:
        entries = [
            (segment.get("classification"), segment.get("insights"), segment, index)
            for index, segment in enumerate(segments)
        ]
    else:
        entries = [
            (
                result.get("classification"),
                result.get("insights"),
                None,
                0,
            )
        ]

    file_name = str(doc.get("file_name") or "Document")
    extension = str(doc.get("extension") or "").upper().lstrip(".")
    document_id = str(doc.get("_id", ""))

    rows = []

    for classification, insights, segment, position in entries:
        if not isinstance(classification, dict):
            classification = {}
        if not isinstance(insights, dict):
            insights = {}

        category = _normalise_label(classification.get("label"))
        confidence = _extract_confidence(classification, category)
        stored_result = doc.get("result")
        if not isinstance(stored_result, dict):
            stored_result = {}

        verification = (
            segment.get("classification_verification")
            if isinstance(segment, dict)
            else stored_result.get("classification_verification")
        )

        if not isinstance(verification, dict):
            verification = {}

        verification_verified = verification.get("verified") is True

        confidence_review = insights.get("confidence_review")
        if not isinstance(confidence_review, dict):
            confidence_review = {}

        review_required = insights.get("review_required")
        if not isinstance(review_required, bool):
            review_required = (
                confidence is None
                or confidence < 0.70
                or category == "unclassified"
            )

        boundary_review = (
            bool(segment.get("boundary_review_required"))
            if isinstance(segment, dict) else False
        )

        review_required = (
            review_required
            or bool(classification.get("classification_review_required"))
            or bool(classification.get("review_required"))
            or boundary_review
        )

        review_reasons = []

        for reason_list in (
            insights.get("review_reasons"),
            classification.get("classification_review_reasons"),
            segment.get("boundary_warnings") if isinstance(segment, dict) else None,
        ):
            if isinstance(reason_list, list):
                for reason in reason_list:
                    if reason and reason not in review_reasons:
                        review_reasons.append(str(reason))

        rejected = bool(
            confidence_review.get("prediction_rejected")
            or (
                confidence_review.get("below_threshold")
                and confidence_review.get("action") == "Reject prediction"
            )
        )

        segment_id = (
            segment.get("segment_id")
            if isinstance(segment, dict) else None
        ) or f"{document_id}:part:{position + 1}"

        display_name = file_name
        if len(entries) > 1:
            display_name = f"{file_name} - Part {position + 1}"

        rows.append({
            "id": document_id,
            "segment_id": str(segment_id),
            "document_index": (
                segment.get("document_index", position)
                if isinstance(segment, dict) else None
            ),
            "name": file_name,
            "display_name": display_name,
            "type": extension,
            "category": category,
            "model": classification.get("model") or "EfficientNetB0",
            "status": doc.get("status"),
            "created_at": _isoformat(
                doc.get("processed_at") or doc.get("created_at")
            ),
            "confidence": confidence,
            "classification_verification": {
                "verified": verification_verified,
                "status": "verified" if verification_verified else "pending",
                "confirmed_class": verification.get("confirmed_class"),
                "verified_at": _isoformat(verification.get("verified_at")),
            },
            "review_required": review_required,
            "prediction_rejected": rejected,
            "review_reasons": review_reasons,
            "page_range": (
                segment.get("page_range")
                if isinstance(segment, dict) else None
            ),
        })

    return rows


@bp.get("")
@login_required
def classification_summary():
    db = get_db()

    base_query = {
        "user_id": g.user_id,
        "status": "completed",
    }

    date_range, date_error = parse_date_range_args(request.args)
    if date_error:
        return jsonify({
            "error": "invalid_date_range",
            "message": date_error,
        }), 400

    date_clause = processed_or_created_date_clause(date_range)
    stats_query = (
        {"$and": [base_query, date_clause]}
        if date_clause else base_query
    )

    filter_type = (request.args.get("type") or "all").strip().lower()
    if filter_type not in ALLOWED_FILTERS:
        return jsonify({
            "error": "invalid_filter",
            "message": "Choose all, form, invoice, resume or unclassified.",
        }), 400

    requested_limit = request.args.get("limit", 100, type=int)
    result_limit = min(RESULT_LIMIT, max(1, requested_limit or 100))
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * result_limit

    counts = {label: 0 for label in KNOWN_LABELS}
    total = 0
    unclassified_total = 0
    review_total = 0
    result_total = 0
    rows = []

    # Aggregate logical documents rather than counting parent upload files.
    # Only the requested result page is retained in memory.
    cursor = db.documents.find(stats_query).sort([
        ("processed_at", -1),
        ("created_at", -1),
    ])

    for doc in cursor:
        for row in _logical_rows_for_document(doc):
            total += 1
            category = row["category"]

            if category in counts:
                counts[category] += 1
            else:
                unclassified_total += 1

            if row["review_required"]:
                review_total += 1

            matches_filter = (
                filter_type == "all"
                or (filter_type == "unclassified" and category not in KNOWN_LABELS)
                or category == filter_type
            )

            if not matches_filter:
                continue

            if offset <= result_total < offset + result_limit:
                rows.append(row)

            result_total += 1

    classified_total = sum(counts.values())
    active_categories = sum(1 for value in counts.values() if value > 0)

    return jsonify({
        "stats": {
            "total": total,
            "form": counts["form"],
            "invoice": counts["invoice"],
            "resume": counts["resume"],
            "classified": classified_total,
            "unclassified": unclassified_total,
            "review_required": review_total,
            "categories_active": active_categories,
        },
        "categories": counts,
        "results": rows,
        "returned": len(rows),
        "limit": result_limit,
        "filter": filter_type,
        "pagination": {
            "page": page,
            "limit": result_limit,
            "total": result_total,
            "pages": (result_total + result_limit - 1) // result_limit,
        },
        "date_filter_applied": bool(date_range),
    })


@bp.get("/models")
@login_required
def model_performance():
    data_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "model_metrics.json"
    )

    if not data_path.is_file():
        return jsonify({
            "error": "metrics_missing",
            "message": "Saved model evaluation metrics are missing.",
        }), 503

    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Unable to read saved model evaluation metrics.")
        return jsonify({
            "error": "metrics_unreadable",
            "message": "Saved model evaluation results could not be read.",
        }), 503

    if not isinstance(payload, dict):
        return jsonify({
            "error": "metrics_invalid",
            "message": "Saved model evaluation data has an invalid format.",
        }), 503

    saved_benchmark = payload.get("inference_benchmark")
    if not isinstance(saved_benchmark, dict):
        saved_benchmark = {}

    payload["inference_benchmark"] = _measured_inference_benchmark(
        saved_benchmark,
        g.user_id,
    )

    return jsonify(payload)


def _percentile(values, percentile):
    if not values:
        return None
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return values[index]


def _measured_inference_benchmark(saved, user_id):
    """Return descriptive runtime observations for this user only.

    The stored metric sums page-level model forward-pass timings for a document.
    It is observational application data, not a controlled seven-model benchmark.
    """
    benchmark = dict(saved)
    samples = []

    try:
        db = get_db()
        cursor = (
            db.documents.find(
                {
                    "user_id": user_id,
                    "status": "completed",
                    "result.performance.model_inference_ms": {"$exists": True},
                },
                {
                    "result.performance.model_inference_ms": 1,
                },
            )
            .sort("processed_at", -1)
            .limit(200)
        )

        for doc in cursor:
            result = doc.get("result")
            performance = (
                result.get("performance")
                if isinstance(result, dict)
                else None
            )
            if not isinstance(performance, dict):
                continue

            value = performance.get("model_inference_ms")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value > 0
            ):
                samples.append(float(value))

    except Exception:
        logger.exception("Unable to retrieve inference runtime observations.")
        benchmark["records"] = []
        benchmark["status"] = "unavailable"
        benchmark["message"] = (
            "Runtime observations could not be retrieved for this account."
        )
        return benchmark

    if not samples:
        benchmark["records"] = []
        benchmark["status"] = "unavailable"
        benchmark["message"] = (
            "No valid EfficientNetB0 runtime observations were found for this account. "
            "A controlled seven-model benchmark is not available because the other six "
            "model packages are not included."
        )
        return benchmark

    ordered = sorted(samples)
    average = mean(samples)

    benchmark["records"] = [{
        "model": DEPLOYED_MODEL,
        "inference_time_ms": round(average, 3),
        "mean_ms": round(average, 3),
        "median_ms": round(median(samples), 3),
        "p95_ms": round(_percentile(ordered, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "samples": len(samples),
    }]
    benchmark["status"] = "observational_runtime"
    benchmark["timed_scope"] = (
        "Sum of stored page-level model forward-pass timings per document; "
        "not OCR, preprocessing, or end-to-end processing time."
    )
    benchmark["method"] = (
        "Descriptive statistics over up to 200 recent completed documents "
        "belonging to the authenticated user. Not a controlled benchmark."
    )
    benchmark["message"] = (
        "Observed EfficientNetB0 runtime values for this account only. "
        "This is not a controlled seven-model comparison."
    )
    return benchmark
