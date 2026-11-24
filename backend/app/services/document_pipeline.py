
import csv
import json
import math
import re
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree

import fitz
from flask import current_app

from .field_extractors import (
    FORM_FIELD_LABEL_PATTERNS,
    extract_document_fields,
    build_field_provenance,
)



def _record_ocr_confidences(metrics, scores, page_number=None):
    """Aggregate valid PaddleOCR token scores without inventing missing values."""
    if not isinstance(metrics, dict) or scores is None:
        return
    if hasattr(scores, "tolist"):
        scores = scores.tolist()

    flattened = []
    def collect(value):
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
            return
        try:
            score = float(value)
        except (TypeError, ValueError, OverflowError):
            return
        if math.isfinite(score) and 0.0 <= score <= 1.0:
            flattened.append(score)

    collect(scores)
    if not flattened:
        return

    try:
        previous_count = int(metrics.get("ocr_confidence_count", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        previous_count = 0
    previous_mean = metrics.get("ocr_confidence_mean")
    try:
        previous_mean = float(previous_mean) if previous_mean is not None else None
    except (TypeError, ValueError, OverflowError):
        previous_mean = None
    if previous_count < 0:
        previous_count = 0
    previous_sum = (previous_mean * previous_count) if previous_mean is not None and math.isfinite(previous_mean) else 0.0

    combined_count = previous_count + len(flattened)
    combined_sum = previous_sum + sum(flattened)
    metrics["ocr_confidence_count"] = combined_count
    metrics["ocr_confidence_mean"] = round(combined_sum / combined_count, 6)
    previous_min = metrics.get("ocr_confidence_min")
    try:
        previous_min = float(previous_min) if previous_min is not None else None
    except (TypeError, ValueError, OverflowError):
        previous_min = None
    metrics["ocr_confidence_min"] = round(min(flattened + ([previous_min] if previous_min is not None and math.isfinite(previous_min) else [])), 6)
    metrics["ocr_confidence_source"] = "PaddleOCR token recognition scores"
    metrics["ocr_confidence_status"] = "available"

    # Keep page-level aggregates so mixed-PDF logical documents are assessed
    # against their own source pages, not the confidence of unrelated segments.
    if page_number is not None:
        try:
            page_key = str(int(page_number))
        except (TypeError, ValueError, OverflowError):
            page_key = None
        if page_key is not None:
            by_page = metrics.setdefault("ocr_confidence_by_page", {})
            if isinstance(by_page, dict):
                page_entry = by_page.setdefault(page_key, {"count": 0, "mean": None, "minimum": None})
                try:
                    page_count = max(0, int(page_entry.get("count") or 0))
                except (TypeError, ValueError, OverflowError):
                    page_count = 0
                try:
                    page_mean = float(page_entry.get("mean")) if page_entry.get("mean") is not None else None
                except (TypeError, ValueError, OverflowError):
                    page_mean = None
                page_sum = page_mean * page_count if page_mean is not None and math.isfinite(page_mean) else 0.0
                page_total = page_count + len(flattened)
                page_entry["count"] = page_total
                page_entry["mean"] = round((page_sum + sum(flattened)) / page_total, 6)
                try:
                    page_minimum = float(page_entry.get("minimum")) if page_entry.get("minimum") is not None else None
                except (TypeError, ValueError, OverflowError):
                    page_minimum = None
                page_entry["minimum"] = round(min(flattened + ([page_minimum] if page_minimum is not None and math.isfinite(page_minimum) else [])), 6)


def _ocr_box_bounds(box):
    """Convert PaddleOCR rectangle or polygon coordinates to x/y bounds."""
    if hasattr(box, "tolist"):
        box = box.tolist()

    if not isinstance(box, (list, tuple)):
        return None

    try:
        if len(box) == 4 and all(
            not isinstance(value, (list, tuple)) for value in box
        ):
            x0, y0, x1, y1 = (float(value) for value in box)
        else:
            points = []
            for point in box:
                if hasattr(point, "tolist"):
                    point = point.tolist()
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    points.append((float(point[0]), float(point[1])))

            if len(points) < 3:
                return None

            x0 = min(point[0] for point in points)
            y0 = min(point[1] for point in points)
            x1 = max(point[0] for point in points)
            y1 = max(point[1] for point in points)

        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            return None

        if x1 <= x0 or y1 <= y0:
            return None

        return x0, y0, x1, y1

    except (TypeError, ValueError, OverflowError):
        return None


def _order_ocr_texts_by_geometry(texts, boxes):
    """Order PaddleOCR text regions top-to-bottom and left-to-right within lines.

    Retain the original OCR order when the geometry is unavailable or invalid.
    """
    if not isinstance(texts, (list, tuple)):
        return []

    original = [
        str(item).strip() if item else ""
        for item in texts
    ]
    fallback = [item for item in original if item]

    if len(fallback) < 2:
        return fallback

    if hasattr(boxes, "tolist"):
        boxes = boxes.tolist()

    if not isinstance(boxes, (list, tuple)) or len(boxes) != len(texts):
        return fallback

    records = []

    for index, text in enumerate(original):
        if not text:
            continue

        bounds = _ocr_box_bounds(boxes[index])

        if bounds is None:
            return fallback

        x0, y0, x1, y1 = bounds

        records.append({
            "text": text,
            "x0": x0,
            "x1": x1,
            "y0": y0,
            "y1": y1,
            "height": y1 - y0,
            "center_y": (y0 + y1) / 2.0,
        })

    if len(records) < 2:
        return fallback

    rows = []

    for record in sorted(records, key=lambda item: (item["center_y"], item["x0"])):
        best_row = None
        best_score = float("-inf")

        for row in rows:
            overlap = max(
                0.0,
                min(record["y1"], row["y1"])
                - max(record["y0"], row["y0"]),
            )

            minimum_height = min(record["height"], row["y1"] - row["y0"])
            overlap_ratio = overlap / minimum_height if minimum_height > 0 else 0.0

            row_height = row["y1"] - row["y0"]
            row_center = (row["y0"] + row["y1"]) / 2.0
            center_distance = abs(record["center_y"] - row_center)
            allowed_distance = 0.45 * max(record["height"], row_height)

            if overlap_ratio >= 0.35 or center_distance <= allowed_distance:
                distance_score = center_distance / max(allowed_distance, 1.0)
                score = overlap_ratio - (distance_score * 0.05)

                if score > best_score:
                    best_score = score
                    best_row = row

        if best_row is None:
            rows.append({
                "y0": record["y0"],
                "y1": record["y1"],
                "items": [record],
            })
        else:
            best_row["items"].append(record)
            best_row["y0"] = min(best_row["y0"], record["y0"])
            best_row["y1"] = max(best_row["y1"], record["y1"])

    rows.sort(key=lambda row: (row["y0"], row["y1"]))

    # A row-wise OCR order interleaves the left and right columns of a resume.
    # Detect a genuine two-column resume using both a wide x-coordinate gutter
    # and section-heading evidence on each side. Other documents (especially
    # invoice tables) retain the existing row-wise output below.
    resume_heading_aliases = {
        "contact", "contact details", "technical skills", "technical and professional skills",
        "professional and technical skills", "core competencies", "key skills", "skills",
        "education", "educational qualifications", "academic qualifications", "qualifications",
        "certifications", "professional certifications", "certificates", "licenses",
        "professional summary", "profile summary", "career summary", "summary", "career objective",
        "objective", "profile", "work experience", "professional experience", "employment history",
        "work history", "experience", "projects", "academic projects", "personal projects",
        "achievements", "academic achievements", "academic awards", "honours and awards",
        "honors and awards", "awards", "languages", "internships", "publications",
    }
    x_starts = sorted(record["x0"] for record in records)
    x_span = max(record["x1"] for record in records) - min(record["x0"] for record in records)
    min_column_records = max(3, int(len(records) * 0.12))
    candidate_gaps = []
    for gap_index in range(min_column_records - 1, len(x_starts) - min_column_records):
        gap = x_starts[gap_index + 1] - x_starts[gap_index]
        if gap >= max(40.0, x_span * 0.10):
            candidate_gaps.append((gap, gap_index))

    resume_split_x = None
    if candidate_gaps:
        _, gap_index = max(candidate_gaps)
        candidate_split = (x_starts[gap_index] + x_starts[gap_index + 1]) / 2.0
        heading_evidence = {0: set(), 1: set()}
        for record in records:
            normalized = re.sub(r"[^a-z0-9 ]", "", record["text"].lower()).strip()
            if normalized in resume_heading_aliases:
                side = 0 if record["x0"] < candidate_split else 1
                heading_evidence[side].add(normalized)

        combined_headings = heading_evidence[0] | heading_evidence[1]
        left_resume_headings = {
            "technical skills", "technical and professional skills", "professional and technical skills",
            "core competencies", "skills", "education", "educational qualifications",
            "academic qualifications", "certifications", "professional certifications",
        }
        right_resume_headings = {
            "professional summary", "profile summary", "summary", "work experience",
            "professional experience", "employment history", "experience", "projects",
            "academic projects", "achievements", "academic achievements", "awards",
        }
        has_resume_structure = (
            len(heading_evidence[0]) >= 2
            and len(heading_evidence[1]) >= 2
            and bool(combined_headings & left_resume_headings)
            and bool(combined_headings & right_resume_headings)
            and len(combined_headings) >= 4
        )

        if has_resume_structure:
            resume_split_x = candidate_split

    if resume_split_x is not None:
        column_lines = {0: [], 1: []}
        for row in rows:
            row_columns = {0: [], 1: []}
            for record in row["items"]:
                side = 0 if record["x0"] < resume_split_x else 1
                row_columns[side].append(record)

            for side in (0, 1):
                if row_columns[side]:
                    cell_text = " ".join(
                        record["text"]
                        for record in sorted(row_columns[side], key=lambda item: item["x0"])
                    ).strip()
                    if cell_text:
                        column_lines[side].append(cell_text)

        column_major = column_lines[0] + column_lines[1]
        if column_major:
            return column_major

    ordered = []

    for row in rows:
        row_texts = [
            record["text"]
            for record in sorted(
                row["items"],
                key=lambda item: item["x0"],
            )
        ]

        if row_texts:
            # Tabs preserve visual table columns for downstream extraction.
            ordered.append("\t".join(row_texts))

    return ordered if ordered else fallback
    # OCR_ROW_COLUMNS_PRESERVED
    # OCR_TWO_COLUMN_RESUME_READ_ORDER

# =========================================================
# PADDLE OCR
# =========================================================

def _paddle_ocr(image_path, timing_metrics=None, page_number=None):
    """Extract text using PaddleOCR 2.x or 3.x."""

    if not hasattr(_paddle_ocr, "engine"):
        initialization_started = time.perf_counter()
        from paddleocr import PaddleOCR
        lang = current_app.config.get("PADDLEOCR_LANG", "en")
        # Orientation and deskew are already handled by Stage 4 preprocessing,
        # so PaddleOCR's additional per-page orientation/unwarping models are
        # disabled. PaddleOCR 2.x falls back to its supported constructor.
        try:
            _paddle_ocr.engine = PaddleOCR(
                lang=lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        except TypeError:
            _paddle_ocr.engine = PaddleOCR(lang=lang)
        finally:
            if timing_metrics is not None:
                timing_metrics["ocr_model_load_ms"] = round(
                    float(timing_metrics.get("ocr_model_load_ms", 0.0))
                    + (time.perf_counter() - initialization_started) * 1000,
                    3,
                )

    engine = _paddle_ocr.engine
    started = time.perf_counter()
    found = []
    if timing_metrics is not None:
        timing_metrics["ocr_page_count"] = int(timing_metrics.get("ocr_page_count", 0)) + 1

    # PaddleOCR 3.x
    if hasattr(engine, "predict"):
        try:
            results = engine.predict(str(image_path))

            for result in results or []:
                payload = getattr(result, "json", None)

                if callable(payload):
                    payload = payload()

                if payload is None and isinstance(result, dict):
                    payload = result

                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {}

                if isinstance(payload, dict):
                    content = payload.get("res", payload)

                    if isinstance(content, dict):
                        texts = content.get("rec_texts", [])
                        _record_ocr_confidences(
                            timing_metrics,
                            content.get("rec_scores"),
                            page_number=page_number,
                        )
                        boxes = content.get("rec_boxes")

                        if boxes is None:
                            boxes = content.get("rec_polys")

                        found.extend(
                            _order_ocr_texts_by_geometry(texts, boxes)
                        )

        except Exception:
            current_app.logger.exception(
                "PaddleOCR 3.x prediction failed"
            )

    # PaddleOCR 2.x compatibility.
    if not found and hasattr(engine, "ocr"):
        try:
            try:
                results = engine.ocr(
                    str(image_path),
                    cls=True
                )
            except TypeError:
                results = engine.ocr(str(image_path))

            for page in results or []:
                for item in page or []:
                    if (
                        isinstance(item, (list, tuple))
                        and len(item) > 1
                        and isinstance(item[1], (list, tuple))
                        and item[1]
                    ):
                        found.append(str(item[1][0]))
                        if len(item[1]) > 1:
                            _record_ocr_confidences(timing_metrics, [item[1][1]], page_number=page_number)

        except Exception:
            current_app.logger.exception(
                "PaddleOCR legacy prediction failed"
            )

    if timing_metrics is not None:
        timing_metrics["ocr_ms"] = round(
            float(timing_metrics.get("ocr_ms", 0.0)) + (time.perf_counter() - started) * 1000,
            3,
        )
    return "\n".join(found)


# =========================================================
# PDF TEXT AND OCR MERGING
# =========================================================

def _merge_native_and_ocr_text(native_text, ocr_text):
    """Merge native and OCR text without flattening source paragraph breaks."""

    native_text = (native_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    ocr_text = (ocr_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    if not native_text:
        return ocr_text
    if not ocr_text:
        return native_text

    def normalize(line):
        return re.sub(r"\s+", " ", line).strip().casefold()

    native_keys = {
        normalize(line)
        for line in native_text.splitlines()
        if line.strip()
    }

    additional_lines = []
    seen_ocr_only = set()
    for line in ocr_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key = normalize(stripped)
        if key and key not in native_keys and key not in seen_ocr_only:
            additional_lines.append(stripped)
            seen_ocr_only.add(key)

    if not additional_lines:
        return native_text

    # Preserve native paragraph/line layout verbatim; append only unique OCR lines.
    return native_text.rstrip() + "\n\n" + "\n".join(additional_lines)



def _page_has_substantial_raster_content(page, threshold=0.12):
    """Return whether raster images occupy a meaningful part of a PDF page.

    Small logos, signatures and decorative marks do not trigger OCR on a page
    that already contains usable native text. A scanned page or a large embedded
    image still triggers OCR. Image rectangles are unioned to avoid counting
    overlapping images more than once.
    """
    page_rect = page.rect
    page_area = float(page_rect.width * page_rect.height)
    if page_area <= 0:
        return False

    try:
        image_info = page.get_image_info(xrefs=True)
    except Exception:
        # Retain the conservative fallback if this PyMuPDF build cannot
        # provide image bounding boxes.
        return bool(page.get_images(full=True))

    rectangles = []
    for item in image_info or []:
        bbox = item.get("bbox") if isinstance(item, dict) else None
        if not bbox:
            continue
        try:
            rect = fitz.Rect(bbox) & page_rect
        except (TypeError, ValueError):
            continue
        if not rect.is_empty and rect.width > 0 and rect.height > 0:
            rectangles.append(rect)

    if not rectangles:
        return False

    # Sweep the x-axis and merge y-intervals for each strip.
    x_edges = sorted({edge for rect in rectangles for edge in (rect.x0, rect.x1)})
    union_area = 0.0
    for x0, x1 in zip(x_edges, x_edges[1:]):
        strip_width = x1 - x0
        if strip_width <= 0:
            continue
        intervals = sorted(
            (rect.y0, rect.y1)
            for rect in rectangles
            if rect.x0 < x1 and rect.x1 > x0
        )
        if not intervals:
            continue
        covered_y = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                covered_y += end - start
                start, end = next_start, next_end
        covered_y += end - start
        union_area += strip_width * covered_y

    return (union_area / page_area) >= threshold

def extract_pdf_pages(path, artifacts_dir=None, timing_metrics=None, enable_ocr=True):
    """Extract PDF text and optionally write canonical/OCR image variants.

    When artifacts_dir is provided, every page is rendered and passed through
    Stage 4 orientation/deskew preprocessing so EfficientNet sees the same
    canonical grayscale representation used during training.
    """
    pages = []
    manifest = []
    root = Path(artifacts_dir) if artifacts_dir else None
    if root:
        root.mkdir(parents=True, exist_ok=True)
    from .preprocessing import preprocess_image_file

    with fitz.open(path) as pdf:
        for page_index, page in enumerate(pdf, start=1):
            native_started = time.perf_counter()
            native_text = page.get_text("text") or ""

            # Read external hyperlink targets. Some resumes show a
            # clickable label but do not print its destination URL.
            pdf_link_uris = []
            try:
                page_links = page.get_links() or []
            except Exception:
                page_links = []

            for link in page_links:
                uri = str(link.get("uri") or "").strip()
                if (
                    uri.lower().startswith(("http://", "https://"))
                    and uri not in pdf_link_uris
                ):
                    pdf_link_uris.append(uri)

            if timing_metrics is not None:
                timing_metrics["native_text_ms"] = round(
                    float(timing_metrics.get("native_text_ms", 0.0)) + (time.perf_counter() - native_started) * 1000,
                    3,
                )
            has_substantial_image = _page_has_substantial_raster_content(page)
            should_run_ocr = bool(enable_ocr) and (
                len(native_text.strip()) < 25
                or has_substantial_image
            )
            if timing_metrics is not None and not should_run_ocr:
                if not enable_ocr:
                    counter = "ocr_disabled_page_count"
                else:
                    counter = "ocr_skipped_native_page_count"
                timing_metrics[counter] = int(timing_metrics.get(counter, 0)) + 1
            if root:
                raw_path = root / f"page_{page_index:04d}_render.png"
                render_started = time.perf_counter()
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                pix.save(str(raw_path))
                if timing_metrics is not None:
                    timing_metrics["pdf_render_ms"] = round(
                        float(timing_metrics.get("pdf_render_ms", 0.0))
                        + (time.perf_counter() - render_started) * 1000,
                        3,
                    )
                preprocessing_started = time.perf_counter()
                canonical, ocr_image, metadata = preprocess_image_file(
                    raw_path, root, f"page_{page_index:04d}"
                )
                if timing_metrics is not None:
                    timing_metrics["preprocessing_ms"] = round(
                        float(timing_metrics.get("preprocessing_ms", 0.0)) + (time.perf_counter() - preprocessing_started) * 1000,
                        3,
                    )
                raw_path.unlink(missing_ok=True)
                manifest.append(metadata)
                if should_run_ocr:
                    ocr_text = _paddle_ocr(str(ocr_image), timing_metrics=timing_metrics, page_number=page_index)
                    extracted_text = _merge_native_and_ocr_text(native_text, ocr_text)
                else:
                    extracted_text = native_text.strip()
            elif should_run_ocr:
                with tempfile.TemporaryDirectory() as temp_dir:
                    image_path = Path(temp_dir) / "page.png"
                    render_started = time.perf_counter()
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    pix.save(str(image_path))
                    if timing_metrics is not None:
                        timing_metrics["pdf_render_ms"] = round(
                            float(timing_metrics.get("pdf_render_ms", 0.0))
                            + (time.perf_counter() - render_started) * 1000,
                            3,
                        )
                    ocr_text = _paddle_ocr(str(image_path), timing_metrics=timing_metrics, page_number=page_index)
                extracted_text = _merge_native_and_ocr_text(native_text, ocr_text)
            else:
                extracted_text = native_text.strip()
            if pdf_link_uris:
                link_text = "\n".join(pdf_link_uris)
                extracted_text = "\n".join(
                    part
                    for part in (extracted_text.strip(), link_text)
                    if part
                )

            pages.append(extracted_text)
    if root:
        (root / "preprocessing_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return pages


def extract_pdf(path):
    """Combine PDF page text while preserving page order."""

    return "\n\n".join(
        extract_pdf_pages(path)
    )


# =========================================================
# DOCUMENT PAGE EXTRACTION
# =========================================================

def extract_document_pages(path, extension, artifacts_dir=None, timing_metrics=None, enable_ocr=True):
    """Return PDF pages or one logical page for other formats."""

    ext = extension.lower().lstrip(".")

    if ext == "pdf":
        return extract_pdf_pages(
            path,
            artifacts_dir=artifacts_dir,
            timing_metrics=timing_metrics,
            enable_ocr=enable_ocr,
        )

    if ext in {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"} and artifacts_dir:
        from .preprocessing import preprocess_image_file
        preprocessing_started = time.perf_counter()
        canonical, ocr_image, metadata = preprocess_image_file(path, artifacts_dir, "page_0001")
        if timing_metrics is not None:
            timing_metrics["preprocessing_ms"] = round(
                float(timing_metrics.get("preprocessing_ms", 0.0)) + (time.perf_counter() - preprocessing_started) * 1000,
                3,
            )
        text = _paddle_ocr(str(ocr_image), timing_metrics=timing_metrics, page_number=1)
        (Path(artifacts_dir) / "preprocessing_manifest.json").write_text(
            json.dumps([metadata], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return [text]

    native_started = time.perf_counter()
    extracted = extract_document(path, ext)
    if timing_metrics is not None:
        timing_metrics["native_text_ms"] = round(
            float(timing_metrics.get("native_text_ms", 0.0)) + (time.perf_counter() - native_started) * 1000,
            3,
        )
    return [extracted]


def extract_image(path):
    """Extract text from an image."""

    return _paddle_ocr(path)


def extract_document(path, extension):
    """Extract text from supported document formats."""

    ext = extension.lower().lstrip(".")

    if ext == "pdf":
        return extract_pdf(path)

    if ext in {
        "png",
        "jpg",
        "jpeg",
        "tif",
        "tiff",
        "bmp",
        "webp"
    }:
        return extract_image(path)

    if ext in {"txt", "md", "rtf"}:

        return Path(path).read_text(
            encoding="utf-8",
            errors="replace"
        )

    if ext == "csv":

        with open(
            path,
            encoding="utf-8-sig",
            errors="replace",
            newline=""
        ) as handle:

            return "\n".join(
                ", ".join(row)
                for row in csv.reader(handle)
            )

    if ext == "json":

        value = json.loads(
            Path(path).read_text(
                encoding="utf-8",
                errors="replace"
            )
        )

        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str
        )

    if ext == "bson":

        from bson import BSON

        value = BSON(
            Path(path).read_bytes()
        ).decode()

        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str
        )

    if ext == "xml":

        root = ElementTree.parse(path).getroot()

        return "\n".join(
            item.strip()
            for item in root.itertext()
            if item.strip()
        )

    if ext == "docx":

        from docx import Document

        doc = Document(path)

        chunks = [
            paragraph.text
            for paragraph in doc.paragraphs
            if paragraph.text.strip()
        ]

        for table in doc.tables:
            for row in table.rows:

                chunks.append(
                    " | ".join(
                        cell.text.strip()
                        for cell in row.cells
                    )
                )

        return "\n".join(chunks)

    if ext in {"xlsx", "xlsm"}:

        from openpyxl import load_workbook

        workbook = load_workbook(
            path,
            read_only=True,
            data_only=True
        )

        chunks = []

        try:
            for sheet in workbook.worksheets:

                chunks.append(
                    f"Sheet: {sheet.title}"
                )

                for row in sheet.iter_rows(values_only=True):

                    values = [
                        str(value)
                        for value in row
                        if value is not None
                    ]

                    if values:
                        chunks.append(
                            " | ".join(values)
                        )

        finally:
            workbook.close()

        return "\n".join(chunks)

    if ext == "pptx":

        from pptx import Presentation

        presentation = Presentation(path)

        return "\n".join(
            shape.text
            for slide in presentation.slides
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False)
            and shape.text.strip()
        )

    raise ValueError(
        f"Unsupported document type: .{ext}"
    )


# =========================================================
# GENERIC FIELD EXTRACTION
# =========================================================

def extract_key_fields(text):
    """
    Extract generic fields from document text.

    Document-specific extraction is handled separately.
    """

    text = text or ""

    patterns = {

        "emails": (
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
        ),

        "phone_numbers": (
            r"(?<!\d)(?:\+91[\s.-]?)?"
            r"[6-9]\d{4}[\s.-]?\d{5}(?!\d)"
        ),

        "dates": (
            r"\b(?:"
            r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
            r"|\d{4}-\d{2}-\d{2}"
            r"|(?:0?[1-9]|[12]\d|3[01])\s+"
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|"
            r"Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
            r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
            r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}"
            r")\b"
        ),

        "amounts": (
            r"(?:₹|INR|USD|\$|€)\s?"
            r"\d[\d,]*(?:\.\d{1,2})?"
        ),

        "invoice_numbers": (
            r"(?:\binvoice\s*(?:number|no\.?|#)"
            r"\s*[:#.-]?\s*"
            r"([A-Z0-9][A-Z0-9/-]{2,})"
            r"|\b(INV(?:[-/]\s*|\d)"
            r"[A-Z0-9/-]{2,})\b)"
        ),

        "gstin": (
            r"\b\d{2}[A-Z]{5}\d{4}[A-Z]"
            r"[A-Z0-9]Z[A-Z0-9]\b"
        ),
    }

    result = {}

    for key, pattern in patterns.items():

        values = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if key == "invoice_numbers":

            cleaned = [
                str(
                    next(
                        (
                            part
                            for part in value
                            if part
                        ),
                        ""
                    )
                ).strip()
                for value in values
            ]

        else:

            cleaned = [
                str(value).strip()
                for value in values
            ]

        cleaned = [
            value
            for value in cleaned
            if value
        ]

        if key == "invoice_numbers":
            cleaned = [
                value
                for value in cleaned
                if re.fullmatch(
                    r"[A-Z0-9][A-Z0-9/-]{2,}",
                    value,
                    flags=re.IGNORECASE,
                )
                and re.search(r"\d", value)
            ]

        if cleaned:

            result[key] = list(
                dict.fromkeys(cleaned)
            )[:20]

    return result


# =========================================================
# INSIGHTS AND DOCUMENT-SPECIFIC EXTRACTION
# =========================================================

def _normalize_extracted_value(value):
    """Normalize nested OCR-derived values without flattening their structure."""
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        value = value.replace("\u00a0", " ").replace("\u200b", "")
        value = value.replace("\ufeff", "")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\s*\n\s*", "\n", value)
        value = value.strip()
        value = re.sub(r"^[????]+\s*", "", value)
        return value or None

    if isinstance(value, dict):
        result = {}

        for key, item in value.items():
            normalized = _normalize_extracted_value(item)

            if normalized is None:
                continue

            if isinstance(normalized, (str, list, dict)) and not normalized:
                continue

            clean_key = str(key).strip()

            if clean_key:
                result[clean_key] = normalized

        return result or None

    if isinstance(value, (list, tuple)):
        result = []
        seen = set()

        for item in value:
            normalized = _normalize_extracted_value(item)

            if normalized is None:
                continue

            if isinstance(normalized, (dict, list)):
                token = json.dumps(
                    normalized,
                    sort_keys=True,
                    ensure_ascii=False,
                    default=str,
                )
            else:
                token = repr((type(normalized).__name__, normalized))

            if token not in seen:
                seen.add(token)
                result.append(normalized)

        return result or None

    if isinstance(value, float) and not math.isfinite(value):
        return None

    return value


def _normalize_extracted_fields(fields):
    """Apply consistent whitespace, empty-value and duplicate handling."""
    if not isinstance(fields, dict):
        return {}

    normalized = _normalize_extracted_value(fields)

    return normalized if isinstance(normalized, dict) else {}


def build_insights(text, classification, user_settings=None, source_pages=None, ocr_metrics=None):
    """
    Generate generic and document-specific insights.

    ``ocr_metrics`` is the timing/recognition metrics dictionary populated by
    ``extract_document_pages``. When source-page metadata is supplied, OCR
    confidence is scoped to those pages so mixed-upload segments are not
    evaluated using unrelated documents' recognition scores.

    Preserves the existing insights.key_fields API contract.
    """

    text = text or ""

    # Generate document summary.
    sentences = re.split(
        r"(?<=[.!?])\s+",
        re.sub(r"\s+", " ", text).strip()
    )

    summary = " ".join(
        sentence
        for sentence in sentences[:3]
        if sentence
    )[:700]

    # Generic field extraction.
    fields = extract_key_fields(text)

    # Document-specific field extraction.
    specific_extraction_failed = False
    specific_fields = {}
    try:

        specific_fields = extract_document_fields(
            text,
            classification
        )

        if isinstance(specific_fields, dict):

            fields.update(specific_fields)

    except Exception:
        specific_extraction_failed = True
        current_app.logger.exception(
            "Document-specific field extraction failed"
        )

    fields = _normalize_extracted_fields(fields)
    specific_fields = _normalize_extracted_fields(specific_fields)
    summary = _normalize_extracted_value(summary) or ""

    label = classification.get("label", "") if isinstance(classification, dict) else str(classification or "")
    expected_fields = {
        "invoice": ["invoice_number", "invoice_date", "seller_company_details", "buyer_customer_details", "email_addresses", "phone_numbers", "gstin", "line_items", "subtotal", "tax_amount", "grand_total"],
        # Resume expectations are populated dynamically from sections and
        # contact evidence actually present in the source text below. Optional
        # nested values (employer/job title/institution) are not double-counted
        # as nonexistent top-level fields.
        "resume": [],
        "form": [],
    }

    category = str(label).lower().strip()

    def present(value):
        if isinstance(value, dict):
            return any(present(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(present(item) for item in value)
        return value is not None and str(value).strip() != "" and value is not False

    aliases = {
        "email_addresses": ["emails", "email"],
        "phone_numbers": ["phone"],
        "technical_skills": ["skills"],
        "work_experience": ["experience"],
        "educational_qualifications": ["education"],
        "projects": ["projects"],
        "certifications": ["certifications"],
        "professional_summary": ["summary"],
        "address": ["location_address"],
        "form_questions_answers": ["purpose_reason"],
        "applicant_name": ["full_name", "name_of_applicant"],
        "date_of_birth": ["dob"],
    }

    if category == "resume":
        required = ["candidate_name"]
        resume_section_signals = {
            "professional_summary": r"professional\s+summary|profile\s+summary|career\s+summary|summary|career\s+objective|objective|profile",
            "technical_skills": r"technical\s+skills|technical\s+and\s+professional\s+skills|professional\s+and\s+technical\s+skills|core\s+competencies|key\s+skills|skills|technologies",
            "work_experience": r"professional\s+experience|work\s+experience|employment\s+history|work\s+history|experience",
            "educational_qualifications": r"educational\s+qualifications|academic\s+qualifications|education|qualifications",
            "projects": r"academic\s+projects|personal\s+projects|projects",
            "certifications": r"professional\s+certifications|certifications|certificates|licenses|certifications\s+(?:and|&)\s+achievements|certifications\s+(?:and|&)\s+languages",
            "academic_achievements": r"academic\s+achievements|academic\s+awards|achievements|honours\s+and\s+awards|honors\s+and\s+awards|awards",
            "internships": r"internships|internship\s+experience|internship",
            "languages": r"languages|language\s+proficiency|spoken\s+languages",
            "publications": r"publications|research\s+publications|papers",
            "volunteer_experience": r"volunteer\s+experience|volunteering|community\s+service",
            "extracurricular_activities": r"extracurricular\s+activities|extra\s+curricular\s+activities|activities",
            "interests": r"interests|hobbies\s+and\s+interests|hobbies",
            "references": r"references|professional\s+references|references\s+available\s+upon\s+request",
        }
        for field_name, heading_pattern in resume_section_signals.items():
            if re.search(rf"(?im)^\s*(?:{heading_pattern})\s*(?:[:|].*)?$", text or ""):
                required.append(field_name)

        if re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text or "", re.IGNORECASE):
            required.append("emails")
        if re.search(r"(?<!\d)(?:\+?91[\s.-]?)?[6-9]\d{4}[\s.-]?\d{5}(?!\d)", text or ""):
            required.append("phone_numbers")
        if (
            re.search(r"(?im)^\s*(?:location|current\s+location|address|city|permanent\s+address)\s*[:#-]", text or "")
            or present(specific_fields.get("location_address"))
        ):
            required.append("location_address")
        profile_domains = {
            "linkedin": r"linkedin\.com/",
            "github": r"github\.com/",
            "portfolio": r"(?:behance\.net/|https?://|www\.)",
        }
        for field_name, domain_pattern in profile_domains.items():
            if re.search(domain_pattern, text or "", re.IGNORECASE):
                required.append(field_name)
        required = list(dict.fromkeys(required))

    elif category == "form":
        form_label_patterns = FORM_FIELD_LABEL_PATTERNS

        required = []

        first_lines = (text or "").splitlines()[:8]
        if any(
            re.search(
                r"\b(form|application|registration|admission|claim|declaration|survey|account\s+intake|customer\s+onboarding|client\s+onboarding|account\s+opening)\b",
                line,
                re.IGNORECASE,
            )
            for line in first_lines
        ):
            required.append("form_title_type")

        for field_name, label_pattern in form_label_patterns.items():
            line_pattern = (
                rf"(?im)^\s*\b(?:{label_pattern})\b"
                rf"\s*(?:[:#-]\s*.*)?$"
            )
            if re.search(line_pattern, text or ""):
                required.append(field_name)

        if re.search(
            r"(?im)^\s*(?:purpose\s*/?\s*reason|reason\s*for\s*application|purpose|question(?:\s*\d+)?|answer|remarks)\s*[:#?-]",
            text or "",
        ):
            required.append("form_questions_answers")

        if re.search(r"(?:\[\s*[xX✓✔]\s*\]|☒|☑)\s*[A-Za-z]", text or ""):
            required.append("selected_checkbox_radio_options")

        required = list(dict.fromkeys(required))
    else:
        required = expected_fields.get(category, [])

    extracted = []

    for key in required:
        candidates = [key] + aliases.get(key, [])
        if any(present(fields.get(candidate)) for candidate in candidates):
            extracted.append(key)

    missing_fields = [key for key in required if key not in extracted]
    form_missing_fields = missing_fields if category == "form" else []
    coverage = round((len(extracted) / len(required)) * 100, 2) if required else 0.0

    validation_issues = []
    if category == "resume":
        candidate_name = fields.get("candidate_name")
        candidate_name_text = str(candidate_name or "").strip()
        name_is_plausible = bool(re.fullmatch(
            r"[A-Za-z][A-Za-z.'’\-]*(?:\s+[A-Za-z][A-Za-z.'’\-]*){1,4}",
            candidate_name_text,
        ))
        name_has_descriptor = bool(re.search(
            r"(?i)\b(?:graduate|undergraduate|postgraduate|student|fresher|"
            r"aspiring|seeking|bachelor|master(?:'s)?|statistics|"
            r"machine\s+learning|data\s+(?:analysis|analytics|science)|"
            r"programming|visualization|automation|reporting|model\s+evaluation)\b",
            candidate_name_text,
        ))
        if not candidate_name_text:
            validation_issues.append({
                "code": "resume_candidate_name_missing",
                "message": "Candidate name was not confidently extracted. Verify the resume header manually.",
            })
        elif not name_is_plausible or name_has_descriptor:
            validation_issues.append({
                "code": "resume_candidate_name_suspicious",
                "message": "The extracted candidate name may contain a role, qualification or profile descriptor. Verify it against the resume header.",
            })

    if category == "invoice":
        # Use the extraction layer's explicit component-aware arithmetic checks.
        # A generic subtotal+tax comparison can be wrong when discounts, freight
        # or round-off are separately stated on the source invoice.
        for check_name, check in (fields.get("calculation_validation", {}) or {}).items():
            if isinstance(check, dict) and check.get("status") == "mismatch":
                validation_issues.append({
                    "code": f"invoice_{check_name}_mismatch",
                    "message": f"Invoice {check_name.replace('_', ' ')} calculation does not reconcile with the extracted source values. Verify the invoice manually.",
                    "details": check,
                })

    settings = user_settings if isinstance(user_settings, dict) else {}
    threshold = settings.get("threshold", 70)
    if (
        not isinstance(threshold, (int, float))
        or isinstance(threshold, bool)
        or not math.isfinite(threshold)
    ):
        threshold = 70
    threshold = min(90, max(50, int(threshold)))

    scores = classification.get("scores", []) if isinstance(classification, dict) else []
    score_type = classification.get("score_type") if isinstance(classification, dict) else None
    confidence = None
    # A confidence value is valid only when the visual model's highest-probability
    # class agrees with the final routed class. OCR overrides are rule-based decisions,
    # not calibrated probabilities, so do not relabel the visual confidence as final
    # class confidence.
    if score_type in {"softmax_probability", "mean_page_softmax_probability"} and isinstance(scores, list) and scores and isinstance(scores[0], dict):
        raw_confidence = scores[0].get("score")
        top_visual_label = str(scores[0].get("label", "")).lower()
        if (
            top_visual_label == category
            and isinstance(raw_confidence, (int, float))
            and not isinstance(raw_confidence, bool)
            and math.isfinite(raw_confidence)
            and 0.0 <= float(raw_confidence) <= 1.0
        ):
            confidence = float(raw_confidence)

    confidence_status = "available" if confidence is not None else "unavailable"
    confidence_source = (
        str(classification.get("model") or "visual_classifier")
        + ":" + str(score_type)
        if confidence is not None and isinstance(classification, dict) else None
    )
    confidence_basis = (
        "Visual classifier probability for the final routed category; not field-level OCR confidence."
        if confidence is not None else
        "Unavailable: no valid visual-classifier probability supports the final routed category."
    )
    confidence_unavailable = (
        category in {"resume", "invoice", "form"} and confidence is None
    )

    # OCR recognition confidence is separate from visual document-class
    # confidence. It remains unavailable when native PDF text was used or when
    # the OCR engine does not expose token-level scores.
    ocr_metrics = ocr_metrics if isinstance(ocr_metrics, dict) else {}
    effective_ocr_metrics = dict(ocr_metrics)
    by_page = ocr_metrics.get("ocr_confidence_by_page")
    if isinstance(by_page, dict) and isinstance(source_pages, list):
        selected_entries = []
        for source_page in source_pages:
            if not isinstance(source_page, dict):
                continue
            page_number = source_page.get("page_number")
            if page_number is None:
                continue
            entry = by_page.get(str(page_number), by_page.get(page_number))
            if isinstance(entry, dict) and entry.get("count"):
                selected_entries.append(entry)
        if selected_entries:
            total_tokens = 0
            total_score = 0.0
            minima = []
            for entry in selected_entries:
                try:
                    count = max(0, int(entry.get("count") or 0))
                    mean = float(entry.get("mean"))
                except (TypeError, ValueError, OverflowError):
                    continue
                if count and math.isfinite(mean) and 0.0 <= mean <= 1.0:
                    total_tokens += count
                    total_score += mean * count
                    try:
                        minimum = float(entry.get("minimum"))
                    except (TypeError, ValueError, OverflowError):
                        minimum = None
                    if minimum is not None and math.isfinite(minimum) and 0.0 <= minimum <= 1.0:
                        minima.append(minimum)
            if total_tokens:
                effective_ocr_metrics["ocr_confidence_mean"] = round(total_score / total_tokens, 6)
                effective_ocr_metrics["ocr_confidence_count"] = total_tokens
                effective_ocr_metrics["ocr_confidence_min"] = min(minima) if minima else None
                effective_ocr_metrics["ocr_confidence_source"] = "PaddleOCR token recognition scores for this logical document's source pages"
            else:
                effective_ocr_metrics.pop("ocr_confidence_mean", None)
        else:
            # Do not substitute a mixed-upload mean when this segment has no
            # OCR-scored pages (for example, it used native PDF text).
            effective_ocr_metrics.pop("ocr_confidence_mean", None)
            effective_ocr_metrics.pop("ocr_confidence_count", None)
            effective_ocr_metrics.pop("ocr_confidence_min", None)
    raw_ocr_confidence = effective_ocr_metrics.get("ocr_confidence_mean")
    ocr_confidence = None
    if isinstance(raw_ocr_confidence, (int, float)) and not isinstance(raw_ocr_confidence, bool):
        if math.isfinite(raw_ocr_confidence) and 0.0 <= float(raw_ocr_confidence) <= 1.0:
            ocr_confidence = float(raw_ocr_confidence)
    raw_ocr_threshold = settings.get("ocrThreshold", 0.70)
    if not isinstance(raw_ocr_threshold, (int, float)) or isinstance(raw_ocr_threshold, bool) or not math.isfinite(raw_ocr_threshold):
        raw_ocr_threshold = 0.70
    if raw_ocr_threshold > 1:
        raw_ocr_threshold = raw_ocr_threshold / 100.0
    ocr_threshold = min(1.0, max(0.0, float(raw_ocr_threshold)))
    low_ocr_confidence = ocr_confidence is not None and ocr_confidence < ocr_threshold
    try:
        ocr_token_count = max(0, int(effective_ocr_metrics.get("ocr_confidence_count") or 0))
    except (TypeError, ValueError, OverflowError):
        ocr_token_count = 0
    raw_ocr_minimum = effective_ocr_metrics.get("ocr_confidence_min")
    ocr_minimum = None
    if isinstance(raw_ocr_minimum, (int, float)) and not isinstance(raw_ocr_minimum, bool):
        if math.isfinite(raw_ocr_minimum) and 0.0 <= float(raw_ocr_minimum) <= 1.0:
            ocr_minimum = float(raw_ocr_minimum)
    ocr_confidence_review = {
        "confidence": ocr_confidence,
        "status": "available" if ocr_confidence is not None else "unavailable",
        "source": effective_ocr_metrics.get("ocr_confidence_source") if ocr_confidence is not None else None,
        "basis": (
            "Mean PaddleOCR token recognition score; not visual document-class confidence."
            if ocr_confidence is not None else
            "Unavailable: OCR token recognition scores were not supplied by the OCR engine or no OCR was required."
        ),
        "threshold": ocr_threshold,
        "below_threshold": low_ocr_confidence,
        "token_count": ocr_token_count if ocr_confidence is not None else 0,
        "minimum_token_confidence": ocr_minimum if ocr_confidence is not None else None,
    }

    low_confidence = (
        confidence is not None
        and confidence * 100 < threshold
    )
    low_action = settings.get("lowAction", "Mark for manual review")
    if low_action not in {"Mark for manual review", "Reject prediction"}:
        low_action = "Mark for manual review"

    review_reasons = [item["message"] for item in validation_issues]
    if isinstance(classification, dict):
        review_reasons.extend(
            str(reason)
            for reason in classification.get("classification_review_reasons", [])
            if reason
        )
    is_unclassified = category not in expected_fields
    if is_unclassified:
        review_reasons.append(
            "The document could not be assigned to a supported class; verify it manually."
        )
    if required and coverage < 40:
        review_reasons.append(
            "Low extracted-field coverage; verify the OCR output manually."
        )
    if low_ocr_confidence:
        review_reasons.append(
            f"OCR recognition confidence is below the configured {ocr_threshold:.0%} threshold; verify the recognized text manually."
        )
    if low_confidence:
        if low_action == "Reject prediction":
            review_reasons.append(
                f"Prediction rejected by the configured {threshold}% confidence threshold; manual review is required."
            )
        else:
            review_reasons.append(
                f"Confidence is below the configured {threshold}% threshold; verify the prediction manually."
            )

    if specific_extraction_failed:
        review_reasons.append(
            "Document-specific extraction failed; verify the extracted fields manually."
        )
    if category in {"resume", "invoice", "form"} and not specific_fields:
        review_reasons.append(
            "No document-specific fields were mapped; verify the source text and extraction output."
        )
    if confidence_unavailable:
        review_reasons.append(
            "Classification confidence is unavailable for the final document category; manual review is required."
        )
    field_provenance = build_field_provenance(fields, source_pages or []) if source_pages else {}
    classification_review_required = (
        bool(
            classification.get("classification_review_required")
            or classification.get("review_required")
        )
        if isinstance(classification, dict) else False
    )
    review_required = (
        is_unclassified
        or bool(validation_issues)
        or (bool(required) and coverage < 40)
        or bool(form_missing_fields)
        or low_confidence
        or low_ocr_confidence
        or confidence_unavailable
        or specific_extraction_failed
        or (category in {"resume", "invoice", "form"} and not specific_fields)
        or classification_review_required
    )
    return {
        "summary": summary or "No readable text was extracted from this document.",
        "key_fields": fields,
        "field_provenance": field_provenance,
        "classification": classification,
        "word_count": len(text.split()),
        "character_count": len(text),
        "field_coverage": {
            "category": category or "unclassified",
            "expected_field_count": len(required),
            "extracted_field_count": len(extracted),
            "coverage_percent": coverage,
            "extracted_fields": extracted,
            "missing_fields": missing_fields,
        },
        "validation_issues": validation_issues,
        "ocr_confidence_review": ocr_confidence_review,
        "confidence_review": {
            "confidence": confidence,
            "status": confidence_status,
            "source": confidence_source,
            "basis": confidence_basis,
            "threshold_percent": threshold,
            "below_threshold": low_confidence,
            "action": low_action,
            "prediction_rejected": (
                low_confidence and low_action == "Reject prediction"
            ),
        },
        "review_required": review_required,
        "review_reasons": review_reasons,
    }

# OCR_ROW_HEIGHT_KEY_FIX
