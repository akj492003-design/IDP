"""Canonical image preparation matching the Stage 4 training contract.

The model image is saved immediately after orientation correction and conservative
Hough deskew. Denoising, CLAHE and adaptive thresholding are applied only to the
separate OCR image and never to the EfficientNet input.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _orientation_classifier():
    from paddleocr import DocImgOrientationClassification
    return DocImgOrientationClassification(
        model_name="PP-LCNet_x1_0_doc_ori",
        device="cpu",
    )


def _result_payload(result):
    payload = getattr(result, "json", None)
    if callable(payload):
        payload = payload()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    if isinstance(payload, dict):
        return payload.get("res", payload)
    if isinstance(result, dict):
        return result.get("res", result)
    return {}


def _predict_orientation(image):
    try:
        output = _orientation_classifier().predict(image, batch_size=1)
        if not output:
            return None, 0.0
        payload = _result_payload(output[0])
        labels = payload.get("label_names") or []
        scores = payload.get("scores") or []
        label = str(labels[0]) if labels else ""
        score = float(np.asarray(scores).reshape(-1)[0]) if len(scores) else 0.0
        if label not in {"0", "90", "180", "270"} or not np.isfinite(score):
            return None, 0.0
        return int(label), score
    except Exception:
        logger.exception("Document orientation prediction unavailable; retaining source orientation")
        return None, 0.0


def _to_uint8(image):
    """Match the Stage 4 notebook's deterministic dtype conversion."""
    if image.dtype == np.uint8:
        return image
    if np.issubdtype(image.dtype, np.integer):
        minimum, maximum = float(np.min(image)), float(np.max(image))
        if maximum <= minimum:
            return np.zeros(image.shape, dtype=np.uint8)
        scaled = (image.astype(np.float32) - minimum) / (maximum - minimum) * 255.0
        return np.clip(scaled, 0, 255).astype(np.uint8)
    if np.issubdtype(image.dtype, np.floating):
        return cv2.normalize(image.astype(np.float32), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    raise ValueError(f"Unsupported image dtype: {image.dtype}")


def _rotate_by_predicted_orientation(image, angle):
    """Use the exact PaddleOCR orientation mapping validated in Stage 4."""
    if angle == 0:
        return image.copy()
    if angle == 90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if angle == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    raise ValueError(f"Unsupported orientation angle: {angle}")


def _rotate_bound(image, angle):
    height, width = image.shape[:2]
    centre = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int((height * sine) + (width * cosine))
    new_height = int((height * cosine) + (width * sine))
    matrix[0, 2] += (new_width / 2.0) - centre[0]
    matrix[1, 2] += (new_height / 2.0) - centre[1]
    return cv2.warpAffine(
        image, matrix, (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


def _conservative_deskew(gray):
    """Faithful port of Stage 4 estimate_text_skew and rotate_bound."""
    height, width = gray.shape[:2]
    edges = cv2.Canny(gray, 50, 150)
    margin_x = max(5, int(width * 0.035))
    margin_y = max(5, int(height * 0.035))
    edges[:margin_y, :] = 0
    edges[-margin_y:, :] = 0
    edges[:, :margin_x] = 0
    edges[:, -margin_x:] = 0

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=max(35, int(width * 0.045)),
        minLineLength=max(45, int(width * 0.07)),
        maxLineGap=12,
    )
    if lines is None:
        return gray, 0.0

    angles = []
    for line in lines:
        x1, y1, x2, y2 = map(int, line[0])
        dx, dy = x2 - x1, y2 - y1
        length = float(np.hypot(dx, dy))
        if length < max(45, width * 0.07):
            continue
        centre_y = (y1 + y2) / 2.0
        if centre_y < height * 0.07 or centre_y > height * 0.93:
            continue
        angle = float(np.degrees(np.arctan2(dy, dx)))
        if abs(angle) <= 7:
            angles.append(angle)

    if len(angles) < 3:
        return gray, 0.0

    values = np.asarray(angles, dtype=np.float32)
    median_angle = float(np.median(values))
    mad = float(np.median(np.abs(values - median_angle)))
    if not (0.50 <= abs(median_angle) <= 5.0) or mad > 1.20:
        return gray, 0.0
    same_direction_ratio = float(np.mean(np.sign(values) == np.sign(median_angle)))
    if same_direction_ratio < 0.70:
        return gray, 0.0

    # Stage 4 passes the measured median angle directly to rotate_bound.
    height, width = gray.shape
    centre = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, median_angle, 1.0)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(height * sine + width * cosine)
    new_height = int(height * cosine + width * sine)
    matrix[0, 2] += new_width / 2.0 - centre[0]
    matrix[1, 2] += new_height / 2.0 - centre[1]
    rotated = cv2.warpAffine(
        gray,
        matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    return rotated, median_angle


def _ocr_variant(gray):
    # Apply the notebook's OCR-only denoise/contrast/threshold branch.
    residual = cv2.absdiff(gray, cv2.medianBlur(gray, 3)).astype(np.float32)
    local = cv2.blur(gray.astype(np.float32), (7, 7))
    local_sq = cv2.blur(gray.astype(np.float32) ** 2, (7, 7))
    local_std = np.sqrt(np.maximum(local_sq - local ** 2, 0))
    smooth = residual[local_std < 12]
    noise_sigma = float(np.median(smooth) * 1.4826) if smooth.size else 0.0
    working = cv2.medianBlur(gray, 3) if noise_sigma >= 8 else gray.copy()
    if float(np.std(working)) < 35:
        working = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(working)
    block = min(35, min(working.shape) if min(working.shape) % 2 else min(working.shape)-1)
    if block >= 3:
        if block % 2 == 0:
            block -= 1
        working = cv2.adaptiveThreshold(
            working, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, block, 11,
        )
    return working, noise_sigma


def preprocess_bgr(image, output_dir, stem):
    """Write canonical greyscale and OCR-ready PNGs; return paths and audit metadata."""
    output_dir=Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("Could not decode document page image.")
    image = _to_uint8(image)
    if image.ndim == 2:
        bgr=cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        bgr=cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    elif image.ndim == 3 and image.shape[2] == 3:
        bgr=image.copy()
    else:
        raise ValueError(f"Unsupported image shape: {image.shape}")

    angle, confidence = _predict_orientation(bgr)
    try:
        from flask import current_app
        confidence_threshold = float(current_app.config.get("ORIENTATION_CONFIDENCE_THRESHOLD", 0.80))
    except RuntimeError:
        confidence_threshold = 0.80
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("ORIENTATION_CONFIDENCE_THRESHOLD must be between 0 and 1.")

    applied_orientation = 0
    if angle in {90, 180, 270} and confidence >= confidence_threshold:
        candidate=_rotate_by_predicted_orientation(bgr, angle)
        verified_angle, verified_confidence = _predict_orientation(candidate)
        if verified_angle == 0 and verified_confidence >= confidence_threshold:
            bgr=candidate
            applied_orientation=angle

    gray=cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray, deskew_angle=_conservative_deskew(gray)
    canonical=output_dir/f"{stem}_canonical.png"
    ocr_path=output_dir/f"{stem}_ocr.png"
    if not cv2.imwrite(str(canonical), gray):
        raise OSError(f"Unable to write canonical image: {canonical}")
    ocr_image, noise_sigma=_ocr_variant(gray)
    if not cv2.imwrite(str(ocr_path), ocr_image):
        raise OSError(f"Unable to write OCR image: {ocr_path}")
    return canonical, ocr_path, {
        "canonical_image": canonical.name,
        "ocr_image": ocr_path.name,
        "predicted_orientation_deg": angle,
        "orientation_confidence": round(confidence, 6),
        "applied_rotation_clockwise_deg": applied_orientation,
        "applied_deskew_deg": round(deskew_angle, 4),
        "noise_sigma": round(noise_sigma, 4),
        "status": "processed",
    }


def preprocess_image_file(path, output_dir, stem="page"):
    path=Path(path)
    raw=np.fromfile(str(path), dtype=np.uint8)
    image=cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    return preprocess_bgr(image, output_dir, stem)
