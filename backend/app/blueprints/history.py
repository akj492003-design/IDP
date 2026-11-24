from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, g

from app.security import login_required, get_db
from app.date_range import parse_date_range_args

bp = Blueprint("history", __name__)

EVENT_LABELS = {
    "upload": "Document Uploaded",
    "processing_started": "OCR Processing Started",
    "processing_retry": "OCR Processing Retried",
    "scan_completed": "Document Completed",
    "scan_failed": "OCR Processing Failed",
    "delete": "Document Deleted",
    "settings_changed": "Settings Changed",
    # AUDIT TRAIL EVENT LABELS
    "document_updated": "Document Updated",
    "classification_verified": "Classification Verified",
    "extracted_fields_corrected": "Extracted Fields Corrected",
    "document_approved": "Document Approved",
    "document_rejected": "Document Rejected",
}

STATUS_LABELS = {
    "queued": "In Progress",
    "pending": "In Progress",
    "processing": "Processing",
    "completed": "Success",
    "failed": "Failed",
}


def _timestamp(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return ""
    return ""


def _humanize_event(raw_type):
    raw_type = str(raw_type or "activity").strip()
    if raw_type in EVENT_LABELS:
        return EVENT_LABELS[raw_type]
    return " ".join(part.capitalize() for part in raw_type.replace("-", "_").split("_") if part) or "Activity"


@bp.get("")
@login_required
def get_history():
    page = max(1, request.args.get("page", 1, type=int))
    limit = min(100, max(1, request.args.get("limit", 30, type=int)))

    query = {"user_id": g.user_id}

    date_range, date_error = parse_date_range_args(request.args)
    if date_error:
        return jsonify({
            "error": "invalid_date_range",
            "message": date_error,
        }), 400

    if date_range:
        query["created_at"] = date_range

    event_type = request.args.get("type")
    status = request.args.get("status")

    if event_type and event_type != "all":
        query["event_type"] = event_type
    if status and status != "all":
        query["status"] = status

    db = get_db()
    total = db.activities.count_documents(query)
    cursor = (
        db.activities.find(query)
        .sort("created_at", -1)
        .skip((page - 1) * limit)
        .limit(limit)
    )

    events = []
    for item in cursor:
        raw_status = str(item.get("status") or "completed").lower()
        events.append({
            "id": str(item.get("_id", "")),
            "documentId": str(item.get("document_id") or ""),
            "eventType": str(item.get("event_type") or "activity"),
            "type": _humanize_event(item.get("event_type")),
            "status": STATUS_LABELS.get(raw_status, raw_status.replace("_", " ").title()),
            "fileName": str(item.get("file_name") or "Document"),
            "description": str(item.get("description") or ""),
            "timestamp": _timestamp(item.get("created_at")),
            "user": str(item.get("user") or item.get("actor_user_id") or "system"),
            "actorUserId": str(item.get("actor_user_id") or ""),
            "changes": (
                item.get("changes")
                if isinstance(item.get("changes"), list)
                else []
            ),
        })

    return jsonify({
        "events": events,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit,
        },
    })
