
from datetime import datetime, timezone
import json
import hashlib
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
import shutil
import tempfile
import time

from celery import Celery
from bson import ObjectId

from app.services.document_pipeline import (
    extract_document_pages,
    build_insights,
)
from app.services.classifier import classify_canonical_images
from app.services.document_segmentation import (
    group_page_predictions,
    classification_for_group,
    reconcile_page_predictions,
)

# Keep the same bounded text limit for the overall result and each segment.
MAX_EXTRACTED_TEXT_CHARS = 2_000_000


# =========================================================
# DOCUMENT STORAGE
# =========================================================

def _materialize_document(flask_app, doc):
    """
    Retrieve document content from GridFS into a temporary file.

    Supports older documents that contain storage_path.

    Returns:
        (processing_path, is_temporary)
    """

    gridfs_file_id = doc.get("gridfs_file_id")

    if gridfs_file_id is not None:

        # Support BSON ObjectId and serialized string IDs.
        if isinstance(gridfs_file_id, str):

            if not ObjectId.is_valid(gridfs_file_id):
                raise ValueError("Invalid GridFS file ID.")

            gridfs_file_id = ObjectId(gridfs_file_id)

        bucket = flask_app.extensions["gridfs_bucket"]

        extension = Path(
            doc.get("file_name", "")
        ).suffix

        temp_path = None

        try:
            with bucket.open_download_stream(
                gridfs_file_id
            ) as grid_file:

                with NamedTemporaryFile(
                    mode="wb",
                    suffix=extension,
                    prefix="idp_worker_",
                    delete=False,
                ) as temp_file:

                    temp_path = Path(temp_file.name)

                    shutil.copyfileobj(
                        grid_file,
                        temp_file,
                        length=1024 * 1024,
                    )

            return temp_path, True

        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

            raise

    # Backward compatibility with older local-storage documents.
    storage_path = doc.get("storage_path")

    if storage_path:

        legacy_path = Path(storage_path)

        if legacy_path.is_file():
            return legacy_path, False

    raise FileNotFoundError(
        f"No accessible file found for document {doc.get('_id')}"
    )




def _queue_wait_ms(queued_at, started_at=None):
    """Calculate queue delay from the post-upload enqueue timestamp."""
    if not isinstance(queued_at, datetime):
        if isinstance(queued_at, str):
            try:
                queued_at = datetime.fromisoformat(queued_at.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
    if queued_at.tzinfo is None:
        queued_at = queued_at.replace(tzinfo=timezone.utc)
    started_at = started_at or datetime.now(timezone.utc)
    elapsed = (started_at - queued_at).total_seconds() * 1000
    return round(max(0.0, elapsed), 3)

# =========================================================
# CLASSIFICATION VALIDATION
# =========================================================

def _is_valid_classification(result):
    """
    Determine whether the classifier returned a usable category.
    """

    if not isinstance(result, dict):
        return False

    label = result.get("label")

    if not isinstance(label, str):
        return False

    return label.strip().lower() not in {
        "",
        "unknown",
        "unclassified",
    }



def _classify_document(text, extension, processing_path, flask_app, canonical_paths=None):
    """Classify only with the Stage 11 EfficientNetB0 package.

    The model requires Stage 4 canonical grayscale PNGs. Other model families
    are not silently substituted when canonical images are missing.
    """
    if not canonical_paths:
        return _text_classification_fallback(text)

    try:
        prediction = classify_canonical_images(canonical_paths)
        if _is_valid_classification(prediction):
            return prediction
        return {
            "label": "unclassified",
            "model": "EfficientNetB0",
            "score_type": "unavailable",
            "scores": [],
            "error": "Stage 11 EfficientNetB0 did not return a valid class.",
        }
    except Exception as exc:
        flask_app.logger.exception("Stage 11 EfficientNetB0 classification failed")
        return {
            "label": "unclassified",
            "model": "EfficientNetB0",
            "score_type": "unavailable",
            "scores": [],
            "error": str(exc)[:300],
        }


def _text_classification_fallback(text):
    """Conservative text-based fallback for Office/CSV/TXT files without images."""
    value = (text or "").lower()
    cues = {
        "invoice": [r"\btax invoice\b", r"\binvoice (?:no|number|date)\b", r"\bsubtotal\b", r"\bgrand total\b", r"\bcgst\b", r"\bsgst\b", r"\bigst\b", r"\bbill to\b"],
        "resume": [r"\b(curriculum vitae|resume)\b", r"\bwork experience\b", r"\beducation(?:al)?\b", r"\bprofessional summary\b", r"\btechnical skills\b", r"\bcareer objective\b"],
        "form": [r"\b(application form|registration form|admission form)\b", r"\bdate of birth\b", r"\bfather'?s name\b", r"\bapplicant name\b", r"\bdeclaration\b", r"\bcheckbox\b"],
    }
    counts = {label: sum(len(re.findall(pattern, value, re.I)) for pattern in patterns) for label, patterns in cues.items()}
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < 2 or ranked[0][1] == ranked[1][1]:
        return {"label": "unclassified", "model": "Text heuristic fallback", "score_type": "cue_count", "scores": [], "review_required": True, "error": "Insufficient document-specific text cues for a reliable category."}
    total = sum(counts.values()) or 1
    return {"label": ranked[0][0], "model": "Text heuristic fallback", "score_type": "normalized_cue_count_not_probability", "scores": [{"label": label, "score": round(count / total, 4)} for label, count in ranked], "review_required": True, "classification_source": "text_heuristic"}



# =========================================================
# CELERY REGISTRATION
# =========================================================

def register_tasks(flask_app):

    celery = Celery(
        "docupilot",
        broker=flask_app.config["CELERY_BROKER_URL"],
        backend=flask_app.config["CELERY_RESULT_BACKEND"],
    )

    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=None,
        broker_transport_options={"socket_timeout": 10, "socket_connect_timeout": 10, "retry_on_timeout": True, "health_check_interval": 30},
        result_backend_transport_options={"socket_timeout": 10, "retry_on_timeout": True, "health_check_interval": 30},
        task_publish_retry=True,
        task_publish_retry_policy={"max_retries": 5, "interval_start": 0, "interval_step": 0.5, "interval_max": 5},
        worker_max_tasks_per_child=100,
        timezone="UTC",
        enable_utc=True,
    )

    # =====================================================
    # PROCESS DOCUMENT TASK
    # =====================================================

    @celery.task(
        name="app.tasks.process_document",
        bind=True,
        max_retries=2,
    )
    def process_document(self, document_id):

        with flask_app.app_context():

            # Capture worker entry before the metadata query so queue delay
            # does not include MongoDB lookup latency.
            processing_wall_started = datetime.now(timezone.utc)
            db = flask_app.extensions["mongo_db"]

            # Validate the document ID before querying MongoDB.
            try:
                object_id = ObjectId(document_id)

            except Exception:
                flask_app.logger.exception(
                    "Invalid document ID received by Celery"
                )

                return {
                    "status": "invalid_document_id",
                }

            # Retrieve document metadata.
            db_read_started = time.perf_counter()
            try:
                doc = db.documents.find_one({
                    "_id": object_id,
                })

            except Exception as exc:
                flask_app.logger.exception(
                    "Could not retrieve document metadata: %s",
                    document_id,
                )

                if self.request.retries < self.max_retries:
                    raise self.retry(
                        exc=exc,
                        countdown=5,
                    )

                raise

            if not doc:
                return {
                    "status": "missing",
                }

            processing_started = time.perf_counter()
            phase_timings = {
                "queue_wait_ms": _queue_wait_ms(
                    doc.get("queued_at") or doc.get("created_at"),
                    processing_wall_started,
                ),
                "pdf_render_ms": 0.0,
                "ocr_model_load_ms": 0.0,
                "ocr_ms": 0.0,
                "preprocessing_ms": 0.0,
                "segmentation_ms": 0.0,
                "database_read_ms": round((time.perf_counter() - db_read_started) * 1000, 3),
                "database_write_ms": 0.0,
            }
            processing_path = None
            is_temporary = False

            try:

                # -------------------------------------------------
                # PROCESSING START
                # -------------------------------------------------

                now = datetime.now(timezone.utc)
                database_started = time.perf_counter()

                db.documents.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "status": "processing",
                            "progress": 5,
                            "current_stage": "File Uploaded",
                            "started_at": now,
                            "updated_at": now,
                        }
                    },
                )

                db.activities.insert_one({
                    "user_id": doc.get("user_id"),
                    "document_id": document_id,
                    "event_type": "processing_started",
                    "status": "processing",
                    "file_name": doc.get("file_name", ""),
                    "created_at": now,
                    "description": "Document processing started",
                })
                phase_timings["database_write_ms"] = round(
                    phase_timings["database_write_ms"] +
                    (time.perf_counter() - database_started) * 1000, 3
                )

                # -------------------------------------------------
                # MATERIALIZE DOCUMENT
                # -------------------------------------------------

                processing_path, is_temporary = (
                    _materialize_document(
                        flask_app,
                        doc,
                    )
                )

                extension = str(
                    doc.get("extension")
                    or Path(doc.get("file_name", "")).suffix
                ).lower().lstrip(".")

                # Read user preferences once for this processing attempt.
                settings_started = time.perf_counter()
                settings_doc = db.settings.find_one(
                    {"user_id": doc.get("user_id")},
                    {"settings": 1},
                )
                user_settings = (
                    settings_doc.get("settings")
                    if isinstance(settings_doc, dict)
                    and isinstance(settings_doc.get("settings"), dict)
                    else {}
                )
                phase_timings["database_read_ms"] = round(
                    phase_timings.get("database_read_ms", 0.0)
                    + (time.perf_counter() - settings_started) * 1000,
                    3,
                )
                ocr_enabled = user_settings.get("switches", {}).get("ocrState", True)
                if not isinstance(ocr_enabled, bool):
                    ocr_enabled = True

                # -------------------------------------------------
                # STAGE 1: TEXT EXTRACTION
                # -------------------------------------------------

                database_started = time.perf_counter()
                db.documents.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "progress": 10,
                            "current_stage": "Text Extraction",
                        }
                    },
                )
                phase_timings["database_write_ms"] = round(
                    phase_timings["database_write_ms"] +
                    (time.perf_counter() - database_started) * 1000, 3
                )

                extraction_started = time.perf_counter()
                with tempfile.TemporaryDirectory(prefix="idp_preprocess_") as artifact_dir:
                    page_texts = extract_document_pages(
                        str(processing_path),
                        extension,
                        artifacts_dir=artifact_dir,
                        timing_metrics=phase_timings,
                        enable_ocr=ocr_enabled,
                    )

                    canonical_paths = sorted(
                        str(path) for path in Path(artifact_dir).glob("*_canonical.png")
                    )
                    manifest_path = Path(artifact_dir) / "preprocessing_manifest.json"
                    preprocessing_manifest = (
                        json.loads(manifest_path.read_text(encoding="utf-8"))
                        if manifest_path.is_file() else []
                    )

                    text = "\n\n".join(page_texts)
                    phase_timings["text_extraction_ms"] = round(
                        (time.perf_counter() - extraction_started) * 1000, 3
                    )
                    phase_timings["native_text_ms"] = phase_timings.get("native_text_ms", 0.0)

                    page_count = len(page_texts) if extension == "pdf" else None

                    # -------------------------------------------------
                    # STAGE 2: DOCUMENT CLASSIFICATION
                    # -------------------------------------------------
                    database_started = time.perf_counter()
                    db.documents.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {"progress": 60, "current_stage": "Document Classification"}},
                    )
                    phase_timings["database_write_ms"] = round(
                        phase_timings["database_write_ms"] +
                        (time.perf_counter() - database_started) * 1000, 3
                    )
                    classification_started = time.perf_counter()
                    classification = _classify_document(
                        text=text,
                        extension=extension,
                        processing_path=processing_path,
                        flask_app=flask_app,
                        canonical_paths=canonical_paths,
                    )
                    phase_timings['classification_ms'] = round(
                        (time.perf_counter() - classification_started) * 1000, 3
                    )
                    if not canonical_paths and page_texts:
                        classification.setdefault("page_predictions", [{"page_number": i + 1, "label": classification.get("label", "unclassified"), "confidence": 0.0, "scores": classification.get("scores", [])} for i in range(len(page_texts))])

                    # Preserve the raw model telemetry before document grouping replaces
                    # the aggregate classifier result with a segment-level classification.
                    classifier_telemetry = {
                        key: classification.get(key)
                        for key in (
                            "inference_time_ms",
                            "model_preprocessing_ms",
                            "model_load_time_ms",
                            "classifier_duration_ms",
                        )
                    }

                    # Reconcile each visual page prediction with independent OCR
                    # structural evidence. The raw EfficientNet probabilities and
                    # visual class remain available for audit; only the final routing
                    # label is changed when the text evidence is strong and specific.
                    classification = reconcile_page_predictions(
                        page_texts,
                        classification,
                    )

                    segmentation_started = time.perf_counter()
                    page_groups = group_page_predictions(page_texts, classification)
                    phase_timings["segmentation_ms"] = round(
                        (time.perf_counter() - segmentation_started) * 1000,
                        3,
                    )

                # -------------------------------------------------
                # STAGE 3: INSIGHTS AND FIELD EXTRACTION
                # -------------------------------------------------

                database_started = time.perf_counter()
                db.documents.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "progress": 82,
                            "current_stage": "Field Extraction",
                        }
                    },
                )
                phase_timings["database_write_ms"] = round(
                    phase_timings["database_write_ms"] +
                    (time.perf_counter() - database_started) * 1000, 3
                )

                field_extraction_started = time.perf_counter()
                segmented_documents = []
                for segment_index, group in enumerate(page_groups, start=1):
                    segment_text = "\n\n".join(
                        page_texts[i] for i in group["page_indexes"] if i < len(page_texts)
                    ).strip()
                    segment_classification = classification_for_group(group, classification)
                    segment_source_pages = [
                        {"page_number": page_index + 1, "text": page_texts[page_index]}
                        for page_index in group["page_indexes"]
                        if 0 <= page_index < len(page_texts)
                    ]
                    segment_insights = build_insights(
                        segment_text,
                        segment_classification,
                        user_settings,
                        source_pages=segment_source_pages,
                        ocr_metrics=phase_timings,
                    )
                    boundary_warnings = list(group.get("boundary_warnings", []))
                    if group.get("boundary_review_required"):
                        segment_insights["review_required"] = True
                        segment_insights.setdefault("review_reasons", []).extend(boundary_warnings or [
                            "Page-level classification was not sufficiently confident to determine a document boundary. Verify this segment manually."
                        ])
                    segment_identity = (
                        f"{document_id}:{group['start_page']}:{group['end_page']}:"
                        f"{segment_classification.get('label', 'unclassified')}"
                    )
                    segment_id = hashlib.sha256(
                        segment_identity.encode("utf-8")
                    ).hexdigest()[:24]

                    segmented_documents.append({
                        "segment_id": segment_id,
                        "document_index": segment_index,
                        "page_range": {"start": group["start_page"], "end": group["end_page"]},
                        "page_count": len(group["page_indexes"]),
                        "classification": segment_classification,
                        "insights": segment_insights,
                        "extracted_text": segment_text[:MAX_EXTRACTED_TEXT_CHARS],
                        "text_truncated": len(segment_text) > MAX_EXTRACTED_TEXT_CHARS,
                        "boundary_method": group["boundary_method"],
                        "boundary_review_required": bool(group.get("boundary_review_required")),
                        "boundary_warnings": boundary_warnings,
                    })
                if len(segmented_documents) == 1:
                    classification = segmented_documents[0]["classification"]
                    insights = segmented_documents[0]["insights"]
                else:
                    merged_fields = {}
                    merged_provenance = {}
                    merged_validation_issues = []
                    for segment in segmented_documents:
                        segment_insights = segment.get("insights", {}) or {}
                        for key, value in (segment_insights.get("key_fields", {}) or {}).items():
                            if value in (None, "", [], {}):
                                continue
                            existing = merged_fields.setdefault(key, [])
                            values = value if isinstance(value, list) else [value]
                            for item in values:
                                if item not in existing:
                                    existing.append(item)
                        for key, evidence in (segment_insights.get("field_provenance", {}) or {}).items():
                            target = merged_provenance.setdefault(key, {
                                "source_pages": [],
                                "confidence": None,
                                "method": "normalized_text_match",
                                "evidence_status": "not_found_or_derived",
                            })
                            for page_number in evidence.get("source_pages", []):
                                if page_number not in target["source_pages"]:
                                    target["source_pages"].append(page_number)
                            if target["source_pages"]:
                                target["evidence_status"] = "source_text_match"
                        for issue in segment_insights.get("validation_issues", []) or []:
                            merged_validation_issues.append({
                                "document_index": segment.get("document_index"),
                                "page_range": segment.get("page_range"),
                                **issue,
                            })
                    for evidence in merged_provenance.values():
                        evidence["source_pages"].sort()
                    classification = {
                        "label": "multiple" if len(segmented_documents) > 1 else (segmented_documents[0]["classification"].get("label", "unclassified")),
                        "model": "EfficientNetB0" if canonical_paths else "Text heuristic fallback",
                        "score_type": "per_document_scores",
                        "scores": [],
                        "page_count": len(page_texts),
                        "aggregate_label_is_document_level": False,
                    }
                    insights = {
                        "summary": f"Detected {len(segmented_documents)} logical document segments in this uploaded file. Review each segment separately.",
                        "key_fields": merged_fields,
                        "field_provenance": merged_provenance,
                        "classification": classification,
                        "word_count": len(text.split()),
                        "character_count": len(text),
                        "field_coverage": {"category": "multiple", "expected_field_count": 0, "extracted_field_count": 0, "coverage_percent": 0.0, "extracted_fields": [], "missing_fields": []},
                        "validation_issues": merged_validation_issues,
                        "confidence_review": {"confidence": None, "prediction_rejected": False},
                        "review_required": any(bool(item.get("insights", {}).get("review_required")) or bool(item.get("boundary_review_required")) for item in segmented_documents),
                        "review_reasons": ["This upload contains multiple logical documents. Verify the detected page boundaries and each document's extracted fields."],
                    }
                phase_timings['field_extraction_ms'] = round(
                    (time.perf_counter() - field_extraction_started) * 1000, 3
                )

                # -------------------------------------------------
                # BUILD RESULT
                # -------------------------------------------------

                result = {
                    "extracted_text": text[:MAX_EXTRACTED_TEXT_CHARS],

                    "page_texts": [
                        page[:1_000_000]
                        for page in page_texts
                    ],

                    "text_truncated": (
                        len(text) > MAX_EXTRACTED_TEXT_CHARS
                    ),

                    "classification": classification,

                    "insights": insights,
                    "documents": segmented_documents,
                    "segmentation": {
                        "document_count": len(segmented_documents),
                        "method": "page_classification_and_text_start_markers",
                        "requires_manual_boundary_review": True,
                    },

                    "page_count": page_count,
                    "preprocessing": preprocessing_manifest,
                }

                # -------------------------------------------------
                # SAVE FINAL RESULT
                # -------------------------------------------------

                finished_at = datetime.now(timezone.utc)
                phase_timings['total_processing_ms'] = round(
                    (time.perf_counter() - processing_started) * 1000, 3
                )
                phase_timings["model_inference_ms"] = classifier_telemetry.get("inference_time_ms")
                phase_timings["model_input_preprocessing_ms"] = classifier_telemetry.get("model_preprocessing_ms")
                phase_timings["classifier_model_load_ms"] = classifier_telemetry.get("model_load_time_ms")
                phase_timings["classifier_duration_ms"] = classifier_telemetry.get("classifier_duration_ms")
                phase_timings["ocr_page_count"] = int(phase_timings.get("ocr_page_count", 0))
                phase_timings["native_text_ms"] = round(float(phase_timings.get("native_text_ms", 0.0)), 3)
                phase_timings['total_ocr_preprocessing_ms'] = round(
                    phase_timings.get('pdf_render_ms', 0.0)
                    + phase_timings.get('ocr_model_load_ms', 0.0)
                    + phase_timings.get('ocr_ms', 0.0)
                    + phase_timings.get('preprocessing_ms', 0.0),
                    3,
                )
                result['performance'] = phase_timings
                database_started = time.perf_counter()

                db.documents.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "status": "completed",
                            "progress": 100,
                            "current_stage": "Completed",
                            "result": result,
                            "processed_at": finished_at,
                            "updated_at": finished_at,
                        },

                        "$unset": {
                            "error_message": "",
                        },
                    },
                )

                db.activities.insert_one({
                    "user_id": doc.get("user_id"),
                    "document_id": document_id,
                    "event_type": "scan_completed",
                    "status": "completed",
                    "file_name": doc.get("file_name", ""),
                    "created_at": finished_at,
                    "description": (
                        f"Classified as "
                        f"{classification.get('label', 'unclassified')}"
                    ),
                })

                phase_timings['database_write_ms'] = round(
                    phase_timings.get('database_write_ms', 0.0) +
                    (time.perf_counter() - database_started) * 1000, 3
                )
                phase_timings['total_processing_ms'] = round(
                    (time.perf_counter() - processing_started) * 1000, 3
                )
                db.documents.update_one(
                    {'_id': doc['_id']},
                    {'$set': {'result.performance': phase_timings}},
                )

                return {
                    "status": "completed",
                    "document_id": document_id,
                }

            # =================================================
            # ERROR HANDLING AND RETRIES
            # =================================================

            except Exception as exc:

                flask_app.logger.exception(
                    "Document processing failed: %s",
                    document_id,
                )

                now = datetime.now(timezone.utc)

                # -------------------------------------------------
                # RETRY HANDLING
                # -------------------------------------------------

                if self.request.retries < self.max_retries:

                    try:

                        db.documents.update_one(
                            {"_id": doc["_id"]},
                            {
                                "$set": {
                                    "status": "queued",
                                    "progress": 0,
                                    "current_stage": "Queued for retry",
                                    "error_message": (
                                        "Temporary processing error; "
                                        "retry scheduled."
                                    ),
                                    "updated_at": now,
                                }
                            },
                        )

                        db.activities.insert_one({
                            "user_id": doc.get("user_id"),
                            "document_id": document_id,
                            "event_type": "processing_retry",
                            "status": "queued",
                            "file_name": doc.get("file_name", ""),
                            "created_at": now,
                            "description": (
                                "Temporary error; retry scheduled"
                            ),
                        })

                    except Exception:
                        flask_app.logger.exception(
                            "Could not persist retry status: %s",
                            document_id,
                        )

                    raise self.retry(
                        exc=exc,
                        countdown=5,
                    )

                # -------------------------------------------------
                # FINAL FAILURE
                # -------------------------------------------------

                try:

                    db.documents.update_one(
                        {"_id": doc["_id"]},
                        {
                            "$set": {
                                "status": "failed",
                                "progress": 0,
                                "current_stage": "Failed",
                                "error_message": str(exc)[:500],
                                "updated_at": now,
                            }
                        },
                    )

                    db.activities.insert_one({
                        "user_id": doc.get("user_id"),
                        "document_id": document_id,
                        "event_type": "scan_failed",
                        "status": "failed",
                        "file_name": doc.get("file_name", ""),
                        "created_at": now,
                        "description": (
                            "Document processing failed after "
                            "retry attempts"
                        ),
                    })

                except Exception:
                    flask_app.logger.exception(
                        "Could not persist final failure status: %s",
                        document_id,
                    )

                return {
                    "status": "failed",
                    "document_id": document_id,
                }

            # =================================================
            # TEMPORARY FILE CLEANUP
            # =================================================

            finally:

                if is_temporary and processing_path is not None:

                    try:
                        processing_path.unlink(
                            missing_ok=True
                        )

                    except OSError:
                        flask_app.logger.exception(
                            "Could not remove temporary document file"
                        )

    return celery
