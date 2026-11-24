from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, g
from pymongo.errors import PyMongoError

from app.security import login_required, get_db


bp = Blueprint("settings", __name__)

DEFAULTS = {
    "theme": "navy",
    "preferredModel": "auto",
    "textModels": [
        "Linear SVM",
        "Logistic Regression",
        "KNN",
        "Gaussian NB",
    ],
    "imageModels": [
        "EfficientNetB0",
        "MobileNetV2",
        "ResNet18",
    ],
    "formats": ["PDF"],
    "threshold": 70,
    "lowAction": "Mark for manual review",
    "maxSize": "10 MB",
    "retention": "Keep until manually deleted",
    "ocrDisplay": "Show status (Required / Completed / Failed)",
    "switches": {
        "rejectState": True,
        "ocrState": True,
        "showState": True,
        "preserveState": True,
    },
    "collapsed": {},
    "managedFolders": [
        "Invoices", "Forms", "Resumes", "College", "Personal", "Work",
    ],
    "managedTags": [
        {"name": "Important", "tone": "violet"},
        {"name": "Urgent", "tone": "rose"},
        {"name": "Reviewed", "tone": "emerald"},
        {"name": "Pending", "tone": "amber"},
    ],
}


def _copy_defaults():
    return {
        **DEFAULTS,
        "textModels": DEFAULTS["textModels"][:],
        "imageModels": DEFAULTS["imageModels"][:],
        "formats": DEFAULTS["formats"][:],
        "switches": DEFAULTS["switches"].copy(),
        "collapsed": {},
        "managedFolders": DEFAULTS["managedFolders"][:],
        "managedTags": [tag.copy() for tag in DEFAULTS["managedTags"]],
    }


def _string_list(value, allowed, fallback):
    if not isinstance(value, list):
        return fallback[:]
    allowed_set = set(allowed)
    result = []
    for item in value:
        if isinstance(item, str) and item in allowed_set and item not in result:
            result.append(item)
    return result


def _normalize_settings(incoming):
    """Validate untrusted settings input and return a safe complete document."""
    settings = _copy_defaults()

    size_options = {
        "5 MB": 5,
        "10 MB": 10,
        "25 MB": 25,
        "50 MB": 50,
    }
    server_limit = current_app.config.get("MAX_UPLOAD_MB", 10)
    if (
        not isinstance(server_limit, int)
        or isinstance(server_limit, bool)
        or server_limit < 1
    ):
        server_limit = 10

    default_limit = min(size_options[DEFAULTS["maxSize"]], server_limit)
    settings["maxSize"] = f"{default_limit} MB"

    if not isinstance(incoming, dict):
        return settings

    if incoming.get("theme") in {"light", "navy"}:
        settings["theme"] = incoming["theme"]

    preferred_model = incoming.get("preferredModel")
    valid_models = {
        "auto", "EfficientNetB0", "MobileNetV2", "ResNet18",
        "Linear SVM", "Logistic Regression", "KNN", "Gaussian NB",
    }
    if isinstance(preferred_model, str) and preferred_model in valid_models:
        settings["preferredModel"] = preferred_model

    settings["textModels"] = _string_list(
        incoming.get("textModels"),
        DEFAULTS["textModels"],
        settings["textModels"],
    )
    settings["imageModels"] = _string_list(
        incoming.get("imageModels"),
        DEFAULTS["imageModels"],
        settings["imageModels"],
    )
    settings["formats"] = _string_list(
        incoming.get("formats"),
        [
            "PDF", "JPG", "PNG", "JPEG", "DOCX", "XLSX", "PPTX",
            "TXT", "CSV", "BSON", "JSON", "XML",
        ],
        settings["formats"],
    )

    threshold = incoming.get("threshold", DEFAULTS["threshold"])
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        settings["threshold"] = min(90, max(50, int(threshold)))

    if incoming.get("lowAction") in {
        "Mark for manual review",
        "Reject prediction",
    }:
        settings["lowAction"] = incoming["lowAction"]

    requested_size = incoming.get("maxSize")
    if requested_size in size_options:
        effective_size = min(size_options[requested_size], server_limit)
        # The per-user preference can lower, but never exceed, the global cap.
        settings["maxSize"] = f"{effective_size} MB"

    if incoming.get("retention") in {
        "Keep until manually deleted",
        "30 days",
        "90 days",
        "1 year",
    }:
        settings["retention"] = incoming["retention"]

    if incoming.get("ocrDisplay") in {
        "Show status (Required / Completed / Failed)",
        "Show only failures",
        "Hide status",
    }:
        settings["ocrDisplay"] = incoming["ocrDisplay"]

    switches = incoming.get("switches")
    if isinstance(switches, dict):
        settings["switches"] = {
            key: switches.get(key, default)
            if isinstance(switches.get(key, default), bool)
            else default
            for key, default in DEFAULTS["switches"].items()
        }

    collapsed = incoming.get("collapsed")
    if isinstance(collapsed, dict):
        settings["collapsed"] = {
            str(key)[:80]: value
            for key, value in list(collapsed.items())[:100]
            if isinstance(key, str) and isinstance(value, bool)
        }

    folders = incoming.get("managedFolders")
    if isinstance(folders, list):
        cleaned = []
        for item in folders:
            if isinstance(item, str):
                name = item.strip()[:80]
                if name and name not in cleaned:
                    cleaned.append(name)
        settings["managedFolders"] = cleaned[:100]

    tags = incoming.get("managedTags")
    if isinstance(tags, list):
        cleaned_tags = []
        allowed_tones = {
            "violet", "rose", "emerald", "amber", "sky", "blue", "green",
        }
        for item in tags[:100]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            tone = item.get("tone", "sky")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(tone, str) or tone not in allowed_tones:
                tone = "sky"
            cleaned_tags.append({
                "name": name.strip()[:40],
                "tone": tone,
            })
        settings["managedTags"] = cleaned_tags

    return settings


@bp.get("")
@login_required
def get_settings():
    try:
        doc = get_db().settings.find_one({"user_id": g.user_id})
    except PyMongoError:
        return jsonify({
            "error": "service_unavailable",
            "message": "Settings are temporarily unavailable.",
        }), 503

    stored = doc.get("settings") if isinstance(doc, dict) else None
    if not isinstance(stored, dict):
        stored = {}

    settings = _normalize_settings(stored)
    return jsonify({"settings": settings})


@bp.put("")
@login_required
def put_settings():
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify({
            "error": "validation_error",
            "message": "Settings payload must be a JSON object.",
        }), 400

    settings = _normalize_settings(incoming)
    now = datetime.now(timezone.utc)

    try:
        get_db().settings.update_one(
            {"user_id": g.user_id},
            {
                "$set": {
                    "settings": settings,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "user_id": g.user_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )
    except PyMongoError:
        return jsonify({
            "error": "service_unavailable",
            "message": "Settings could not be saved. Please try again.",
        }), 503

    return jsonify({
        "message": "Settings saved.",
        "settings": settings,
    })
