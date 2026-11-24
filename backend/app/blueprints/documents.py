
import os
import hashlib
import re
import uuid

from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
    g,
    send_file,
)

from werkzeug.utils import secure_filename
from bson import ObjectId
from gridfs.errors import NoFile
from pymongo import ReturnDocument

from app.security import login_required, get_db
from app.date_range import parse_date_range_args
from app.services.quota import (
    consume_upload_slot,
    current_quota,
    release_upload_slot,
    QuotaExceeded,
)


# =========================================================
# BLUEPRINT CONFIGURATION
# =========================================================

bp = Blueprint("documents", __name__)


ALLOWED = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "tif",
    "tiff",
    "bmp",
    "webp",
    "docx",
    "xlsx",
    "xlsm",
    "pptx",
    "txt",
    "md",
    "rtf",
    "csv",
    "json",
    "xml",
    "bson",
}


# =========================================================
# SERIALIZATION HELPERS
# =========================================================

def _safe_isoformat(value):
    """Safely serialize datetime and string date values."""

    if value is None:
        return None

    if isinstance(value, str):
        return value

    if hasattr(value, "isoformat"):
        return value.isoformat()

    return None


def _serialize(doc, include_result=False):
    """Convert a MongoDB document into the existing API response."""

    extension = str(
        doc.get("extension") or ""
    ).lower()

    created = _safe_isoformat(
        doc.get("created_at")
    )

    updated = _safe_isoformat(
        doc.get("updated_at")
    )

    document_result = doc.get("result")

    if not isinstance(document_result, dict):
        document_result = {}

    try:
        progress = int(
            doc.get("progress") or 0
        )
    except (TypeError, ValueError):
        progress = 0

    result = {
        "id": str(doc["_id"]),
        "name": doc.get("file_name"),
        "file_name": doc.get("file_name"),
        "type": extension.upper(),
        "extension": extension,
        "mime_type": doc.get("mime_type"),
        "size": doc.get("size", 0),
        "status": doc.get("status", "queued"),
        "folder": doc.get("folder", ""),
        "tags": doc.get("tags", []),
        "archived": doc.get("archived", False),
        "progress": progress,
        "current_stage": doc.get(
            "current_stage",
            "File Uploaded",
        ),
        "created_at": created,
        "updated_at": updated,
        "classification": document_result.get(
            "classification"
        ),
        "download_url": (
            f"/api/documents/{doc['_id']}/download"
        ),
    }

    # Preserve the existing full-result response.
    if include_result and document_result:
        result["result"] = document_result

    if doc.get("error_message"):
        result["error_message"] = doc["error_message"]

    return result


# =========================================================
# GRIDFS HELPERS
# =========================================================

def _get_gridfs_file_id(doc):
    """Return a valid BSON ObjectId for a stored GridFS file."""

    file_id = doc.get("gridfs_file_id")

    if isinstance(file_id, ObjectId):
        return file_id

    if isinstance(file_id, str) and ObjectId.is_valid(file_id):
        return ObjectId(file_id)

    return None


def _read_document_content(doc):
    """
    Retrieve document bytes from GridFS.

    Returns None when the stored file is genuinely missing.
    Raises storage errors so callers can distinguish them from 404s.

    Retains backward compatibility with storage_path records.
    """

    raw_file_id = doc.get("gridfs_file_id")

    if raw_file_id is not None:

        gridfs_file_id = _get_gridfs_file_id(doc)

        if gridfs_file_id is None:
            raise ValueError(
                "The document contains an invalid GridFS file ID."
            )

        bucket = current_app.extensions.get(
            "gridfs_bucket"
        )

        if bucket is None:
            raise RuntimeError(
                "GridFS storage is not configured."
            )

        try:
            with bucket.open_download_stream(
                gridfs_file_id
            ) as grid_out:

                return grid_out.read()

        except NoFile:
            current_app.logger.warning(
                "GridFS file %s was not found.",
                gridfs_file_id,
            )
            return None

        except Exception:
            current_app.logger.exception(
                "Failed to retrieve document from GridFS"
            )
            raise

    # A document marked as GridFS must have a valid file ID.
    if doc.get("storage_type") == "gridfs":
        raise ValueError(
            "The GridFS document is missing its file ID."
        )

    # Backward compatibility with older local-storage records.
    storage_path = doc.get("storage_path")

    if storage_path:

        try:
            path = Path(storage_path)

            if not path.is_file():
                return None

            return path.read_bytes()

        except FileNotFoundError:
            return None

        except OSError:
            current_app.logger.exception(
                "Failed to retrieve legacy document"
            )
            raise

    return None


def _delete_document_content(doc):
    """Delete stored content safely and idempotently."""

    raw_file_id = doc.get("gridfs_file_id")

    gridfs_file_id = _get_gridfs_file_id(doc)

    if raw_file_id is not None:

        if gridfs_file_id is None:
            raise ValueError(
                "The document contains an invalid GridFS file ID."
            )

        bucket = current_app.extensions["gridfs_bucket"]

        try:
            bucket.delete(gridfs_file_id)

        except NoFile:
            # A previous deletion may already have removed the file.
            current_app.logger.info(
                "GridFS file %s was already absent during deletion",
                gridfs_file_id,
            )

        return

    if doc.get("storage_type") == "gridfs":
        raise ValueError(
            "The GridFS document is missing its file ID."
        )

    # Legacy local-storage cleanup.
    storage_path = doc.get("storage_path")

    if storage_path:
        Path(storage_path).unlink(missing_ok=True)


# =========================================================
# UPLOAD DOCUMENT
# =========================================================

@bp.post("")
@login_required
def upload_document():

    file = (
        request.files.get("file")
        or request.files.get("document")
    )

    if not file or not file.filename:
        return jsonify({
            "error": "missing_file",
            "message": "Choose a document to upload.",
        }), 400

    original_name = secure_filename(
        file.filename
    )

    ext = Path(
        original_name
    ).suffix.lower().lstrip(".")

    if ext not in ALLOWED:
        return jsonify({
            "error": "unsupported_file_type",
            "message": (
                f".{ext or 'unknown'} files are not supported."
            ),
        }), 415

    if ext == "pdf" and file.mimetype != "application/pdf":
        return jsonify({
            "error": "invalid_mime_type",
            "message": (
                "PDF uploads must use the application/pdf MIME type."
            ),
        }), 415

    # -----------------------------------------------------
    # DETERMINE FILE SIZE AND USER UPLOAD SETTINGS
    # -----------------------------------------------------

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)

    if size <= 0:
        return jsonify({
            "error": "empty_file",
            "message": "The selected file is empty.",
        }), 400

    if size > current_app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({
            "error": "file_too_large",
            "message": (
                f"The selected file exceeds the "
                f"{current_app.config['MAX_UPLOAD_MB']} MB server upload limit."
            ),
        }), 413

    # Respect the per-user format checklist. The backend ALLOWED set remains
    # authoritative, so unsupported archive formats cannot be enabled via API.
    settings_doc = get_db().settings.find_one(
        {"user_id": g.user_id},
        {"settings.formats": 1, "settings.maxSize": 1},
    )
    stored_settings = (
        settings_doc.get("settings")
        if isinstance(settings_doc, dict)
        else None
    )
    selected_formats = (
        stored_settings.get("formats")
        if isinstance(stored_settings, dict)
        else None
    )
    if not isinstance(selected_formats, list):
        selected_formats = ["PDF"]

    configured_limit_mb = int(current_app.config.get("MAX_UPLOAD_MB", 10))
    max_size_setting = (
        stored_settings.get("maxSize")
        if isinstance(stored_settings, dict)
        else None
    )
    size_options = {
        "5 MB": 5,
        "10 MB": 10,
        "25 MB": 25,
        "50 MB": 50,
    }
    requested_limit_mb = size_options.get(
        max_size_setting,
        min(10, configured_limit_mb),
    )
    effective_limit_mb = min(configured_limit_mb, requested_limit_mb)
    effective_limit_bytes = effective_limit_mb * 1024 * 1024

    if size > effective_limit_bytes:
        return jsonify({
            "error": "file_too_large",
            "message": (
                f"The selected file exceeds your effective "
                f"{effective_limit_mb} MB upload limit."
            ),
        }), 413

    selected_formats = {
        str(value).strip().lower()
        for value in selected_formats
        if isinstance(value, str)
    }
    if ext not in selected_formats:
        return jsonify({
            "error": "file_type_disabled",
            "message": (
                f".{ext} uploads are not enabled in your document upload settings. "
                "Enable this format in Settings and try again."
            ),
        }), 415

    # Hash only after size/type validation to avoid spending time hashing
    # rejected files. Duplicate detection remains advisory.
    digest = hashlib.sha256()
    while True:
        chunk = file.stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    file_hash = digest.hexdigest()
    file.stream.seek(0)

    duplicate = get_db().documents.find_one(
        {"user_id": g.user_id, "file_sha256": file_hash},
        {"_id": 1, "file_name": 1, "created_at": 1},
        sort=[("created_at", -1)],
    )

    # -----------------------------------------------------
    # ENFORCE UPLOAD QUOTA
    # -----------------------------------------------------

    try:
        quota = consume_upload_slot(
            g.user_id
        )

    except QuotaExceeded as exc:

        until = (
            exc.cooldown_until.isoformat()
            if exc.cooldown_until
            else None
        )

        return jsonify({
            "error": "upload_quota_exceeded",
            "message": str(exc),
            "cooldown_until": until,
            "quota": current_quota(g.user_id),
        }), 429

    # -----------------------------------------------------
    # INITIALIZE UPLOAD METADATA
    # -----------------------------------------------------

    document_id = uuid.uuid4().hex

    gridfs_file_id = None
    record = None

    now = datetime.now(timezone.utc)

    # -----------------------------------------------------
    # STORE FILE IN GRIDFS AND CREATE DOCUMENT RECORD
    # -----------------------------------------------------

    try:

        bucket = current_app.extensions["gridfs_bucket"]

        file.stream.seek(0)

        gridfs_file_id = bucket.upload_from_stream(
            original_name,
            file.stream,
            metadata={
                "public_id": document_id,
                "user_id": g.user_id,
                "extension": ext,
                "mime_type": (
                    file.mimetype
                    or "application/octet-stream"
                ),
                "size": size,
            },
        )

        # Create MongoDB document metadata.
        record = {
            "_id": ObjectId(),
            "public_id": document_id,
            "user_id": g.user_id,
            "file_name": original_name,
            "extension": ext,
            "mime_type": (
                file.mimetype
                or "application/octet-stream"
            ),
            "size": size,
            "file_sha256": file_hash,
            "is_duplicate": bool(duplicate),
            "duplicate_of": str(duplicate["_id"]) if duplicate else None,
            "gridfs_file_id": gridfs_file_id,
            "storage_type": "gridfs",
            "status": "queued",
            "folder": "",
            "tags": [],
            "archived": False,
            "created_at": now,
            # Set after GridFS upload completes so queue-delay telemetry does
            # not count the time spent transferring the uploaded file.
            "queued_at": datetime.now(timezone.utc),
            "updated_at": now,
        }

        get_db().documents.insert_one(record)

        # Record upload activity.
        get_db().activities.insert_one({
            "user_id": g.user_id,
            "document_id": document_id,
            "event_type": "upload",
            "status": "queued",
            "file_name": original_name,
            "created_at": now,
            "description": "Document uploaded",
        })

        # Queue document processing.
        current_app.extensions["celery"].send_task(
            "app.tasks.process_document",
            args=[str(record["_id"])],
        )

    except Exception:

        current_app.logger.exception(
            "Upload could not be stored or queued"
        )

        # -------------------------------------------------
        # GRIDFS ROLLBACK
        # -------------------------------------------------

        if gridfs_file_id is not None:

            try:
                current_app.extensions["gridfs_bucket"].delete(
                    gridfs_file_id
                )

            except NoFile:
                pass

            except Exception:
                current_app.logger.exception(
                    "GridFS upload cleanup failed"
                )

        # -------------------------------------------------
        # MONGODB METADATA ROLLBACK
        # -------------------------------------------------

        try:

            if record is not None:

                get_db().documents.delete_one({
                    "_id": record["_id"],
                    "user_id": g.user_id,
                })

                get_db().activities.delete_one({
                    "user_id": g.user_id,
                    "document_id": document_id,
                    "event_type": "upload",
                })

        except Exception:
            current_app.logger.exception(
                "Upload metadata rollback failed"
            )

        # -------------------------------------------------
        # QUOTA ROLLBACK
        # -------------------------------------------------

        try:
            release_upload_slot(
                g.user_id,
                quota,
            )

        except Exception:
            current_app.logger.exception(
                "Upload quota rollback failed"
            )

        return jsonify({
            "error": "processing_unavailable",
            "message": (
                "The scan queue is unavailable. "
                "Check MongoDB, Redis and the Celery worker, then retry."
            ),
        }), 503

    return jsonify({
        "message": "Upload accepted for processing.",
        "document": _serialize(record),
        "quota": quota,
        "duplicate": ({"document_id": str(duplicate["_id"]), "file_name": duplicate.get("file_name")} if duplicate else None),
    }), 202


# =========================================================
# LIST DOCUMENTS
# =========================================================

@bp.get("")
@login_required
def list_documents():

    page = max(
        1,
        request.args.get(
            "page",
            1,
            type=int,
        ),
    )

    limit = min(
        100,
        max(
            1,
            request.args.get(
                "limit",
                20,
                type=int,
            ),
        ),
    )

    query = {
        "user_id": g.user_id,
    }

    date_range, date_error = parse_date_range_args(request.args)
    if date_error:
        return jsonify({
            "error": "invalid_date_range",
            "message": date_error,
        }), 400

    if date_range:
        query["created_at"] = date_range

    archived = request.args.get("archived")

    if archived in ("true", "false"):
        query["archived"] = archived == "true"

    status = request.args.get("status")

    if status:
        query["status"] = status

    # Search using literal text, not a user-supplied regex.
    search_text = request.args.get(
        "q",
        "",
    ).strip()

    if search_text:

        if len(search_text) > 100:
            return jsonify({
                "error": "invalid_search",
                "message": (
                    "Search text cannot exceed 100 characters."
                ),
            }), 400

        query["file_name"] = {
            "$regex": re.escape(search_text),
            "$options": "i",
        }

    total = get_db().documents.count_documents(query)

    cursor = (
        get_db().documents.find(query)
        .sort("created_at", -1)
        .skip((page - 1) * limit)
        .limit(limit)
    )

    return jsonify({
        "items": [
            _serialize(doc)
            for doc in cursor
        ],

        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": (total + limit - 1) // limit,
        },

        "quota": current_quota(g.user_id),
    })


# =========================================================
# GET SINGLE DOCUMENT
# =========================================================

@bp.get("/<document_id>")
@login_required
def get_document(document_id):

    if ObjectId.is_valid(document_id):

        doc = get_db().documents.find_one({
            "_id": ObjectId(document_id),
            "user_id": g.user_id,
        })

    else:

        doc = get_db().documents.find_one({
            "public_id": document_id,
            "user_id": g.user_id,
        })

    if not doc:
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    return jsonify({
        "document": _serialize(
            doc,
            include_result=True,
        ),
    })


# =========================================================
# PREVIEW DOCUMENT
# =========================================================

@bp.get("/<document_id>/preview")
@login_required
def preview_document(document_id):

    if not ObjectId.is_valid(document_id):
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    doc = get_db().documents.find_one({
        "_id": ObjectId(document_id),
        "user_id": g.user_id,
    })

    if not doc:
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    previewable = {
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "tif",
        "tiff",
        "bmp",
        "webp",
    }

    extension = str(
        doc.get("extension") or ""
    ).lower()

    if extension not in previewable:
        return jsonify({
            "error": "preview_unavailable",
            "message": (
                "This document type cannot be previewed in the browser."
            ),
        }), 404

    try:
        content = _read_document_content(doc)

    except Exception:
        current_app.logger.exception(
            "Document preview storage retrieval failed"
        )

        return jsonify({
            "error": "storage_unavailable",
            "message": (
                "Document storage is temporarily unavailable."
            ),
        }), 503

    if content is None:
        return jsonify({
            "error": "preview_unavailable",
            "message": (
                "The document file could not be retrieved."
            ),
        }), 404

    response = send_file(
        BytesIO(content),
        as_attachment=False,
        download_name=doc["file_name"],
        mimetype=doc.get("mime_type"),
        conditional=True,
    )

    response.headers["X-Content-Type-Options"] = "nosniff"

    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; sandbox"
    )

    return response


# =========================================================
# DOWNLOAD DOCUMENT
# =========================================================

@bp.get("/<document_id>/download")
@login_required
def download_document(document_id):

    if not ObjectId.is_valid(document_id):
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    doc = get_db().documents.find_one({
        "_id": ObjectId(document_id),
        "user_id": g.user_id,
    })

    if not doc:
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    try:
        content = _read_document_content(doc)

    except Exception:
        current_app.logger.exception(
            "Document download storage retrieval failed"
        )

        return jsonify({
            "error": "storage_unavailable",
            "message": (
                "Document storage is temporarily unavailable."
            ),
        }), 503

    if content is None:
        return jsonify({
            "error": "not_found",
            "message": "Document file not found.",
        }), 404

    return send_file(
        BytesIO(content),
        as_attachment=True,
        download_name=doc["file_name"],
        mimetype=doc.get("mime_type"),
    )


# =========================================================
# EXPORT EXTRACTED FIELDS
# =========================================================

@bp.get("/<document_id>/export")
@login_required
def export_extracted_fields(document_id):
    """Export extracted fields while preserving nested field structure."""
    if not ObjectId.is_valid(document_id):
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    doc = get_db().documents.find_one({
        "_id": ObjectId(document_id),
        "user_id": g.user_id,
    })
    if not doc:
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    if doc.get("status") != "completed":
        return jsonify({
            "error": "not_ready",
            "message": "Exports are available after processing completes.",
        }), 409

    fmt = str(request.args.get("format", "json")).lower().strip()
    supported = {"json", "xml", "txt", "csv", "pdf", "docx", "xlsx"}
    if fmt not in supported:
        return jsonify({
            "error": "unsupported_format",
            "message": "Choose PDF, TXT, DOCX, XLSX, CSV, JSON or XML.",
        }), 400

    result = doc.get("result") if isinstance(doc.get("result"), dict) else {}
    segments = result.get("documents") if isinstance(result.get("documents"), list) else []
    export_scope = str(request.args.get("scope", "current")).lower().strip()
    if export_scope not in {"current", "all"}:
        return jsonify({
            "error": "invalid_export_scope",
            "message": "Choose current or all export scope.",
        }), 400

    if export_scope == "all":
        try:
            from app.services.bulk_export import build_bulk_export
            payload, mime, download_name = build_bulk_export(doc, fmt)
        except ValueError as exc:
            return jsonify({
                "error": "bulk_export_unavailable",
                "message": str(exc),
            }), 409
        except Exception:
            current_app.logger.exception("Bulk export generation failed")
            return jsonify({
                "error": "bulk_export_failed",
                "message": "Unable to generate the complete batch export.",
            }), 500

        return send_file(
            BytesIO(payload),
            as_attachment=True,
            download_name=download_name,
            mimetype=mime,
        )

    requested_index_raw = request.args.get("document_index")
    selected_segment = None
    selected_document_index = None

    if requested_index_raw not in (None, ""):
        try:
            requested_index = int(requested_index_raw)
        except (TypeError, ValueError):
            return jsonify({
                "error": "invalid_document_index",
                "message": "The selected document index must be an integer.",
            }), 400

        for position, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                continue
            try:
                segment_index = int(segment.get("document_index") or position)
            except (TypeError, ValueError):
                continue
            if segment_index == requested_index:
                selected_segment = segment
                break

        if selected_segment is None:
            return jsonify({
                "error": "document_segment_not_found",
                "message": "The selected logical document was not found in this upload.",
            }), 404

        selected_document_index = requested_index

    elif len(segments) > 1:
        return jsonify({
            "error": "document_index_required",
            "message": "This upload contains multiple documents. Select a document before exporting.",
        }), 400

    elif len(segments) == 1 and isinstance(segments[0], dict):
        selected_segment = segments[0]
        selected_document_index = int(selected_segment.get("document_index") or 1)

    if selected_segment is not None:
        insights = selected_segment.get("insights") if isinstance(selected_segment.get("insights"), dict) else {}
        classification = selected_segment.get("classification") if isinstance(selected_segment.get("classification"), dict) else {}
    else:
        insights = result.get("insights") if isinstance(result.get("insights"), dict) else {}
        classification = result.get("classification") if isinstance(result.get("classification"), dict) else {}

    fields = insights.get("key_fields") if isinstance(insights.get("key_fields"), dict) else {}

    from app.services.bulk_export import order_extracted_fields
    fields = order_extracted_fields(classification.get("label"), fields)

    if not fields:
        return jsonify({
            "error": "no_extracted_fields",
            "message": "No extracted fields are available to export.",
        }), 409

    if fmt in {"pdf", "docx"}:
        export_doc = dict(doc)

        if selected_segment is not None:
            export_result = dict(result)
            export_result["documents"] = [selected_segment]
            export_doc["result"] = export_result

        try:
            from app.services.bulk_export import build_bulk_export

            payload, mime, download_name = build_bulk_export(
                export_doc,
                fmt,
                scope="current",
            )

        except ValueError as exc:
            return jsonify({
                "error": "export_unavailable",
                "message": str(exc),
            }), 409

        except Exception:
            current_app.logger.exception(
                "Current PDF/DOCX export generation failed"
            )
            return jsonify({
                "error": "export_failed",
                "message": "Unable to generate the selected document report.",
            }), 500

        return send_file(
            BytesIO(payload),
            as_attachment=True,
            download_name=download_name,
            mimetype=mime,
        )

    import csv
    import json
    import textwrap
    import xml.etree.ElementTree as ET
    from io import StringIO

    def display_label(value):
        return re.sub(r"\s+", " ", str(value).replace("_", " ")).strip().title()

    def scalar_text(value):
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return str(value)

    def nested_text(value, depth=0):
        """Represent one nested value as readable multiline text."""
        indent = "  " * depth
        if isinstance(value, dict):
            if not value:
                return [f"{indent}—"]
            lines = []
            for key, child in value.items():
                label = display_label(key)
                if isinstance(child, (dict, list)):
                    lines.append(f"{indent}{label}:")
                    lines.extend(nested_text(child, depth + 1))
                else:
                    lines.append(f"{indent}{label}: {scalar_text(child)}")
            return lines
        if isinstance(value, list):
            if not value:
                return [f"{indent}—"]
            lines = []
            for child in value:
                if isinstance(child, (dict, list)):
                    lines.append(f"{indent}-")
                    lines.extend(nested_text(child, depth + 1))
                else:
                    lines.append(f"{indent}- {scalar_text(child)}")
            return lines
        return [f"{indent}{scalar_text(value)}"]

    def safe_xml_tag(value):
        tag = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))
        if not tag or not re.match(r"[A-Za-z_]", tag[0]):
            tag = f"field_{tag}"
        return tag

    file_stem = secure_filename(Path(doc.get("file_name", "document")).stem) or "document"
    if selected_document_index is not None and len(segments) > 1:
        file_stem = f"{file_stem}_document_{selected_document_index:02d}"
    original_name = str(doc.get("file_name") or "Document")
    document_type = str(classification.get("label") or "Unclassified")
    model_name = str(classification.get("model") or "Unavailable")
    processed_at = str(doc.get("processed_at") or "")
    selected_page_range = selected_segment.get("page_range") if selected_segment else {}
    if not isinstance(selected_page_range, dict):
        selected_page_range = {}
    source_page_start = selected_page_range.get("start")
    source_page_end = selected_page_range.get("end")
    if source_page_start is not None and source_page_end is not None:
        source_pages_label = (
            str(source_page_start)
            if source_page_start == source_page_end
            else f"{source_page_start}-{source_page_end}"
        )
    else:
        source_pages_label = "Unavailable"

    output = BytesIO()
    mime = "application/octet-stream"

    # -----------------------------------------------------
    # JSON — retain the original nested data structure.
    # -----------------------------------------------------
    if fmt == "json":
        export_payload = {
            "document": {
                "file_name": original_name,
                "document_type": document_type,
                "model": model_name,
                "processed_at": processed_at,
                "document_index": selected_document_index,
                "source_pages": source_pages_label,
            },
            "extracted_fields": fields,
        }
        payload = json.dumps(
            export_payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")
        mime = "application/json; charset=utf-8"

    # -----------------------------------------------------
    # CSV — one row per top-level field, exactly two columns.
    # Nested values remain in one cell with line breaks.
    # -----------------------------------------------------
    elif fmt == "csv":
        export_doc = dict(doc)
        if selected_segment is not None:
            export_result = dict(result)
            export_result["documents"] = [selected_segment]
            export_doc["result"] = export_result
        try:
            from app.services.bulk_export import build_bulk_export
            payload, mime, download_name = build_bulk_export(
                export_doc, "csv", scope="current"
            )
        except ValueError as exc:
            return jsonify({"error": "export_unavailable", "message": str(exc)}), 409
        except Exception:
            current_app.logger.exception("Current structured CSV export failed")
            return jsonify({"error": "export_failed", "message": "Unable to generate the structured CSV archive."}), 500
        return send_file(
            BytesIO(payload),
            as_attachment=True,
            download_name=download_name,
            mimetype=mime,
        )

    # -----------------------------------------------------
    # XML — retain nested dictionaries and arrays.
    # -----------------------------------------------------
    elif fmt == "xml":
        root_node = ET.Element("idp_extraction_report")
        metadata = ET.SubElement(root_node, "document")
        ET.SubElement(metadata, "file_name").text = original_name
        ET.SubElement(metadata, "document_type").text = document_type
        ET.SubElement(metadata, "model").text = model_name
        ET.SubElement(metadata, "processed_at").text = processed_at
        ET.SubElement(metadata, "document_index").text = str(selected_document_index or "")
        ET.SubElement(metadata, "source_pages").text = source_pages_label
        fields_node = ET.SubElement(root_node, "extracted_fields")

        def append_xml(parent, tag, value):
            node = ET.SubElement(parent, safe_xml_tag(tag))
            if isinstance(value, dict):
                for key, child in value.items():
                    append_xml(node, key, child)
            elif isinstance(value, list):
                for child in value:
                    append_xml(node, "item", child)
            elif value is not None:
                node.text = scalar_text(value)
            return node

        for key, value in fields.items():
            append_xml(fields_node, key, value)

        payload = ET.tostring(
            root_node,
            encoding="utf-8",
            xml_declaration=True,
        )
        mime = "application/xml; charset=utf-8"

    # -----------------------------------------------------
    # TXT — hierarchical numbered fields and nested bullets.
    # -----------------------------------------------------
    elif fmt == "txt":
        from app.services.bulk_export import prepare_export_display_fields
        fields = prepare_export_display_fields(document_type, fields)
        lines = [
            "INTELLIGENT DOCUMENT PROCESSING SYSTEM",
            "EXTRACTED INFORMATION REPORT",
            f"Original file: {original_name}",
            f"Document type: {document_type}",
            f"Classifier: {model_name}",
            f"Processed at: {processed_at or 'Unavailable'}",
            f"Logical document index: {selected_document_index or 'Unavailable'}",
            f"Source PDF pages: {source_pages_label}",
            "",
        ]
        for index, (key, value) in enumerate(fields.items(), start=1):
            lines.append(f"{index}. {display_label(key)}")
            lines.extend(nested_text(value, depth=1))
            lines.append("")
        payload = "\n".join(lines).encode("utf-8")
        mime = "text/plain; charset=utf-8"

    # -----------------------------------------------------
    # PDF — wrapped text, numbered field headings, nested bullets.
    # -----------------------------------------------------
    # -----------------------------------------------------
    # PDF - use the shared renderer for individual and bulk exports.
    # -----------------------------------------------------
    elif fmt == "pdf":
        export_doc = dict(doc)

        if selected_segment is not None:
            export_result = dict(result)
            export_result["documents"] = [selected_segment]
            export_doc["result"] = export_result

        try:
            from app.services.bulk_export import build_bulk_export

            payload, mime, download_name = build_bulk_export(
                export_doc,
                "pdf",
                scope="current",
            )
        except ValueError as exc:
            return jsonify({
                "error": "export_unavailable",
                "message": str(exc),
            }), 409
        except Exception:
            current_app.logger.exception("Current PDF export failed")
            return jsonify({
                "error": "export_failed",
                "message": "Unable to generate the PDF report.",
            }), 500

        return send_file(
            BytesIO(payload),
            as_attachment=True,
            download_name=download_name,
            mimetype=mime,
        )

    # -----------------------------------------------------
    # DOCX - semantic headings, bold labels, nested bullet lists.
    # -----------------------------------------------------
    elif fmt == "docx":
        from docx import Document
        from docx.shared import Inches, Pt

        document = Document()
        section = document.sections[0]
        section.top_margin = Inches(0.65)
        section.bottom_margin = Inches(0.65)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

        document.add_heading(
            "IDP System — Extracted Information Report",
            level=1,
        )
        document.add_paragraph(f"Original file: {original_name}")
        document.add_paragraph(f"Document type: {document_type}")
        document.add_paragraph(f"Classifier: {model_name}")
        document.add_paragraph(f"Processed at: {processed_at or 'Unavailable'}")
        document.add_paragraph(f"Logical document index: {selected_document_index or 'Unavailable'}")
        document.add_paragraph(f"Source PDF pages: {source_pages_label}")
        document.add_heading("Extracted fields", level=2)

        def add_docx_nested(value, depth=0):
            if isinstance(value, dict):
                if not value:
                    document.add_paragraph("—")
                for key, child in value.items():
                    label = display_label(key)
                    if isinstance(child, dict):
                        document.add_heading(label, level=min(3 + depth, 5))
                        add_docx_nested(child, depth + 1)
                    elif isinstance(child, list):
                        paragraph = document.add_paragraph()
                        run = paragraph.add_run(f"{label}:")
                        run.bold = True
                        add_docx_nested(child, depth + 1)
                    else:
                        paragraph = document.add_paragraph()
                        run = paragraph.add_run(f"{label}: ")
                        run.bold = True
                        paragraph.add_run(scalar_text(child))
            elif isinstance(value, list):
                if not value:
                    document.add_paragraph("—")
                for child in value:
                    style = "List Bullet" if depth <= 1 else "List Bullet 2"
                    if isinstance(child, (dict, list)):
                        paragraph = document.add_paragraph(style=style)
                        paragraph.add_run("Item").bold = True
                        add_docx_nested(child, depth + 1)
                    else:
                        document.add_paragraph(
                            scalar_text(child),
                            style=style,
                        )
            else:
                document.add_paragraph(scalar_text(value))

        for index, (key, value) in enumerate(fields.items(), start=1):
            document.add_heading(
                f"{index}. {display_label(key)}",
                level=2,
            )
            add_docx_nested(value)

        footer = section.footer.paragraphs[0]
        footer.text = (
            "Generated by the Intelligent Document Processing System"
        )
        footer.style = document.styles["Caption"]

        document.save(output)
        payload = output.getvalue()
        mime = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )

    # -----------------------------------------------------
    # XLSX — genuine workbook with Field / Extracted Value columns.
    # -----------------------------------------------------
    else:  # fmt == "xlsx"
        from app.services.bulk_export import prepare_export_display_fields
        fields = prepare_export_display_fields(document_type, fields)
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Extracted Fields"

        worksheet.merge_cells("A1:B1")
        worksheet["A1"] = "IDP SYSTEM — EXTRACTED INFORMATION"
        worksheet["A1"].font = Font(bold=True, size=15, color="FFFFFF")
        worksheet["A1"].fill = PatternFill("solid", fgColor="12345A")
        worksheet["A1"].alignment = Alignment(vertical="center")
        worksheet.row_dimensions[1].height = 28

        metadata = [
            ("Original file", original_name),
            ("Document type", document_type),
            ("Classifier", model_name),
            ("Processed at", processed_at or "Unavailable"),
            ("Logical document index", selected_document_index or "Unavailable"),
            ("Source PDF pages", source_pages_label),
        ]
        for row_index, (label, value) in enumerate(metadata, start=2):
            worksheet.cell(row=row_index, column=1, value=label)
            worksheet.cell(row=row_index, column=2, value=value)
            worksheet.cell(row=row_index, column=1).font = Font(bold=True)

        header_row = len(metadata) + 3
        for column, label in enumerate(
            ("Field", "Extracted Value"),
            start=1,
        ):
            cell = worksheet.cell(
                row=header_row,
                column=column,
                value=label,
            )
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1D4ED8")
            cell.alignment = Alignment(vertical="center")

        for row_index, (key, value) in enumerate(
            fields.items(),
            start=header_row + 1,
        ):
            worksheet.cell(
                row=row_index,
                column=1,
                value=display_label(key),
            )
            worksheet.cell(
                row=row_index,
                column=2,
                value="\n".join(nested_text(value)),
            )
            worksheet.cell(row=row_index, column=1).alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )
            worksheet.cell(row=row_index, column=2).alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )

        worksheet.column_dimensions[get_column_letter(1)].width = 28
        worksheet.column_dimensions[get_column_letter(2)].width = 85
        worksheet.freeze_panes = f"A{header_row + 1}"
        worksheet.auto_filter.ref = (
            f"A{header_row}:B{max(header_row, worksheet.max_row)}"
        )

        workbook.save(output)
        payload = output.getvalue()
        mime = (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )

    return send_file(
        BytesIO(payload),
        as_attachment=True,
        download_name=f"{file_stem}_extracted.{fmt}",
        mimetype=mime,
    )


# =========================================================
# UPDATE DOCUMENT
# =========================================================

@bp.patch("/<document_id>")
@login_required
def update_document(document_id):

    if not ObjectId.is_valid(document_id):
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    data = request.get_json(silent=True) or {}

    updates = {
        "updated_at": datetime.now(timezone.utc),
    }

    # -----------------------------------------------------
    # SAVE EXPLICIT CLASSIFICATION CONFIRMATION
    # -----------------------------------------------------

    if "classification_verification" in data:
        verification_payload = data.get("classification_verification")

        if (
            not isinstance(verification_payload, dict)
            or verification_payload.get("verified") is not True
        ):
            return jsonify({
                "error": "validation_error",
                "message": "A valid user confirmation is required.",
            }), 400

        verification_document = get_db().documents.find_one(
            {
                "_id": ObjectId(document_id),
                "user_id": g.user_id,
            },
            {
                "status": 1,
                "result": 1,
            },
        )

        if not verification_document:
            return jsonify({
                "error": "not_found",
                "message": "Document not found.",
            }), 404

        if verification_document.get("status") != "completed":
            return jsonify({
                "error": "document_not_ready",
                "message": "Only completed documents can be verified.",
            }), 409

        stored_result = verification_document.get("result")
        if not isinstance(stored_result, dict):
            stored_result = {}

        segments = stored_result.get("documents")
        update_path = None
        classification_data = None

        if isinstance(segments, list) and len(segments) > 0:
            raw_index = verification_payload.get("document_index")

            if isinstance(raw_index, bool) or raw_index is None:
                return jsonify({
                    "error": "document_index_required",
                    "message": "Select an individual document segment before confirming it.",
                }), 400

            try:
                requested_index = int(raw_index)
            except (TypeError, ValueError):
                return jsonify({
                    "error": "invalid_document_index",
                    "message": "The selected document index is invalid.",
                }), 400

            if requested_index < 1:
                return jsonify({
                    "error": "invalid_document_index",
                    "message": "The selected document index is invalid.",
                }), 400

            matched_position = None
            matched_segment = None

            for position, segment in enumerate(segments, start=1):
                if not isinstance(segment, dict):
                    continue

                stored_index = segment.get("document_index")
                if stored_index is None:
                    stored_index = position

                try:
                    candidate_index = int(stored_index)
                except (TypeError, ValueError):
                    continue

                if candidate_index == requested_index:
                    matched_position = position - 1
                    matched_segment = segment
                    break

            if matched_segment is None:
                return jsonify({
                    "error": "document_segment_not_found",
                    "message": "The selected document segment was not found.",
                }), 404

            classification_data = matched_segment.get("classification")
            update_path = (
                f"result.documents.{matched_position}.classification_verification"
            )

        else:
            if verification_payload.get("document_index") not in (None, ""):
                return jsonify({
                    "error": "unexpected_document_index",
                    "message": "This upload does not contain separate document segments.",
                }), 400

            classification_data = stored_result.get("classification")
            update_path = "result.classification_verification"

        if not isinstance(classification_data, dict):
            return jsonify({
                "error": "classification_unavailable",
                "message": "A final document classification is not available.",
            }), 409

        final_class = re.sub(
            r"[_-]+",
            " ",
            str(classification_data.get("label") or ""),
        ).strip().lower()

        allowed_classes = {"resume", "invoice", "form"}

        if final_class not in allowed_classes:
            return jsonify({
                "error": "classification_not_verifiable",
                "message": "Only a final Resume, Invoice, or Form classification can be confirmed.",
            }), 409

        submitted_class = re.sub(
            r"[_-]+",
            " ",
            str(verification_payload.get("confirmed_class") or ""),
        ).strip().lower()

        if submitted_class != final_class:
            return jsonify({
                "error": "classification_mismatch",
                "message": "The confirmed document type does not match the stored final classification.",
            }), 409

        verified_at = datetime.now(timezone.utc)

        updates[update_path] = {
            "verified": True,
            "status": "verified",
            "confirmed_class": final_class,
            "method": "user_confirmation",
            "verified_at": verified_at,
            "verified_by": str(g.user_id),
        }

    # -----------------------------------------------------
    # EXTRACTED_FIELD_CORRECTION_AND_REVIEW_DECISIONS
    field_update_path = None
    review_decision_path = None
    review_decision_event = None

    def _resolve_review_target(raw_index):
        stored_document = get_db().documents.find_one(
            {
                "_id": ObjectId(document_id),
                "user_id": g.user_id,
            },
            {"status": 1, "result": 1},
        )

        if not stored_document:
            return None, (jsonify({
                "error": "not_found",
                "message": "Document not found.",
            }), 404)

        if stored_document.get("status") != "completed":
            return None, (jsonify({
                "error": "document_not_ready",
                "message": "Only completed documents can be reviewed.",
            }), 409)

        stored_result = stored_document.get("result")
        if not isinstance(stored_result, dict):
            stored_result = {}

        segments = stored_result.get("documents")

        if isinstance(segments, list) and segments:
            if isinstance(raw_index, bool) or raw_index is None:
                return None, (jsonify({
                    "error": "document_index_required",
                    "message": "Select a document segment before making this change.",
                }), 400)

            try:
                requested_index = int(raw_index)
            except (TypeError, ValueError):
                return None, (jsonify({
                    "error": "invalid_document_index",
                    "message": "The selected document index is invalid.",
                }), 400)

            if requested_index < 1:
                return None, (jsonify({
                    "error": "invalid_document_index",
                    "message": "The selected document index is invalid.",
                }), 400)

            for position, segment in enumerate(segments):
                if not isinstance(segment, dict):
                    continue

                stored_index = segment.get("document_index", position + 1)

                try:
                    stored_index = int(stored_index)
                except (TypeError, ValueError):
                    continue

                if stored_index == requested_index:
                    return {
                        "base": f"result.documents.{position}",
                        "segment": segment,
                        "result": stored_result,
                    }, None

            return None, (jsonify({
                "error": "document_segment_not_found",
                "message": "The selected document segment was not found.",
            }), 404)

        if raw_index not in (None, ""):
            return None, (jsonify({
                "error": "unexpected_document_index",
                "message": "This upload has no separate document segments.",
            }), 400)

        return {
            "base": "result",
            "segment": None,
            "result": stored_result,
        }, None

    def _safe_extracted_value(value, depth=0):
        if depth > 8:
            return False

        if isinstance(value, dict):
            if len(value) > 100:
                return False

            for key, child in value.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 120
                    or "." in key
                    or key.startswith("$")
                    or not _safe_extracted_value(child, depth + 1)
                ):
                    return False

            return True

        if isinstance(value, list):
            return (
                len(value) <= 500
                and all(_safe_extracted_value(item, depth + 1) for item in value)
            )

        if isinstance(value, str):
            return len(value) <= 20000

        return value is None or isinstance(value, (bool, int, float))

    # Save category-specific extracted-field corrections.
    if "extracted_fields_update" in data:
        payload = data.get("extracted_fields_update")

        if not isinstance(payload, dict):
            return jsonify({
                "error": "validation_error",
                "message": "Extracted-field update must be an object.",
            }), 400

        corrected_fields = payload.get("fields")

        if (
            not isinstance(corrected_fields, dict)
            or len(corrected_fields) > 100
            or not _safe_extracted_value(corrected_fields)
        ):
            return jsonify({
                "error": "invalid_extracted_fields",
                "message": "The corrected fields contain invalid or oversized values.",
            }), 400

        target, target_error = _resolve_review_target(
            payload.get("document_index")
        )

        if target_error:
            return target_error

        classification = (
            target["segment"].get("classification")
            if target["segment"] is not None
            else target["result"].get("classification")
        )

        classification = classification if isinstance(classification, dict) else {}
        document_type = str(classification.get("label") or "").strip().lower()

        if document_type not in {"resume", "invoice", "form"}:
            return jsonify({
                "error": "classification_unavailable",
                "message": "Only Resume, Invoice, and Form fields can be corrected.",
            }), 409

        field_update_path = f'{target["base"]}.insights.key_fields'
        updates[field_update_path] = corrected_fields

    # Save an explicit human approval or rejection independently
    # of the machine-learning classification verification.
    if "review_decision" in data:
        payload = data.get("review_decision")

        if not isinstance(payload, dict):
            return jsonify({
                "error": "validation_error",
                "message": "Review decision must be an object.",
            }), 400

        decision = str(payload.get("decision") or "").strip().lower()

        if decision not in {"approved", "rejected"}:
            return jsonify({
                "error": "invalid_review_decision",
                "message": "Decision must be approved or rejected.",
            }), 400

        comment = str(payload.get("comment") or "").strip()

        if len(comment) > 1000:
            return jsonify({
                "error": "invalid_review_comment",
                "message": "Review comments cannot exceed 1000 characters.",
            }), 400

        target, target_error = _resolve_review_target(
            payload.get("document_index")
        )

        if target_error:
            return target_error

        review_decision_path = f'{target["base"]}.review_decision'
        review_decision_event = decision

        updates[review_decision_path] = {
            "decision": decision,
            "status": decision,
            "comment": comment,
            "reviewed_at": datetime.now(timezone.utc),
            "reviewed_by": str(g.user_id),
        }
    # UPDATE FOLDER, TAGS AND ARCHIVE STATUS
    # -----------------------------------------------------

    for field in ("folder", "tags", "archived"):

        if field not in data:
            continue

        if field == "folder" and isinstance(data[field], str):
            updates[field] = data[field][:80]

        elif field == "tags" and isinstance(data[field], list):
            updates[field] = [
                str(value)[:40]
                for value in data[field][:20]
            ]

        elif field == "archived" and isinstance(data[field], bool):
            updates[field] = data[field]

    # -----------------------------------------------------
    # UPDATE FILE NAME
    # -----------------------------------------------------

    if "file_name" in data or "name" in data:

        name = secure_filename(
            str(
                data.get(
                    "file_name",
                    data.get("name", ""),
                )
            ).strip()
        )

        if not name:
            return jsonify({
                "error": "validation_error",
                "message": "A valid file name is required.",
            }), 400

        current = get_db().documents.find_one(
            {
                "_id": ObjectId(document_id),
                "user_id": g.user_id,
            },
            {
                "extension": 1,
            },
        )

        if not current:
            return jsonify({
                "error": "not_found",
                "message": "Document not found.",
            }), 404

        requested_extension = (
            Path(name).suffix.lower().lstrip(".")
        )

        current_extension = str(
            current.get("extension") or ""
        ).lower()

        if requested_extension != current_extension:
            return jsonify({
                "error": "validation_error",
                "message": (
                    "Renaming cannot change the original file extension."
                ),
            }), 400

        updates["file_name"] = name

    # -----------------------------------------------------
    # SAVE UPDATES
    # -----------------------------------------------------

    # AUDIT TRAIL FOR DOCUMENT UPDATES
    audit_previous_document = get_db().documents.find_one(
        {
            "_id": ObjectId(document_id),
            "user_id": g.user_id,
        }
    )

    result = get_db().documents.find_one_and_update(
        {
            "_id": ObjectId(document_id),
            "user_id": g.user_id,
        },
        {
            "$set": updates,
        },
        return_document=ReturnDocument.AFTER,
    )

    if not result:
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    # Record before-and-after values for successful document updates.
    audit_changes = []

    def _audit_read_path(document, dotted_path):
        value = document

        for part in dotted_path.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list) and part.isdigit():
                index = int(part)
                value = value[index] if 0 <= index < len(value) else None
            else:
                return None

        return value

    for field_path in updates:
        if field_path == "updated_at" or field_path == field_update_path:
            continue

        before_value = _audit_read_path(
            audit_previous_document, field_path
        )
        after_value = _audit_read_path(result, field_path)

        if before_value != after_value:
            audit_changes.append({
                "field": field_path,
                "before": before_value,
                "after": after_value,
            })

    if field_update_path:
        before_fields = _audit_read_path(
            audit_previous_document, field_update_path
        )
        after_fields = _audit_read_path(result, field_update_path)

        if not isinstance(before_fields, dict):
            before_fields = {}
        if not isinstance(after_fields, dict):
            after_fields = {}

        for field_name in sorted(set(before_fields) | set(after_fields)):
            before_value = before_fields.get(field_name)
            after_value = after_fields.get(field_name)

            if before_value != after_value:
                audit_changes.append({
                    "field": f"extracted_fields.{field_name}",
                    "before": before_value,
                    "after": after_value,
                })

    if audit_changes:
        verification_only = all(
            change["field"].endswith("classification_verification")
            for change in audit_changes
        )

        event_type = (
            "document_approved"
            if review_decision_event == "approved"
            else "document_rejected"
            if review_decision_event == "rejected"
            else "extracted_fields_corrected"
            if field_update_path
            else "classification_verified"
            if verification_only
            else "document_updated"
        )

        changed_labels = [
            change["field"].split(".")[-1].replace("_", " ")
            for change in audit_changes
        ]

        description = (
            "Document approved by reviewer"
            if review_decision_event == "approved"
            else "Document rejected by reviewer"
            if review_decision_event == "rejected"
            else "Extracted fields corrected: " + ", ".join(changed_labels)
            if field_update_path
            else "Document classification verified"
            if verification_only
            else "Document updated: " + ", ".join(changed_labels)
        )

        try:
            get_db().activities.insert_one({
                "user_id": g.user_id,
                "actor_user_id": str(g.user_id),
                "user": str(g.user_id),
                "document_id": document_id,
                "event_type": event_type,
                "status": "completed",
                "file_name": result.get("file_name", ""),
                "created_at": result.get("updated_at") or datetime.now(timezone.utc),
                "description": description,
                "changes": audit_changes,
            })
        except Exception:
            current_app.logger.exception(
                "Document update succeeded but audit activity could not be saved: %s",
                document_id,
            )

    return jsonify({
        "document": _serialize(result),
    })


# =========================================================
# DELETE DOCUMENT
# =========================================================

@bp.delete("/<document_id>")
@login_required
def delete_document(document_id):

    if not ObjectId.is_valid(document_id):
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    document_filter = {
        "_id": ObjectId(document_id),
        "user_id": g.user_id,
    }

    doc = get_db().documents.find_one(
        document_filter
    )

    if not doc:
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    # -----------------------------------------------------
    # DELETE STORED FILE CONTENT
    # -----------------------------------------------------

    # Keep document metadata if file deletion fails.
    try:
        _delete_document_content(doc)

    except Exception:
        current_app.logger.exception(
            "Document file deletion failed; retaining document record"
        )

        return jsonify({
            "error": "file_deletion_failed",
            "message": (
                "The document file could not be deleted. "
                "The document record has been retained; please retry."
            ),
        }), 503

    # -----------------------------------------------------
    # DELETE MONGODB DOCUMENT METADATA
    # -----------------------------------------------------

    try:
        delete_result = get_db().documents.delete_one(
            document_filter
        )

    except Exception:
        current_app.logger.exception(
            "Document file was cleaned up but metadata deletion failed"
        )

        return jsonify({
            "error": "document_deletion_failed",
            "message": (
                "The file cleanup completed, but the document record "
                "could not be removed. Please retry deletion."
            ),
        }), 503

    if delete_result.deleted_count != 1:
        return jsonify({
            "error": "not_found",
            "message": "Document not found.",
        }), 404

    # -----------------------------------------------------
    # SAVE DELETION ACTIVITY
    # -----------------------------------------------------

    # Best-effort activity logging: the document and file
    # have already been deleted.
    try:
        get_db().activities.insert_one({
            "user_id": g.user_id,
            "document_id": document_id,
            "event_type": "delete",
            "status": "completed",
            "file_name": doc["file_name"],
            "created_at": datetime.now(timezone.utc),
            "description": "Document deleted",
        })

    except Exception:
        current_app.logger.exception(
            "Document was deleted, but its activity entry could not be saved"
        )

    return jsonify({
        "message": "Document deleted.",
    })

# INVOICE_FORM_EXPORT_PRESENTATION_FIX_20261116
