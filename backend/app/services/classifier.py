import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from flask import current_app


@lru_cache(maxsize=1)
def _efficientnet_classifier(model_package_path):
    """Load the Stage 11 TensorFlow SavedModel once per worker process."""
    from app.models.efficientnetb0_idp_classifier.inference import IDPDocumentClassifier

    package = Path(model_package_path)
    if not package.is_dir():
        raise FileNotFoundError(f"EfficientNetB0 package not found: {package}")
    return IDPDocumentClassifier(model_dir=package / "saved_model")


def classify_canonical_images(image_paths):
    """Classify Stage 4 canonical grayscale pages with the Stage 11 model.

    The packaged class map is authoritative: 0=resume, 1=invoice, 2=form.
    No legacy SVM or Paddle classification fallback is performed.
    """
    paths = [Path(value) for value in (image_paths or []) if value]
    if not paths:
        return None

    package_path = current_app.config.get("EFFICIENTNET_MODEL_PATH")
    if not package_path:
        raise RuntimeError("EFFICIENTNET_MODEL_PATH is not configured.")

    model_load_started = time.perf_counter()
    model = _efficientnet_classifier(str(package_path))
    model_load_time_ms = round((time.perf_counter() - model_load_started) * 1000, 3)
    started = time.perf_counter()

    # Bound batch memory for long multi-page PDFs while reducing the number
    # of TensorFlow model calls compared with one call per page.
    batch_size = 16
    page_predictions = []
    for start in range(0, len(paths), batch_size):
        page_predictions.extend(
            model.predict_canonical_pngs(paths[start:start + batch_size])
        )

    labels = model.class_names
    class_ids = sorted(labels)

    if class_ids != [0, 1, 2]:
        raise ValueError(
            "Stage 11 class mapping must contain IDs 0, 1 and 2."
        )

    expected_mapping = {0: "resume", 1: "invoice", 2: "form"}
    if labels != expected_mapping:
        raise ValueError(
            f"Unexpected Stage 11 class mapping: {labels!r}"
        )

    probabilities = {
        labels[class_id]: float(np.mean([
            item["probabilities"][labels[class_id]]
            for item in page_predictions
        ]))
        for class_id in class_ids
    }

    total = float(sum(probabilities.values()))
    if not np.isfinite(total) or total <= 0:
        raise ValueError("EfficientNetB0 returned invalid probabilities.")

    probabilities = {
        label: score / total
        for label, score in probabilities.items()
    }

    ranked = sorted(
        probabilities.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    return {
        "label": ranked[0][0],
        "model": "EfficientNetB0",
        "score_type": "softmax_probability",
        "scores": [
            {"label": label, "score": float(score)}
            for label, score in ranked
        ],
        "inference_time_ms": round(
            sum(float(item.get("inference_time_ms", 0.0)) for item in page_predictions),
            3,
        ),
        "model_preprocessing_ms": round(
            sum(float(item.get("preprocessing_time_ms", 0.0)) for item in page_predictions),
            3,
        ),
        "classifier_duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "model_load_time_ms": model_load_time_ms,
        "page_count": len(paths),
        # Keep the per-page outputs. A mean probability across an entire PDF
        # is not a document-level prediction when the PDF contains multiple files.
        "page_predictions": [
            {
                "page_number": index + 1,
                "label": item.get("predicted_class", "unclassified"),
                "confidence": float(item.get("confidence", 0.0)),
                "scores": [
                    {"label": label, "score": float(item["probabilities"][label])}
                    for label in labels.values()
                ],
                "inference_time_ms": float(item.get("inference_time_ms", 0.0)),
                "preprocessing_time_ms": float(item.get("preprocessing_time_ms", 0.0)),
            }
            for index, item in enumerate(page_predictions)
        ],
        "aggregation": "mean_page_probabilities",
        "aggregate_label_is_document_level": False,
        "class_mapping": {
            str(class_id): labels[class_id]
            for class_id in class_ids
        },
    }
