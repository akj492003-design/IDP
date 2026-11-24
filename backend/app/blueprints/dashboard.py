import json
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, jsonify, g
from app.security import login_required, get_db
from app.services.quota import current_quota

bp = Blueprint("dashboard", __name__)


def _evaluation_payload(db=None, user_id=None):
    """Return Stage 9 and Stage 10 evaluations without mixing their datasets."""
    data_path = Path(__file__).resolve().parents[1] / "data" / "model_metrics.json"
    if not data_path.exists():
        return None

    payload = json.loads(data_path.read_text(encoding="utf-8"))
    evaluations = payload.get("evaluations") or {}
    development = evaluations.get("development_oof") or {}
    final_test = evaluations.get("final_test") or {}

    development_models = development.get("models") or []
    selected = next(
        (model for model in development_models if model.get("selected_model")),
        development_models[0] if development_models else None,
    )
    final_models = final_test.get("models") or []
    final_model = next(
        (model for model in final_models if model.get("selected_model")),
        final_models[0] if final_models else None,
    )

    if selected is None and final_model is None:
        return None

    accuracy_rows = [
        {
            "model": model.get("model"),
            "accuracy": round(float(model["accuracy"]) * 100, 2),
        }
        for model in development_models
        if isinstance(model.get("accuracy"), (int, float))
    ]

    metric_rows = [
        {
            "model": model.get("model"),
            "precision": round(float(model["precision"]) * 100, 2),
            "recall": round(float(model["recall"]) * 100, 2),
            "f1_score": round(float(model["f1_score"]) * 100, 2),
        }
        for model in development_models
        if all(isinstance(model.get(key), (int, float)) for key in ("precision", "recall", "f1_score"))
    ]

    selected_report = (final_model or {}).get("classification_report") or {}
    per_class = [
        {
            "class": label,
            "precision": round(float(row["precision"]) * 100, 2),
            "recall": round(float(row["recall"]) * 100, 2),
            "f1_score": round(float(row["f1_score"]) * 100, 2),
        }
        for label, row in selected_report.items()
        if isinstance(row, dict) and all(key in row for key in ("precision", "recall", "f1_score"))
    ]

    radar = []
    if selected:
        for key, label in (
            ("accuracy", "Accuracy"),
            ("precision", "Precision"),
            ("recall", "Recall"),
            ("f1_score", "F1 Score"),
        ):
            value = selected.get(key)
            if isinstance(value, (int, float)):
                radar.append({"metric": label, selected["model"]: round(float(value) * 100, 2)})

    return {
        "selected_model": selected.get("model") if selected else None,
        "metrics": {
            key: selected.get(key)
            for key in ("accuracy", "precision", "recall", "f1_score")
        } if selected else None,
        "development_oof": {
            "title": development.get("title"),
            "samples": development.get("samples"),
            "folds": development.get("folds"),
            "models": development_models,
            "charts": {
                "accuracy_comparison": accuracy_rows,
                "metrics_comparison": metric_rows,
                "model_comparison": radar,
            },
        },
        "final_test": {
            "title": final_test.get("title"),
            "samples": final_test.get("samples"),
            "correct_predictions": final_test.get("correct_predictions"),
            "incorrect_predictions": final_test.get("incorrect_predictions"),
            "model": final_model,
            "classification_report": selected_report,
            "confusion_matrix": (final_model or {}).get("confusion_matrix"),
            "per_class_performance": per_class,
        },
        "inference_time_comparison": [],
        "inference_benchmark": payload.get("inference_benchmark") or {
            "status": "unavailable",
            "records": [],
            "message": "Benchmark data unavailable.",
        },
        "source": {
            "source_notebook": payload.get("source_notebook"),
            "development_samples": development.get("samples"),
            "held_out_test_samples": final_test.get("samples"),
            "evaluation_note": (
                "Stage 9 development OOF results and Stage 10 independent held-out test results "
                "are different evaluation sets."
            ),
        },
    }


@bp.get("")
@login_required
def dashboard_summary():
    db = get_db()
    user_query = {"user_id": g.user_id}
    total = db.documents.count_documents(user_query)
    completed = db.documents.count_documents({**user_query, "status": "completed"})
    processing = db.documents.count_documents({**user_query, "status": {"$in": ["queued", "processing"]}})
    failed = db.documents.count_documents({**user_query, "status": "failed"})
    recent = db.documents.find({**user_query, "archived": {"$ne": True}}).sort("created_at", -1).limit(6)
    recent_items = [{"id": str(d["_id"]), "name": d["file_name"], "type": d["extension"].upper(), "size": d["size"], "status": d["status"], "created_at": d["created_at"].isoformat(), "classification": d.get("result", {}).get("classification", {}).get("label")} for d in recent]
    latest = db.documents.find_one({**user_query, "status": "completed"}, sort=[("processed_at", -1)])
    latest_payload = None
    if latest:
        result = latest.get("result", {})
        classification = result.get("classification") or {}
        scores = classification.get("scores") or []
        insights = result.get("insights") or {}
        started = latest.get("started_at")
        processed = latest.get("processed_at")
        elapsed = (processed - started).total_seconds() if started and processed else None
        if elapsed is not None and elapsed < 0:
            elapsed = None
        preview_url = f"/api/documents/{latest['_id']}/preview" if latest.get("extension") in {"pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"} else None
        latest_payload = {
            "id": str(latest["_id"]), "file_name": latest.get("file_name"), "status": latest.get("status"),
            "current_stage": "Field Extraction", "preview_url": preview_url,
            "extracted_information": insights.get("key_fields", {}),
            "ocr_text": result.get("extracted_text", ""),
            "page_texts": result.get("page_texts", []),
            "prediction": {"predicted_class": classification.get("label"), "confidence_score": scores[0].get("score") if scores else None,
                           "model_name": classification.get("model"), "processing_time": f"{elapsed:.2f} s" if elapsed is not None else "—"},
            "class_probabilities": [{"class": item.get("label"), "probability": round(float(item.get("score", 0)) * 100, 2)} for item in scores],
        }
    elapsed_values = []
    for item in db.documents.find({**user_query, "status": "completed", "started_at": {"$exists": True}, "processed_at": {"$exists": True}}, {"started_at": 1, "processed_at": 1}).limit(100):
        try:
            seconds = (item["processed_at"] - item["started_at"]).total_seconds()
            if seconds >= 0:
                elapsed_values.append(seconds)
        except (TypeError, KeyError):
            continue
    avg = f"{sum(elapsed_values)/len(elapsed_values):.2f} s" if elapsed_values else "—"
    return jsonify({"stats": {"total_documents": total, "completed": completed, "processing": processing, "failed": failed, "average_processing_time": avg},
                    "recent_uploads": recent_items, "latest_document": latest_payload,
                    "evaluation": _evaluation_payload(db, g.user_id), "quota": current_quota(g.user_id),
                    "generated_at": datetime.now(timezone.utc).isoformat()})
