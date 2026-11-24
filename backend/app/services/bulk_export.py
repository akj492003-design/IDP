"""Multi-document export helpers for IDP logical-document batches."""
from __future__ import annotations

import csv
import json
import re
import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path



def _label(value):
    return re.sub(r"\s+", " ", str(value).replace("_", " ")).strip().title()


_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def _leaf_text_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaf_text_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _leaf_text_values(child)
    elif value is not None:
        yield str(value)


def _normalise_form_title(value):
    title = " ".join(str(value or "").split()).strip()
    if title.casefold() == "account intake and service":
        return title + " REQUEST FORM"
    return title


def _invoice_party_address(fields, party):
    """Return an address value, never the full supplier/customer source block."""
    if not isinstance(fields, dict):
        return ""

    if party == "supplier":
        direct_key = "supplier_address"
        block_key = "seller_company_details"
    else:
        direct_key = "buyer_address"
        block_key = "buyer_customer_details"

    direct = fields.get(direct_key)
    if direct not in (None, "", [], {}):
        return direct

    block = fields.get(block_key)
    if isinstance(block, dict):
        for key, value in block.items():
            if re.search(r"(?i)address", str(key)) and value not in (None, "", [], {}):
                return value
        return ""

    if not isinstance(block, str):
        return ""

    for part in re.split(r"\s*\|\s*|\r?\n", block):
        match = re.match(
            r"(?i)^\s*(?:(?:registered|billing|shipping|communication)\s+)?address\s*[:#-]\s*(.+?)\s*$",
            part,
        )
        if match:
            return match.group(1).strip()
    return ""


def _remove_duplicate_party_source_block(fields, party):
    """Keep only source-block fragments not already represented by structured party fields."""
    if party == "supplier":
        composite_key = "seller_company_details"
        residual_key = "supplier_additional_details"
        known_keys = (
            "supplier_name", "vendor_name", "seller_name", "supplier_address",
            "supplier_state", "supplier_gstin", "supplier_email_addresses",
            "supplier_phone_numbers",
        )
    else:
        composite_key = "buyer_customer_details"
        residual_key = "buyer_additional_details"
        known_keys = (
            "customer_name", "buyer_name", "buyer_address", "buyer_gstin",
            "buyer_email_addresses", "buyer_phone_numbers",
        )

    block = fields.get(composite_key)
    if not isinstance(block, str) or not block.strip():
        return

    parts = [part.strip() for part in re.split(r"\s*\|\s*|\r?\n", block) if part.strip()]
    # Do not split or rewrite a prose block when it has no reliable component boundary.
    if len(parts) < 2:
        return

    known_values = []
    for key in known_keys:
        for value in _leaf_text_values(fields.get(key)):
            normalised = re.sub(r"[^a-z0-9]+", "", value.casefold())
            if len(normalised) >= 3:
                known_values.append(normalised)

    if not known_values:
        return

    residual = []
    for part in parts:
        candidates = [part]
        if ":" in part:
            candidates.append(part.split(":", 1)[1])
        if "：" in part:
            candidates.append(part.split("：", 1)[1])
        normalised_candidates = [
            re.sub(r"[^a-z0-9]+", "", candidate.casefold())
            for candidate in candidates
        ]
        duplicate = any(
            known == candidate
            or (len(known) >= 8 and known in candidate)
            for known in known_values
            for candidate in normalised_candidates
            if candidate
        )
        if not duplicate:
            residual.append(part)

    fields.pop(composite_key, None)
    if residual:
        fields[residual_key] = " | ".join(residual)


def _prepare_invoice_export_fields(fields):
    """Prepare a concise invoice report without deleting source extraction data."""
    prepared = dict(fields) if isinstance(fields, dict) else {}
    _remove_duplicate_party_source_block(prepared, "supplier")
    _remove_duplicate_party_source_block(prepared, "buyer")

    # Candidate aggregates are useful for diagnostics, but repeat structured
    # invoice fields in human-facing reports. JSON/XML continue to export raw data.
    if prepared.get("invoice_number"):
        prepared.pop("invoice_numbers", None)
    if prepared.get("supplier_gstin") and prepared.get("buyer_gstin"):
        prepared.pop("gstin", None)
    if prepared.get("invoice_date") or prepared.get("due_date"):
        prepared.pop("date_candidates", None)
        prepared.pop("dates", None)
    if any(prepared.get(key) not in (None, "", [], {}) for key in (
        "subtotal", "taxable_amount", "tax_amount", "grand_total", "line_items"
    )):
        prepared.pop("amount_candidates", None)
        prepared.pop("amounts", None)
    if prepared.get("supplier_email_addresses") or prepared.get("buyer_email_addresses"):
        prepared.pop("email_addresses", None)
    if prepared.get("supplier_phone_numbers") or prepared.get("buyer_phone_numbers"):
        prepared.pop("phone_numbers", None)

    return prepared


def _prepare_form_export_fields(fields):
    """Prepare a human-facing form view while preserving the raw source in JSON/XML."""
    prepared = dict(fields) if isinstance(fields, dict) else {}

    title_value = next((
        prepared.get(key) for key in ("form_title_type", "form_title", "title")
        if prepared.get(key) not in (None, "", [], {})
    ), None)
    title_value = _normalise_form_title(title_value) if title_value is not None else None

    declaration_key = next((
        key for key in ("declaration_statement", "declaration")
        if prepared.get(key) not in (None, "", [], {})
    ), None)

    normalizer_input = {
        "Form Title": title_value or "",
        "Email": prepared.get("email", ""),
        "Email Addresses": prepared.get("email_addresses", prepared.get("emails", "")),
        "Declaration Statement": prepared.get(declaration_key, "") if declaration_key else "",
        "Form Status": prepared.get("form_status", prepared.get("status", "")),
        "Privacy Acknowledgement": prepared.get("privacy_acknowledgement", ""),
        "Terms Acceptance": prepared.get("terms_acceptance", ""),
    }
    normalised = _normalise_form_csv_fields(normalizer_input)

    for key in ("form_title", "title"):
        prepared.pop(key, None)
    if title_value:
        prepared["form_title_type"] = normalised.get("Form Title", title_value)

    # Invalid OCR values such as section headings must not be represented as email addresses.
    for key in ("email", "email_addresses", "emails", "contact_email"):
        prepared.pop(key, None)
    contact_email = normalised.get("Contact Email")
    if contact_email:
        prepared["email"] = contact_email

    if declaration_key:
        prepared[declaration_key] = normalised.get(
            "Declaration Statement", normalizer_input["Declaration Statement"]
        )

    for label, key in (
        ("Submission Date", "submission_date"),
        ("Submission Status", "submission_status"),
        ("Record Status", "record_status"),
        ("Declarant / Authorised Person", "declarant_authorised_person"),
    ):
        if normalised.get(label):
            prepared[key] = normalised[label]

    if normalised.get("Privacy Acknowledgement"):
        prepared["privacy_acknowledgement"] = normalised["Privacy Acknowledgement"]
    if normalised.get("Terms Acceptance"):
        prepared["terms_acceptance"] = normalised["Terms Acceptance"]

    # Keep the human-facing report concise. Label/value OCR aggregates and
    # candidate lists remain available in the raw JSON/XML extraction payload.
    prepared.pop("labelled_field_values", None)
    prepared.pop("date_candidates", None)
    prepared.pop("dates", None)
    prepared.pop("amount_candidates", None)
    prepared.pop("amounts", None)
    if any(prepared.get(key) not in (None, "", [], {}) for key in (
        "email", "phone", "application_date", "submission_date"
    )):
        prepared.pop("email_addresses", None)
        prepared.pop("emails", None)
        prepared.pop("phone_numbers", None)

    # These are source aliases already represented by the normalized status fields.
    prepared.pop("form_status", None)
    return prepared



_RESUME_EXPORT_TECHNOLOGY_ALIASES = {
    "python": "Python",
    "pandas": "Pandas",
    "scikit-learn": "Scikit-learn",
    "tf-idf": "TF-IDF",
    "r": "R",
    "statistics": "Statistics",
    "google forms": "Google Forms",
}


def _split_legacy_resume_project_stack(description):
    """Extract a recognized technology prefix from legacy project descriptions."""
    if not isinstance(description, str):
        return None

    match = re.match(
        r"^\s*(?P<stack>.*?)\s+(?:[??|?]|[-\u2010-\u2015\u2212])\s+(?P<description>\S[\s\S]*)$",
        description,
    )
    if not match:
        return None

    parts = [
        " ".join(part.split()).strip(" .;")
        for part in match.group("stack").split(",")
    ]
    normalized = [part.casefold() for part in parts]

    if (
        len(parts) < 2
        or not all(part in _RESUME_EXPORT_TECHNOLOGY_ALIASES for part in normalized)
    ):
        return None

    technologies = list(dict.fromkeys(
        _RESUME_EXPORT_TECHNOLOGY_ALIASES[part]
        for part in normalized
    ))
    return technologies, match.group("description").strip()


def _prepare_resume_export_fields(fields):
    """Normalize legacy resume records for human-readable exports."""
    prepared = dict(fields) if isinstance(fields, dict) else {}

    projects = prepared.get("projects")
    if isinstance(projects, list):
        normalized_projects = []

        for raw in projects:
            if not isinstance(raw, dict):
                normalized_projects.append(raw)
                continue

            item = dict(raw)
            description_key = next(
                (
                    key for key in ("description", "summary", "details")
                    if isinstance(item.get(key), str) and item.get(key).strip()
                ),
                None,
            )

            if description_key:
                split = _split_legacy_resume_project_stack(
                    item[description_key]
                )
                if split:
                    extracted_technologies, clean_description = split

                    technology_key = next(
                        (
                            key for key in ("technologies", "tech_stack", "tools")
                            if key in item
                        ),
                        "technologies",
                    )
                    existing = item.get(technology_key)

                    if isinstance(existing, list):
                        merged = list(existing)
                    elif isinstance(existing, str) and existing.strip():
                        merged = [
                            part.strip()
                            for part in re.split(r"[,|;]", existing)
                            if part.strip()
                        ]
                    else:
                        merged = []

                    seen = {str(value).casefold() for value in merged}
                    for technology in extracted_technologies:
                        if technology.casefold() not in seen:
                            merged.append(technology)
                            seen.add(technology.casefold())

                    item[technology_key] = merged
                    item[description_key] = clean_description

            normalized_projects.append(item)

        prepared["projects"] = normalized_projects

    # Use a plain hyphen in generated reports so the separators remain
    # unambiguous with the built-in PDF fonts and text extractors.
    education = prepared.get("educational_qualifications")
    if isinstance(education, list):
        normalized_education = []
        for raw in education:
            if not isinstance(raw, dict):
                normalized_education.append(raw)
                continue

            item = dict(raw)
            year = item.get("graduation_year")
            if year not in (None, ""):
                item["graduation_year"] = re.sub(
                    r"\s*[\u2010-\u2015\u2212\u00b7\u2022?]\s*",
                    " - ",
                    str(year),
                ).strip()

            institution = item.get("institution")
            if isinstance(institution, str):
                item["institution"] = re.sub(
                    r"\s*[\u2010-\u2015\u2212\u00b7\u2022?]\s*(?=autonomous\b)",
                    " - ",
                    institution,
                    flags=re.IGNORECASE,
                ).strip()

            normalized_education.append(item)

        prepared["educational_qualifications"] = normalized_education

    return prepared


def prepare_export_display_fields(document_type, fields):
    """Return presentation-safe fields for PDF/DOCX/TXT/XLSX summaries."""
    kind = str(document_type or "").casefold()
    if kind == "form":
        return _prepare_form_export_fields(fields)
    if kind == "invoice":
        return _prepare_invoice_export_fields(fields)
    if kind == "resume":
        return _prepare_resume_export_fields(fields)
    return dict(fields) if isinstance(fields, dict) else {}


_FIELD_ORDER = {
    "resume": [
        "candidate_name", "name", "full_name",
        "emails", "email", "email_addresses",
        "phone_numbers", "phone",
        "location_address", "address",
        "professional_summary", "technical_skills",
        "work_experience", "experience_duration", "total_experience",
        "educational_qualifications", "projects",
        "certifications", "academic_achievements",
        "internships", "volunteer_experience",
        "extracurricular_activities", "languages",
        "publications", "interests", "references",
        "linkedin", "github", "portfolio",
    ],
    "invoice": [
        "supplier_name", "vendor_name", "seller_name",
        "supplier_address", "supplier_state", "supplier_gstin",
        "supplier_additional_details",
        "invoice_number", "invoice_date", "due_date",
        "place_of_supply", "tax_type", "gst_rate",
        "customer_name", "buyer_name", "buyer_address", "buyer_gstin",
        "buyer_additional_details", "bank_transfer_reference", "gstin",
        "supplier_email_addresses", "supplier_phone_numbers",
        "buyer_email_addresses", "buyer_phone_numbers",
        "line_items", "subtotal", "taxable_amount",
        "tax_breakdown", "tax_amount", "total_discount",
        "other_charges", "round_off", "grand_total",
        "amount_paid", "balance_due", "payment_terms",
        "amount_in_words", "currency",
        "email_addresses", "phone_numbers",
        "date_candidates", "amount_candidates",
        "calculation_validation",
    ],
    "form": [
        "applicant_name", "form_title_type", "full_name", "name",
        "primary_contact", "application_reference_number",
        "application_type", "application_date", "submission_date",
        "email", "email_addresses", "phone", "phone_numbers",
        "date_of_birth", "age", "gender", "address",
        "father_name", "mother_name", "guardian_name",
        "occupation", "nationality", "marital_status",
        "qualification", "category", "religion",
        "annual_income", "state", "district", "pin_code",
        "purpose_reason", "form_questions_answers",
        "selected_checkbox_radio_options",
        "signature_label_present", "labelled_field_values",
        "date_candidates", "amount_candidates",
    ],
}


def order_extracted_fields(document_type, fields):
    """Order top-level extracted fields without deleting or mutating values."""
    if not isinstance(fields, dict):
        return {}

    kind = str(document_type or "").strip().casefold()
    priorities = _FIELD_ORDER.get(kind, [])
    rank = {key: index for index, key in enumerate(priorities)}

    indexed = list(enumerate(fields.items()))
    indexed.sort(
        key=lambda item: (
            rank.get(str(item[1][0]).casefold(), len(rank)),
            item[0],
        )
    )

    return {key: value for _, (key, value) in indexed}


def _scalar(value):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _flatten(value, path=""):
    """Flatten nested data without losing document or field-path identity."""
    rows = []
    if isinstance(value, dict):
        if not value:
            return [(path or "(empty)", "—")]
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            rows.extend(_flatten(child, child_path))
    elif isinstance(value, list):
        if not value:
            return [(path or "(empty)", "—")]
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            rows.extend(_flatten(child, child_path))
    else:
        rows.append((path or "value", _scalar(value)))
    return rows


def _flatten_with_root(value, path=""):
    """Return flattened fields with their original top-level field name."""
    for field_path, field_value in _flatten(value, path):
        root = str(field_path).split(".", 1)[0].split("[", 1)[0]
        yield field_path, field_value, root

def _nested_lines(value, depth=0):
    indent = "  " * depth
    if isinstance(value, dict):
        if not value:
            return [f"{indent}—"]
        lines = []
        for key, child in value.items():
            label = _label(key)
            if isinstance(child, (dict, list)):
                lines.append(f"{indent}{label}:")
                lines.extend(_nested_lines(child, depth + 1))
            else:
                lines.append(f"{indent}{label}: {_scalar(child)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{indent}—"]
        lines = []
        for child in value:
            if isinstance(child, (dict, list)):
                lines.append(f"{indent}-")
                lines.extend(_nested_lines(child, depth + 1))
            else:
                lines.append(f"{indent}- {_scalar(child)}")
        return lines
    return [f"{indent}{_scalar(value)}"]


def _xml_tag(value):
    tag = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))
    if not tag or not re.match(r"[A-Za-z_]", tag[0]):
        tag = f"field_{tag}"
    return tag


def _append_xml(parent, tag, value):
    node = ET.SubElement(parent, _xml_tag(tag))
    if isinstance(value, dict):
        for key, child in value.items():
            _append_xml(node, key, child)
    elif isinstance(value, list):
        for child in value:
            _append_xml(node, "item", child)
    elif value is not None:
        node.text = _scalar(value)
    return node


def _source_pages(segment):
    explicit = segment.get("source_pages")
    if isinstance(explicit, list):
        return [int(page) for page in explicit if str(page).isdigit()]
    page_range = segment.get("page_range")
    if isinstance(page_range, dict):
        start, end = page_range.get("start"), page_range.get("end")
        try:
            start, end = int(start), int(end)
            if start > 0 and end >= start and end - start <= 10000:
                return list(range(start, end + 1))
        except (TypeError, ValueError):
            pass
    return []


def _records(doc):
    result = doc.get("result") if isinstance(doc.get("result"), dict) else {}
    segments = result.get("documents") if isinstance(result.get("documents"), list) else []
    records = []

    if segments:
        for position, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                continue
            insights = segment.get("insights") if isinstance(segment.get("insights"), dict) else {}
            classification = segment.get("classification") if isinstance(segment.get("classification"), dict) else {}
            confidence_review = insights.get("confidence_review") if isinstance(insights.get("confidence_review"), dict) else {}

            classification_confidence = confidence_review.get("confidence")
            classification_method = (
                confidence_review.get("source")
                or classification.get("score_type")
                or "Unavailable"
            )
            confidence_basis = (
                confidence_review.get("basis")
                or "Unavailable: classification confidence basis was not provided."
            )

            fields = insights.get("key_fields") if isinstance(insights.get("key_fields"), dict) else {}
            index = int(segment.get("document_index") or position)
            records.append({
                "document_id": f"{doc.get('_id', 'upload')}:{index}",
                "document_index": index,
                "document_type": str(classification.get("label") or "unclassified"),
                "source_pages": _source_pages(segment),
                "page_range": segment.get("page_range") if isinstance(segment.get("page_range"), dict) else {},
                "page_count": int(segment.get("page_count") or len(_source_pages(segment))),
                "classifier": str(classification.get("model") or "Unavailable"),
                "classification": classification,
                "classification_method": str(classification_method or "Unavailable"),
                "classification_confidence": (
                    classification_confidence
                    if classification_confidence is not None
                    else "Unavailable"
                ),
                "confidence_basis": str(confidence_basis),
                "processed_at": str(doc.get("processed_at") or ""),
                "extracted_fields": order_extracted_fields(classification.get("label"), fields),
                "field_provenance": insights.get("field_provenance") if isinstance(insights.get("field_provenance"), dict) else {},
                "validation_issues": insights.get("validation_issues") if isinstance(insights.get("validation_issues"), list) else [],
                "extraction_warnings": list(segment.get("boundary_warnings") or []) + list(insights.get("review_reasons") or []),
                "review_required": bool(insights.get("review_required") or segment.get("boundary_review_required")),
                "review_reasons": insights.get("review_reasons") if isinstance(insights.get("review_reasons"), list) else [],
            })
    else:
        insights = result.get("insights") if isinstance(result.get("insights"), dict) else {}
        classification = result.get("classification") if isinstance(result.get("classification"), dict) else {}
        if str(classification.get("label") or "").lower() == "multiple":
            raise ValueError("Logical document records are missing; refusing to export aggregated fields for a multi-document upload.")

        confidence_review = insights.get("confidence_review") if isinstance(insights.get("confidence_review"), dict) else {}

        classification_confidence = confidence_review.get("confidence")
        classification_method = (
            confidence_review.get("source")
            or classification.get("score_type")
            or "Unavailable"
        )
        confidence_basis = (
            confidence_review.get("basis")
            or "Unavailable: classification confidence basis was not provided."
        )

        fields = insights.get("key_fields") if isinstance(insights.get("key_fields"), dict) else {}
        page_count = int(result.get("page_count") or len(result.get("page_texts") or []))
        records.append({
            "document_id": f"{doc.get('_id', 'upload')}:1",
            "document_index": 1,
            "document_type": str(classification.get("label") or "unclassified"),
            "source_pages": list(range(1, page_count + 1)),
            "page_range": {"start": 1, "end": page_count} if page_count else {},
            "page_count": page_count,
            "classifier": str(classification.get("model") or "Unavailable"),
            "classification": classification,
            "classification_method": str(classification_method or "Unavailable"),
            "classification_confidence": (
                classification_confidence
                if classification_confidence is not None
                else "Unavailable"
            ),
            "confidence_basis": str(confidence_basis),
            "processed_at": str(doc.get("processed_at") or ""),
            "extracted_fields": order_extracted_fields(classification.get("label"), fields),
            "field_provenance": insights.get("field_provenance") if isinstance(insights.get("field_provenance"), dict) else {},
            "validation_issues": insights.get("validation_issues") if isinstance(insights.get("validation_issues"), list) else [],
            "extraction_warnings": list(insights.get("review_reasons") or []),
            "review_required": bool(insights.get("review_required")),
            "review_reasons": insights.get("review_reasons") if isinstance(insights.get("review_reasons"), list) else [],
        })

    original_name = str(doc.get("file_name") or "Document")
    return original_name, records



def _display_value(value, limit=160):
    if isinstance(value, dict):
        parts = [
            f"{_label(key)}: {_display_value(child, 70)}"
            for key, child in value.items()
        ]
        text = "; ".join(parts)
    elif isinstance(value, list):
        text = "; ".join(_display_value(child, 70) for child in value)
    elif value is None:
        text = "Not available"
    elif isinstance(value, bool):
        text = "Yes" if value else "No"
    else:
        text = " ".join(str(value).split())

    return textwrap.shorten(
        text,
        width=limit,
        placeholder="..."
    ) or "Not available"


def _build_pdf_report(records, original_name, scope):
    import fitz

    pdf = fitz.open()

    page = None
    y = 0
    column = 0

    page_width = 595
    page_height = 842

    left = 34
    right = 34
    gutter = 18
    column_width = (page_width - left - right - gutter) / 2
    column_x = [left, left + column_width + gutter]

    content_top = 130
    content_bottom = 782

    navy = (0.055, 0.12, 0.25)
    blue = (0.10, 0.27, 0.50)
    muted = (0.34, 0.40, 0.49)
    line_color = (0.79, 0.84, 0.90)

    current_record = None
    current_position = 1

    def wrap_text(text, max_width, fontsize, fontname):
        """Wrap text using actual PDF font measurements; do not truncate."""
        output = []

        paragraphs = str(text).splitlines() or [""]

        for paragraph in paragraphs:
            words = paragraph.split()

            if not words:
                output.append("")
                continue

            current = ""

            for word in words:
                word_width = fitz.get_text_length(
                    word,
                    fontname=fontname,
                    fontsize=fontsize,
                )

                if word_width > max_width:
                    if current:
                        output.append(current)
                        current = ""

                    chunk = ""

                    for character in word:
                        candidate = chunk + character
                        candidate_width = fitz.get_text_length(
                            candidate,
                            fontname=fontname,
                            fontsize=fontsize,
                        )

                        if chunk and candidate_width > max_width:
                            output.append(chunk)
                            chunk = character
                        else:
                            chunk = candidate

                    current = chunk
                    continue

                candidate = word if not current else current + " " + word
                candidate_width = fitz.get_text_length(
                    candidate,
                    fontname=fontname,
                    fontsize=fontsize,
                )

                if candidate_width <= max_width:
                    current = candidate
                else:
                    if current:
                        output.append(current)
                    current = word

            if current:
                output.append(current)

        return output or [""]

    def new_page(record, position, continuation=False):
        nonlocal page, y, column

        page = pdf.new_page(width=page_width, height=page_height)
        column = 0

        page.draw_rect(
            fitz.Rect(0, 0, page_width, 84),
            color=navy,
            fill=navy,
            overlay=True,
        )

        page.insert_text(
            (left, 22),
            "IDP SYSTEM | INTELLIGENT DOCUMENT PROCESSING",
            fontsize=8,
            fontname="hebo",
            color=(0.78, 0.88, 1.0),
        )

        page.insert_text(
            (left, 49),
            "Extracted Information Report",
            fontsize=15,
            fontname="hebo",
            color=(1, 1, 1),
        )

        if scope == "current":
            document_title = (
                f"DOCUMENT {record.get('document_index') or position}"
            )
        else:
            document_title = f"DOCUMENT {position} OF {len(records)}"

        type_label = str(
            record.get("document_type") or "Unclassified"
        ).upper()

        suffix = " | CONTINUED" if continuation else ""

        page.insert_text(
            (left, 70),
            f"{document_title} | {type_label}{suffix}"[:105],
            fontsize=8,
            fontname="hebo",
            color=(0.78, 0.88, 1.0),
        )

        source_pages = ", ".join(
            map(str, record.get("source_pages") or [])
        ) or "Unavailable"

        metadata = (
            f"Source pages: {source_pages} | "
            f"Record ID: {record.get('document_id') or 'Unavailable'} | "
            f"Classifier: {record.get('classifier') or 'Unavailable'}"
        )

        page.insert_textbox(
            fitz.Rect(left, 91, page_width - right, 121),
            metadata,
            fontsize=6.5,
            fontname="helv",
            color=muted,
        )

        y = content_top

    def advance_column(required_height=0):
        nonlocal page, y, column

        if required_height > content_bottom - content_top:
            return

        while y + required_height > content_bottom:
            if column == 0:
                column = 1
                y = content_top
            else:
                new_page(
                    current_record,
                    current_position,
                    continuation=True,
                )

    def line_height(fontsize, gap):
        return fontsize + gap

    def estimate_height(text, fontsize, gap, indent=0, bold=False):
        fontname = "hebo" if bold else "helv"
        available_width = column_width - indent - 4

        parts = wrap_text(
            text,
            available_width,
            fontsize,
            fontname,
        )

        return len(parts) * line_height(fontsize, gap)

    def add_line(
        text,
        fontsize=7.2,
        bold=False,
        indent=0,
        gap=1.2,
    ):
        nonlocal page, y, column

        fontname = "hebo" if bold else "helv"
        available_width = column_width - indent - 4

        parts = wrap_text(
            text,
            available_width,
            fontsize,
            fontname,
        )

        height = line_height(fontsize, gap)

        for part in parts:
            if y + height > content_bottom:
                advance_column(height)

            x = column_x[column] + indent

            page.insert_text(
                (x, y),
                part,
                fontsize=fontsize,
                fontname=fontname,
                color=blue if bold else (0.12, 0.16, 0.22),
            )

            y += height

    for position, record in enumerate(records, start=1):
        current_record = record
        current_position = position

        new_page(record, position)

        fields = prepare_export_display_fields(
            record.get("document_type"),
            record.get("extracted_fields"),
        )

        if not isinstance(fields, dict):
            fields = {}

        form_title = None

        if str(record.get("document_type") or "").casefold() == "form":
            form_title = fields.pop("form_title_type", None)

        if form_title:
            first_height = estimate_height(
                str(form_title), 7.2, 1.2, bold=False
            )
            advance_column(10 + first_height)

            add_line(
                "FORM TITLE",
                fontsize=8,
                bold=True,
                gap=1.5,
            )
            add_line(
                str(form_title),
                fontsize=7.2,
                gap=1.2,
            )
            y += 3

        field_items = list(fields.items())

        if not field_items and not form_title:
            add_line(
                "No extracted fields are available for this document.",
                fontsize=7.2,
            )

        for index, (key, value) in enumerate(field_items, start=1):
            nested_lines = _nested_lines(value, 1) or ["—"]

            heading = f"{index}. {_label(key)}"

            first_nested = str(nested_lines[0]).strip()
            required_height = (
                estimate_height(
                    heading,
                    8,
                    1.5,
                    bold=True,
                )
                + estimate_height(
                    first_nested,
                    7.2,
                    1.2,
                    indent=5,
                )
                + 2
            )

            advance_column(required_height)

            add_line(
                heading,
                fontsize=8,
                bold=True,
                gap=1.5,
            )

            for nested_line in nested_lines:
                raw_line = str(nested_line)
                leading_spaces = len(raw_line) - len(raw_line.lstrip(" "))
                indent = min(leading_spaces * 2.2, 24)

                add_line(
                    raw_line.strip() or "—",
                    fontsize=7.2,
                    indent=indent,
                    gap=1.2,
                )

            y += 3

    page_total = len(pdf)

    for page_number, report_page in enumerate(pdf, start=1):
        report_page.draw_line(
            fitz.Point(left, 800),
            fitz.Point(page_width - right, 800),
            color=line_color,
            width=0.6,
        )

        report_page.insert_text(
            (left, 820),
            "Generated by the Intelligent Document Processing System",
            fontsize=7,
            fontname="helv",
            color=muted,
        )

        report_page.insert_text(
            (page_width - 92, 820),
            f"Page {page_number} of {page_total}",
            fontsize=7,
            fontname="helv",
            color=muted,
        )

    payload = pdf.tobytes()
    pdf.close()

    return payload

def _build_docx_report(records, original_name, scope):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = Document()

    section = document.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)

    for style_name in (
        "Normal",
        "Title",
        "Subtitle",
        "Heading 1",
        "Heading 2",
        "Heading 3",
    ):
        style = document.styles[style_name]
        style.font.name = "Aptos"
        style.font.color.rgb = RGBColor(31, 48, 72)

    document.styles["Normal"].font.size = Pt(10)

    document.styles["Heading 1"].font.color.rgb = RGBColor(20, 52, 91)
    document.styles["Heading 1"].font.size = Pt(16)

    document.styles["Heading 2"].font.color.rgb = RGBColor(35, 83, 137)
    document.styles["Heading 2"].font.size = Pt(12)

    document.styles["Heading 3"].font.color.rgb = RGBColor(57, 83, 112)
    document.styles["Heading 3"].font.size = Pt(10)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title_run = title.add_run("IDP SYSTEM")
    title_run.bold = True
    title_run.font.name = "Aptos Display"
    title_run.font.size = Pt(22)
    title_run.font.color.rgb = RGBColor(20, 52, 91)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    subtitle_run = subtitle.add_run("EXTRACTED INFORMATION REPORT")
    subtitle_run.bold = True
    subtitle_run.font.size = Pt(11)
    subtitle_run.font.color.rgb = RGBColor(55, 104, 157)

    source_line = document.add_paragraph()
    source_line.alignment = WD_ALIGN_PARAGRAPH.CENTER

    source_run = source_line.add_run(f"Source file: {original_name}")
    source_run.font.size = Pt(9)
    source_run.font.color.rgb = RGBColor(95, 108, 125)

    # Actual Word PAGE and NUMPAGES fields.
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER

    footer_run = footer.add_run("Generated by IDP System | Page ")
    footer_run.font.size = Pt(8)
    footer_run.font.color.rgb = RGBColor(110, 120, 135)

    def add_word_field(paragraph, field_name):
        run = paragraph.add_run()

        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")

        instruction = OxmlElement("w:instrText")
        instruction.set(qn("xml:space"), "preserve")
        instruction.text = f" {field_name} "

        separate = OxmlElement("w:fldChar")
        separate.set(qn("w:fldCharType"), "separate")

        displayed_value = OxmlElement("w:t")
        displayed_value.text = "1"

        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")

        run._r.append(begin)
        run._r.append(instruction)
        run._r.append(separate)
        run._r.append(displayed_value)
        run._r.append(end)

        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(110, 120, 135)

    add_word_field(footer, "PAGE")

    footer_run = footer.add_run(" of ")
    footer_run.font.size = Pt(8)
    footer_run.font.color.rgb = RGBColor(110, 120, 135)

    add_word_field(footer, "NUMPAGES")

    for position, record in enumerate(records, start=1):
        document_index = record.get("document_index", position)

        document_type = str(
            record.get("document_type") or "Unclassified"
        ).replace("_", " ").title()

        separator = "\u2014"

        # Preserve the exact existing heading text.
        if scope == "current":
            heading_text = (
                f"Document {document_index} {separator} {document_type}"
            )
        else:
            heading_text = (
                f"Document {position} of {len(records)} "
                f"{separator} {document_type}"
            )

        heading = document.add_paragraph(style="Heading 1")
        heading.add_run(heading_text)
        heading.paragraph_format.keep_with_next = True

        # CRITICAL FIX:
        # Subsequent document headings themselves carry the page-break
        # property. Do not use document.add_page_break() here.
        if position > 1:
            heading.paragraph_format.page_break_before = True

        pages = ", ".join(
            map(str, record.get("source_pages") or [])
        ) or "Unavailable"

        metadata = document.add_paragraph()
        metadata.paragraph_format.space_after = Pt(8)

        for label, value in (
            ("Source pages", pages),
            ("Record ID", record.get("document_id") or "Unavailable"),
            ("Classifier", record.get("classifier") or "Unavailable"),
        ):
            run = metadata.add_run(f"{label}: ")
            run.bold = True
            metadata.add_run(f"{value}   ")

        fields = prepare_export_display_fields(
            record.get("document_type"),
            record.get("extracted_fields"),
        )
        form_title = fields.pop("form_title_type", None) if str(record.get("document_type") or "").casefold() == "form" else None

        if form_title:
            form_title_paragraph = document.add_paragraph()
            form_title_label = form_title_paragraph.add_run("Form Title: ")
            form_title_label.bold = True
            form_title_paragraph.add_run(str(form_title))
            form_title_paragraph.paragraph_format.keep_with_next = True

        field_items = list(fields.items())

        # Prominent identity panel.
        if field_items:
            identity_key, identity_value = field_items[0]

            identity_table = document.add_table(rows=1, cols=1)
            identity_cell = identity_table.cell(0, 0)
            identity_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

            cell_properties = identity_cell._tc.get_or_add_tcPr()

            shading = OxmlElement("w:shd")
            shading.set(qn("w:fill"), "EAF2FA")
            cell_properties.append(shading)

            identity_cell.text = ""

            identity_label = identity_cell.paragraphs[0]
            label_run = identity_label.add_run(
                _label(identity_key).upper()
            )
            label_run.bold = True
            label_run.font.size = Pt(8)
            label_run.font.color.rgb = RGBColor(91, 105, 123)

            identity_value_paragraph = identity_cell.add_paragraph()

            value_run = identity_value_paragraph.add_run(
                _display_value(identity_value, limit=220)
            )
            value_run.bold = True
            value_run.font.size = Pt(17)
            value_run.font.color.rgb = RGBColor(20, 52, 91)

            spacer = document.add_paragraph()
            spacer.paragraph_format.space_after = Pt(2)

        remaining_fields = field_items[1:] if field_items else []

        if remaining_fields:
            document.add_heading("Extracted details", level=2)

        for key, value in remaining_fields:
            field_heading = document.add_heading(
                _label(key),
                level=2,
            )
            field_heading.paragraph_format.keep_with_next = True

            for line in _nested_lines(value, 0):
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Inches(0.22)
                paragraph.paragraph_format.space_after = Pt(3)
                paragraph.add_run(line.strip() or " ")

        # Review diagnostics remain in structured exports, not this DOCX report.

    output = BytesIO()
    document.save(output)

    return output.getvalue()



def _build_structured_csv_archive(original_name, records):
    """Create linked CSV tables for logical IDP documents."""
    import zipfile
    from io import BytesIO, StringIO

    common_headers = [
        "Source File", "Document ID", "Document Index", "Document Type",
        "Source Pages", "Page Start", "Page End", "Page Count",
        "Classifier", "Classification Confidence", "Confidence Basis",
        "Processed At", "Review Required",
    ]

    specifications = {
        "documents.csv": [
            "Review Reasons", "Validation Issues", "Extraction Warnings"
        ],
        "document_fields.csv": [
            "Field Path", "Root Field", "Extracted Value"
        ],
        "resumes.csv": [
            "Candidate Name", "Emails", "Phone Numbers", "Location",
            "Professional Summary", "Experience Duration"
        ],
        "resume_work_experience.csv": [
            "Role Index", "Job Title", "Company", "Location",
            "Start Date", "End Date", "Employment Period",
            "Responsibilities", "Complete Record"
        ],
        "resume_education.csv": [
            "Education Index", "Degree", "Institution", "Field Of Study",
            "Graduation Year", "Grade", "Complete Record"
        ],
        "resume_skills.csv": ["Skill Category", "Skill"],
        "resume_projects.csv": [
            "Project Index", "Project Name", "Description",
            "Technologies", "URL", "Complete Record"
        ],
        "resume_certifications.csv": [
            "Credential Index", "Credential", "Issuer", "Date",
            "Complete Record"
        ],
        "resume_languages.csv": [
            "Language Index", "Language", "Proficiency", "Complete Record"
        ],
        "forms.csv": [
            "Form Title", "Application Reference", "Applicant Name",
            "Application Type", "Application Date", "Email", "Phone",
            "Organisation"
        ],
        "form_fields.csv": ["Field Path", "Field", "Extracted Value"],
        "form_declarations.csv": ["Declaration Field", "Declaration Value"],
        "invoices.csv": [
            "Invoice Number", "Invoice Date", "Due Date", "Supplier",
            "Supplier GSTIN", "Supplier Address", "Customer",
            "Customer GSTIN", "Customer Address", "Subtotal", "Tax Amount",
            "Discount", "Grand Total", "Calculation Validation"
        ],
        "invoice_line_items.csv": [
            "Line Item Index", "Description", "HSN/SAC Code", "Quantity",
            "Unit Price", "Taxable Value", "Complete Record"
        ],
        "invoice_tax.csv": ["Tax Component", "Tax Value", "Source Field"],
        "extraction_review.csv": ["Record Type", "Message"],
        "field_evidence.csv": [
            "Field", "Extracted Value", "Confidence",
            "Confidence Status", "Confidence Basis", "Evidence Pages",
            "Evidence Method", "Evidence Status"
        ],
    }

    rows = {name: [] for name in specifications}

    def cell(value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    def as_list(value):
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def as_dict(value):
        return value if isinstance(value, dict) else {"value": value}

    def pick(mapping, *names):
        if not isinstance(mapping, dict):
            return None

        lowered = {
            str(key).casefold(): value
            for key, value in mapping.items()
        }

        for name in names:
            value = lowered.get(name.casefold())
            if value not in (None, "", [], {}):
                return value

        return None

    def base(record):
        page_range = record.get("page_range") or {}

        if not isinstance(page_range, dict):
            page_range = {}

        return {
            "Source File": original_name,
            "Document ID": record.get("document_id", ""),
            "Document Index": record.get("document_index", ""),
            "Document Type": record.get("document_type", ""),
            "Source Pages": ", ".join(
                map(str, record.get("source_pages") or [])
            ),
            "Page Start": page_range.get("start", ""),
            "Page End": page_range.get("end", ""),
            "Page Count": record.get("page_count", ""),
            "Classifier": record.get("classifier", ""),
            "Classification Confidence": cell(
                record.get("classification_confidence")
            ),
            "Confidence Basis": record.get("confidence_basis", ""),
            "Processed At": record.get("processed_at", ""),
            "Review Required": cell(record.get("review_required", False)),
        }

    def add(table_name, record, extra):
        rows[table_name].append({
            **base(record),
            **extra,
        })

    for record in records:
        kind = str(record.get("document_type") or "").casefold()

        fields = record.get("extracted_fields")
        if not isinstance(fields, dict):
            fields = {}

        provenance = record.get("field_provenance")
        if not isinstance(provenance, dict):
            provenance = {}

        # MASTER DOCUMENT RECORD
        add("documents.csv", record, {
            "Review Reasons": cell(record.get("review_reasons") or []),
            "Validation Issues": cell(record.get("validation_issues") or []),
            "Extraction Warnings": cell(record.get("extraction_warnings") or []),
        })

        # ALL FIELDS: preserve nested field paths.
        for path, value, root in _flatten_with_root(fields):
            add("document_fields.csv", record, {
                "Field Path": path,
                "Root Field": root or "",
                "Extracted Value": value,
            })

        # FIELD EVIDENCE.
        for field_name, field_value in fields.items():
            evidence = provenance.get(field_name, {})

            if not isinstance(evidence, dict):
                evidence = {}

            add("field_evidence.csv", record, {
                "Field": field_name,
                "Extracted Value": cell(field_value),
                "Confidence": cell(evidence.get("confidence")),
                "Confidence Status": evidence.get(
                    "confidence_status", "unavailable"
                ),
                "Confidence Basis": evidence.get("confidence_basis", ""),
                "Evidence Pages": ", ".join(
                    map(str, evidence.get("source_pages") or [])
                ),
                "Evidence Method": evidence.get("method", ""),
                "Evidence Status": evidence.get("evidence_status", ""),
            })

        # =================================================
        # RESUME RECORDS
        # =================================================

        if kind == "resume":
            add("resumes.csv", record, {
                "Candidate Name": cell(pick(
                    fields, "candidate_name", "full_name", "name"
                )),
                "Emails": cell(pick(
                    fields, "emails", "email", "email_addresses"
                )),
                "Phone Numbers": cell(pick(
                    fields, "phone_numbers", "phones", "phone"
                )),
                "Location": cell(pick(
                    fields, "location_address", "address", "location"
                )),
                "Professional Summary": cell(pick(
                    fields, "professional_summary", "summary"
                )),
                "Experience Duration": cell(pick(
                    fields, "experience_duration", "total_experience"
                )),
            })

            for index, raw in enumerate(
                as_list(fields.get("work_experience")), 1
            ):
                item = as_dict(raw)

                add("resume_work_experience.csv", record, {
                    "Role Index": index,
                    "Job Title": cell(pick(
                        item, "job_title", "title", "position", "role",
                        "designation"
                    )),
                    "Company": cell(pick(
                        item, "company", "employer", "organisation",
                        "organization"
                    )),
                    "Location": cell(pick(
                        item, "location", "work_location"
                    )),
                    "Start Date": cell(pick(
                        item, "start_date", "from_date"
                    )),
                    "End Date": cell(pick(
                        item, "end_date", "to_date"
                    )),
                    "Employment Period": cell(pick(
                        item, "employment_period", "period"
                    )),
                    "Responsibilities": cell(pick(
                        item, "responsibilities", "duties", "description"
                    )),
                    "Complete Record": cell(item),
                })

            for index, raw in enumerate(
                as_list(fields.get("educational_qualifications")), 1
            ):
                item = as_dict(raw)

                add("resume_education.csv", record, {
                    "Education Index": index,
                    "Degree": cell(pick(
                        item, "degree", "qualification", "program", "course"
                    )),
                    "Institution": cell(pick(
                        item, "institution", "college", "university", "school"
                    )),
                    "Field Of Study": cell(pick(
                        item, "field_of_study", "specialization", "major"
                    )),
                    "Graduation Year": cell(pick(
                        item, "graduation_year", "year", "passing_year"
                    )),
                    "Grade": cell(pick(
                        item, "grade", "cgpa", "percentage", "score"
                    )),
                    "Complete Record": cell(item),
                })

            def add_skill(value, category=""):
                if isinstance(value, dict):
                    for key, child in value.items():
                        next_category = (
                            f"{category} > {key}" if category else str(key)
                        )
                        add_skill(child, next_category)

                elif isinstance(value, list):
                    for child in value:
                        add_skill(child, category)

                elif value is not None:
                    add("resume_skills.csv", record, {
                        "Skill Category": category,
                        "Skill": cell(value),
                    })

            add_skill(fields.get("technical_skills"))

            for index, raw in enumerate(as_list(fields.get("projects")), 1):
                item = as_dict(raw)

                add("resume_projects.csv", record, {
                    "Project Index": index,
                    "Project Name": cell(pick(
                        item, "title", "project_name", "name"
                    )),
                    "Description": cell(pick(
                        item, "description", "summary", "details"
                    )),
                    "Technologies": cell(pick(
                        item, "technologies", "tech_stack", "tools"
                    )),
                    "URL": cell(pick(item, "url", "link")),
                    "Complete Record": cell(item),
                })

            for index, raw in enumerate(
                as_list(fields.get("certifications")), 1
            ):
                item = as_dict(raw)

                add("resume_certifications.csv", record, {
                    "Credential Index": index,
                    "Credential": cell(pick(
                        item, "name", "title", "credential", "value"
                    )),
                    "Issuer": cell(pick(
                        item, "issuer", "organization", "institution"
                    )),
                    "Date": cell(pick(
                        item, "date", "issued_date", "year"
                    )),
                    "Complete Record": cell(item),
                })

            for index, raw in enumerate(
                as_list(fields.get("languages")), 1
            ):
                item = as_dict(raw)

                add("resume_languages.csv", record, {
                    "Language Index": index,
                    "Language": cell(pick(
                        item, "language", "name", "value"
                    )),
                    "Proficiency": cell(pick(
                        item, "proficiency", "level"
                    )),
                    "Complete Record": cell(item),
                })

        # =================================================
        # FORM RECORDS
        # =================================================

        elif kind == "form":
            add("forms.csv", record, {
                "Form Title": cell(pick(
                    fields, "form_title_type", "form_title", "title"
                )),
                "Application Reference": cell(pick(
                    fields, "application_reference_number",
                    "reference_number", "registration_number"
                )),
                "Applicant Name": cell(pick(
                    fields, "applicant_name", "full_name", "name"
                )),
                "Application Type": cell(pick(
                    fields, "application_type"
                )),
                "Application Date": cell(pick(
                    fields, "application_date"
                )),
                "Email": cell(pick(fields, "email", "email_addresses")),
                "Phone": cell(pick(fields, "phone", "phone_numbers")),
                "Organisation": cell(pick(
                    fields, "organisation_name", "organization_name"
                )),
            })

            for path, value, root in _flatten_with_root(fields):
                add("form_fields.csv", record, {
                    "Field Path": path,
                    "Field": root or "",
                    "Extracted Value": value,
                })

                if re.search(
                    r"(?i)(declaration|consent|acknowledg|terms|signature|checkbox|selected)",
                    str(root or ""),
                ):
                    add("form_declarations.csv", record, {
                        "Declaration Field": root,
                        "Declaration Value": value,
                    })

        # =================================================
        # INVOICE RECORDS
        # =================================================

        elif kind == "invoice":
            add("invoices.csv", record, {
                "Invoice Number": cell(pick(
                    fields, "invoice_number", "invoice_no"
                )),
                "Invoice Date": cell(pick(fields, "invoice_date")),
                "Due Date": cell(pick(fields, "due_date")),
                "Supplier": cell(pick(
                    fields, "supplier_name", "vendor_name", "seller_name"
                )),
                "Supplier GSTIN": cell(pick(fields, "supplier_gstin")),
                "Supplier Address": cell(pick(
                    fields, "supplier_address", "seller_company_details"
                )),
                "Customer": cell(pick(
                    fields, "customer_name", "buyer_name"
                )),
                "Customer GSTIN": cell(pick(fields, "buyer_gstin")),
                "Customer Address": cell(pick(
                    fields, "buyer_address", "buyer_customer_details"
                )),
                "Subtotal": cell(pick(
                    fields, "subtotal", "taxable_amount"
                )),
                "Tax Amount": cell(pick(
                    fields, "tax_amount", "total_tax"
                )),
                "Discount": cell(pick(
                    fields, "total_discount", "discount"
                )),
                "Grand Total": cell(pick(
                    fields, "grand_total", "total_amount", "total"
                )),
                "Calculation Validation": cell(
                    fields.get("calculation_validation")
                ),
            })

            for index, raw in enumerate(
                as_list(pick(
                    fields, "line_items", "items", "invoice_line_items"
                )), 1
            ):
                item = as_dict(raw)

                add("invoice_line_items.csv", record, {
                    "Line Item Index": index,
                    "Description": cell(pick(
                        item, "description", "item_description", "name",
                        "product"
                    )),
                    "HSN/SAC Code": cell(pick(
                        item, "hsn_sac_code", "hsn", "sac"
                    )),
                    "Quantity": cell(pick(item, "quantity", "qty")),
                    "Unit Price": cell(pick(
                        item, "unit_price", "rate", "unit_rate"
                    )),
                    "Taxable Value": cell(pick(
                        item, "taxable_value", "line_total", "amount"
                    )),
                    "Complete Record": cell(item),
                })

            tax = fields.get("tax_breakdown")

            if tax is not None:
                for path, value, root in _flatten_with_root(tax):
                    add("invoice_tax.csv", record, {
                        "Tax Component": path,
                        "Tax Value": value,
                        "Source Field": "tax_breakdown",
                    })

            for key, value in fields.items():
                if re.fullmatch(
                    r"(?i)(cgst|sgst|igst|utgst|cess|tax_amount|total_tax)",
                    str(key),
                ):
                    add("invoice_tax.csv", record, {
                        "Tax Component": key,
                        "Tax Value": cell(value),
                        "Source Field": key,
                    })

        # REVIEW QUEUE
        for record_type, messages in (
            ("Review Reason", record.get("review_reasons") or []),
            ("Extraction Warning", record.get("extraction_warnings") or []),
            ("Validation Issue", record.get("validation_issues") or []),
        ):
            for message in as_list(messages):
                add("extraction_review.csv", record, {
                    "Record Type": record_type,
                    "Message": cell(message),
                })

    output = BytesIO()

    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:

        for filename, extra_headers in specifications.items():
            buffer = StringIO(newline="")
            headers = common_headers + extra_headers

            writer = csv.DictWriter(
                buffer,
                fieldnames=headers,
                extrasaction="ignore",
            )

            writer.writeheader()
            writer.writerows(rows[filename])

            archive.writestr(
                filename,
                buffer.getvalue().encode("utf-8-sig"),
            )

        archive.writestr(
            "README.txt",
            (
                "IDP STRUCTURED CSV EXPORT\n"
                "Join child tables to documents.csv using Document ID.\n"
                "Document Index preserves upload order.\n"
                "document_fields.csv retains all nested extracted fields.\n"
                "Confidence is blank when the source does not provide it.\n"
            ).encode("utf-8"),
        )

    return output.getvalue()


def _build_structured_xlsx_workbook(records, original_name):
    """Create a linked, multi-sheet workbook for logical IDP documents."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    common = [
        "Source File", "Document ID", "Document Index", "Document Type",
        "Source Pages", "Page Start", "Page End", "Page Count",
        "Classifier", "Classification Confidence",
        "Classification Confidence Basis", "Processed At",
        "Review Required",
    ]

    schemas = {
        "Overview": ["Metric", "Value"],
        "Documents": common + [
            "Review Reasons", "Validation Issues", "Extraction Warnings"
        ],
        "All Fields": common + [
            "Field Path", "Root Field", "Extracted Value",
            "Field Confidence", "Confidence Status",
            "Confidence Basis", "Evidence Pages", "Evidence Status"
        ],
        "Resumes": common + [
            "Candidate Name", "Emails", "Phone Numbers", "Location",
            "Professional Summary", "Experience Duration"
        ],
        "Work Experience": common + [
            "Role Index", "Job Title", "Company", "Location",
            "Start Date", "End Date", "Employment Period",
            "Responsibilities", "Complete Record"
        ],
        "Education": common + [
            "Education Index", "Degree", "Institution", "Field Of Study",
            "Graduation Year", "Grade", "Complete Record"
        ],
        "Skills": common + ["Skill Category", "Skill"],
        "Projects": common + [
            "Project Index", "Project Name", "Description",
            "Technologies", "URL", "Complete Record"
        ],
        "Certifications": common + [
            "Credential Index", "Credential", "Issuer", "Date",
            "Complete Record"
        ],
        "Languages": common + [
            "Language Index", "Language", "Proficiency", "Complete Record"
        ],
        "Invoices": common + [
            "Invoice Number", "Invoice Date", "Due Date", "Supplier",
            "Supplier GSTIN", "Supplier State", "Supplier Address", "Customer",
            "Customer GSTIN", "Customer Address", "Place Of Supply", "Tax Type",
            "GST Rate", "Bank Transfer Reference", "Subtotal",
            "Tax Amount", "Discount", "Grand Total",
            "Calculation Validation"
        ],
        "Invoice Items": common + [
            "Line Item Index", "Description", "HSN/SAC Code", "Quantity",
            "Unit", "Unit Price", "Tax Rate", "Taxable Value",
            "Complete Record"
        ],
        "Tax Breakdown": common + [
            "Tax Component", "Tax Value", "Source Field"
        ],
        "Forms": common + [
            "Form Title", "Application Reference", "Applicant Name", "Primary Contact",
            "Application Type", "Application Date", "Submission Date", "Email", "Phone",
            "Organisation", "Submission Status", "Record Status", "Declarant / Authorised Person"
        ],
        "Form Fields": common + [
            "Field Path", "Field", "Extracted Value"
        ],
        "Declarations": common + [
            "Declaration Field", "Declaration Value"
        ],
        "Review Warnings": common + [
            "Record Type", "Message"
        ],
        "Field Evidence": common + [
            "Field", "Extracted Value", "Confidence",
            "Confidence Status", "Confidence Basis", "Evidence Pages",
            "Evidence Method", "Evidence Status"
        ],
    }

    workbook = Workbook()
    workbook.remove(workbook.active)

    sheets = {}

    for name, headers in schemas.items():
        sheet = workbook.create_sheet(name)
        sheet.append(headers)
        sheets[name] = sheet

    def safe(value):
        if value is None:
            return None

        if isinstance(value, bool):
            return value

        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(
                value,
                ensure_ascii=False,
                default=str,
            )
        elif not isinstance(value, (int, float)):
            value = str(value)

        if isinstance(value, str):
            value = value[:32000]

            # Prevent OCR text from being interpreted as an Excel formula.
            if value.lstrip().startswith(
                ("=", "+", "-", "@", "\t", "\r")
            ):
                value = "'" + value

        return value

    def cell(value):
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return safe(value)

    def as_list(value):
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def as_dict(value):
        return value if isinstance(value, dict) else {"value": value}

    def pick(mapping, *names):
        if not isinstance(mapping, dict):
            return None

        values = {
            str(key).casefold(): value
            for key, value in mapping.items()
        }

        for name in names:
            value = values.get(name.casefold())

            if value not in (None, "", [], {}):
                return value

        return None

    def meta(record):
        page_range = record.get("page_range") or {}

        if not isinstance(page_range, dict):
            page_range = {}

        pages = record.get("source_pages") or []

        return {
            "Source File": original_name,
            "Document ID": record.get("document_id", ""),
            "Document Index": record.get("document_index", ""),
            "Document Type": record.get("document_type", ""),
            "Source Pages": ", ".join(map(str, pages)),
            "Page Start": page_range.get("start", ""),
            "Page End": page_range.get("end", ""),
            "Page Count": record.get("page_count", ""),
            "Classifier": record.get("classifier", ""),
            "Classification Confidence": record.get(
                "classification_confidence", ""
            ),
            "Classification Confidence Basis": record.get(
                "confidence_basis", ""
            ),
            "Processed At": record.get("processed_at", ""),
            "Review Required": bool(record.get("review_required")),
        }

    def append(sheet_name, record, values):
        merged = {**meta(record), **values}

        sheets[sheet_name].append([
            safe(merged.get(header))
            for header in schemas[sheet_name]
        ])

    def scalar(value):
        if isinstance(value, (dict, list)):
            return json.dumps(
                value,
                ensure_ascii=False,
                default=str,
            )
        return value

    counts = {
        kind: 0
        for kind in ("resume", "invoice", "form", "unclassified")
    }

    review_count = 0

    for record in records:
        kind = str(
            record.get("document_type") or "unclassified"
        ).casefold()

        counts[kind] = counts.get(kind, 0) + 1
        review_count += int(bool(record.get("review_required")))

        fields = record.get("extracted_fields")

        if not isinstance(fields, dict):
            fields = {}

        provenance = record.get("field_provenance")

        if not isinstance(provenance, dict):
            provenance = {}

        # -------------------------------------------------
        # DOCUMENT MASTER
        # -------------------------------------------------

        append("Documents", record, {
            "Review Reasons": scalar(record.get("review_reasons") or []),
            "Validation Issues": scalar(record.get("validation_issues") or []),
            "Extraction Warnings": scalar(
                record.get("extraction_warnings") or []
            ),
        })

        # -------------------------------------------------
        # ALL FIELDS: preserve every nested field path.
        # -------------------------------------------------

        for path, value, root in _flatten_with_root(fields):
            evidence = provenance.get(root, {})

            if not isinstance(evidence, dict):
                evidence = {}

            confidence = evidence.get("confidence")

            append("All Fields", record, {
                "Field Path": path,
                "Root Field": root or "",
                "Extracted Value": value,
                "Field Confidence": (
                    confidence if confidence is not None else None
                ),
                "Confidence Status": evidence.get(
                    "confidence_status", "unavailable"
                ),
                "Confidence Basis": evidence.get(
                    "confidence_basis", ""
                ),
                "Evidence Pages": ", ".join(
                    map(str, evidence.get("source_pages") or [])
                ),
                "Evidence Status": evidence.get(
                    "evidence_status", ""
                ),
            })

        # -------------------------------------------------
        # FIELD EVIDENCE
        # -------------------------------------------------

        for field_name, field_value in fields.items():
            evidence = provenance.get(field_name, {})

            if not isinstance(evidence, dict):
                evidence = {}

            append("Field Evidence", record, {
                "Field": field_name,
                "Extracted Value": cell(field_value),
                "Confidence": evidence.get("confidence"),
                "Confidence Status": evidence.get(
                    "confidence_status", "unavailable"
                ),
                "Confidence Basis": evidence.get(
                    "confidence_basis", ""
                ),
                "Evidence Pages": ", ".join(
                    map(str, evidence.get("source_pages") or [])
                ),
                "Evidence Method": evidence.get("method", ""),
                "Evidence Status": evidence.get(
                    "evidence_status", ""
                ),
            })

        # =================================================
        # RESUME RECORDS
        # =================================================

        if kind == "resume":
            append("Resumes", record, {
                "Candidate Name": cell(pick(
                    fields, "candidate_name", "full_name", "name"
                )),
                "Emails": cell(pick(
                    fields, "emails", "email", "email_addresses"
                )),
                "Phone Numbers": cell(pick(
                    fields, "phone_numbers", "phones", "phone"
                )),
                "Location": cell(pick(
                    fields, "location_address", "address", "location"
                )),
                "Professional Summary": cell(pick(
                    fields, "professional_summary", "summary"
                )),
                "Experience Duration": cell(pick(
                    fields, "experience_duration", "total_experience"
                )),
            })

            for index, raw in enumerate(
                as_list(fields.get("work_experience")), 1
            ):
                item = as_dict(raw)

                append("Work Experience", record, {
                    "Role Index": index,
                    "Job Title": cell(pick(
                        item, "job_title", "title", "position",
                        "role", "designation"
                    )),
                    "Company": cell(pick(
                        item, "company", "employer",
                        "organisation", "organization"
                    )),
                    "Location": cell(pick(
                        item, "location", "work_location"
                    )),
                    "Start Date": cell(pick(
                        item, "start_date", "from_date"
                    )),
                    "End Date": cell(pick(
                        item, "end_date", "to_date"
                    )),
                    "Employment Period": cell(pick(
                        item, "employment_period", "period"
                    )),
                    "Responsibilities": cell(pick(
                        item, "responsibilities", "duties",
                        "description"
                    )),
                    "Complete Record": cell(item),
                })

            for index, raw in enumerate(
                as_list(fields.get("educational_qualifications")), 1
            ):
                item = as_dict(raw)

                append("Education", record, {
                    "Education Index": index,
                    "Degree": cell(pick(
                        item, "degree", "qualification",
                        "program", "course"
                    )),
                    "Institution": cell(pick(
                        item, "institution", "college",
                        "university", "school"
                    )),
                    "Field Of Study": cell(pick(
                        item, "field_of_study",
                        "specialization", "major"
                    )),
                    "Graduation Year": cell(pick(
                        item, "graduation_year",
                        "year", "passing_year"
                    )),
                    "Grade": cell(pick(
                        item, "grade", "cgpa",
                        "percentage", "score"
                    )),
                    "Complete Record": cell(item),
                })

            def add_skill(value, category=""):
                if isinstance(value, dict):
                    for key, child in value.items():
                        next_category = (
                            f"{category} > {key}"
                            if category else str(key)
                        )
                        add_skill(child, next_category)

                elif isinstance(value, (list, tuple)):
                    for child in value:
                        add_skill(child, category)

                elif value is not None:
                    append("Skills", record, {
                        "Skill Category": category,
                        "Skill": cell(value),
                    })

            add_skill(fields.get("technical_skills"))

            for index, raw in enumerate(
                as_list(fields.get("projects")), 1
            ):
                item = as_dict(raw)

                append("Projects", record, {
                    "Project Index": index,
                    "Project Name": cell(pick(
                        item, "title", "project_name", "name"
                    )),
                    "Description": cell(pick(
                        item, "description", "summary", "details"
                    )),
                    "Technologies": cell(pick(
                        item, "technologies", "tech_stack", "tools"
                    )),
                    "URL": cell(pick(item, "url", "link")),
                    "Complete Record": cell(item),
                })

            for index, raw in enumerate(
                as_list(fields.get("certifications")), 1
            ):
                item = as_dict(raw)

                append("Certifications", record, {
                    "Credential Index": index,
                    "Credential": cell(pick(
                        item, "name", "title", "credential", "value"
                    )),
                    "Issuer": cell(pick(
                        item, "issuer", "organization", "institution"
                    )),
                    "Date": cell(pick(
                        item, "date", "issued_date", "year"
                    )),
                    "Complete Record": cell(item),
                })

            for index, raw in enumerate(
                as_list(fields.get("languages")), 1
            ):
                item = as_dict(raw)

                append("Languages", record, {
                    "Language Index": index,
                    "Language": cell(pick(
                        item, "language", "name", "value"
                    )),
                    "Proficiency": cell(pick(
                        item, "proficiency", "level"
                    )),
                    "Complete Record": cell(item),
                })

        # =================================================
        # INVOICE RECORDS
        # =================================================

        elif kind == "invoice":
            append("Invoices", record, {
                "Invoice Number": cell(pick(
                    fields, "invoice_number", "invoice_no"
                )),
                "Invoice Date": cell(pick(fields, "invoice_date")),
                "Due Date": cell(pick(fields, "due_date")),
                "Supplier": cell(pick(
                    fields, "supplier_name", "vendor_name", "seller_name"
                )),
                "Supplier GSTIN": cell(pick(
                    fields, "supplier_gstin"
                )),
                "Supplier State": cell(pick(fields, "supplier_state")),
                "Supplier Address": cell(_invoice_party_address(fields, "supplier")),
                "Customer": cell(pick(
                    fields, "customer_name", "buyer_name"
                )),
                "Customer GSTIN": cell(pick(fields, "buyer_gstin")),
                "Customer Address": cell(_invoice_party_address(fields, "buyer")),
                "Place Of Supply": cell(pick(fields, "place_of_supply")),
                "Tax Type": cell(pick(fields, "tax_type")),
                "GST Rate": cell(pick(fields, "gst_rate", "tax_rate")),
                "Bank Transfer Reference": cell(pick(fields, "bank_transfer_reference", "bank_reference", "transaction_reference")),
                "Subtotal": cell(pick(
                    fields, "subtotal", "taxable_amount"
                )),
                "Tax Amount": cell(pick(
                    fields, "tax_amount", "total_tax"
                )),
                "Discount": cell(pick(
                    fields, "total_discount", "discount"
                )),
                "Grand Total": cell(pick(
                    fields, "grand_total", "total_amount", "total"
                )),
                "Calculation Validation": cell(
                    fields.get("calculation_validation")
                ),
            })

            for index, raw in enumerate(
                as_list(pick(
                    fields, "line_items", "items", "invoice_line_items"
                )), 1
            ):
                item = as_dict(raw)

                append("Invoice Items", record, {
                    "Line Item Index": index,
                    "Description": cell(pick(
                        item, "description", "item_description",
                        "name", "product"
                    )),
                    "HSN/SAC Code": cell(pick(
                        item, "hsn_sac_code", "hsn", "sac"
                    )),
                    "Quantity": cell(pick(
                        item, "quantity", "qty"
                    )),
                    "Unit": cell(pick(item, "unit", "uom")),
                    "Unit Price": cell(pick(
                        item, "unit_price", "rate", "unit_rate"
                    )),
                    "Tax Rate": cell(pick(
                        item, "tax_rate", "gst_rate"
                    )),
                    "Taxable Value": cell(pick(
                        item, "taxable_value", "line_total", "amount"
                    )),
                    "Complete Record": cell(item),
                })

            tax = fields.get("tax_breakdown")
            nested_tax_keys = set()

            if isinstance(tax, dict):
                for path, value, root in _flatten_with_root(tax):
                    nested_tax_keys.add(
                        str(path).split(" > ")[-1].casefold()
                    )

                    append("Tax Breakdown", record, {
                        "Tax Component": path,
                        "Tax Value": value,
                        "Source Field": "tax_breakdown",
                    })

            for key, value in fields.items():
                if (
                    re.fullmatch(
                        r"(?i)(cgst|sgst|igst|utgst|cess|tax_amount|total_tax)",
                        str(key),
                    )
                    and str(key).casefold() not in nested_tax_keys
                ):
                    append("Tax Breakdown", record, {
                        "Tax Component": key,
                        "Tax Value": cell(value),
                        "Source Field": key,
                    })

        # =================================================
        # FORM RECORDS
        # =================================================

        elif kind == "form":
            form_display_fields = _prepare_form_export_fields(fields)
            append("Forms", record, {
                "Form Title": cell(pick(
                    form_display_fields, "form_title_type", "form_title", "title"
                )),
                "Application Reference": cell(pick(
                    form_display_fields, "application_reference_number",
                    "reference_number", "registration_number"
                )),
                "Applicant Name": cell(pick(
                    form_display_fields, "applicant_name", "full_name", "name"
                )),
                "Primary Contact": cell(pick(form_display_fields, "primary_contact")),
                "Application Type": cell(pick(
                    form_display_fields, "application_type"
                )),
                "Application Date": cell(pick(
                    form_display_fields, "application_date"
                )),
                "Submission Date": cell(pick(form_display_fields, "submission_date")),
                "Email": cell(pick(
                    form_display_fields, "email"
                )),
                "Phone": cell(pick(
                    form_display_fields, "phone", "phone_numbers"
                )),
                "Organisation": cell(pick(
                    form_display_fields, "organisation_name", "organization_name"
                )),
                "Submission Status": cell(pick(form_display_fields, "submission_status")),
                "Record Status": cell(pick(form_display_fields, "record_status")),
                "Declarant / Authorised Person": cell(pick(form_display_fields, "declarant_authorised_person")),
            })

            for path, value, root in _flatten_with_root(fields):
                append("Form Fields", record, {
                    "Field Path": path,
                    "Field": root or "",
                    "Extracted Value": value,
                })

                if re.search(
                    r"(?i)(declaration|consent|acknowledg|terms|signature|checkbox|selected)",
                    str(root or ""),
                ):
                    append("Declarations", record, {
                        "Declaration Field": root,
                        "Declaration Value": value,
                    })

        # =================================================
        # REVIEW QUEUE
        # =================================================

        review_messages = []

        review_messages.extend(
            ("Review Reason", item)
            for item in as_list(record.get("review_reasons"))
        )

        review_messages.extend(
            ("Extraction Warning", item)
            for item in as_list(record.get("extraction_warnings"))
        )

        review_messages.extend(
            ("Validation Issue", item)
            for item in as_list(record.get("validation_issues"))
        )

        if record.get("review_required") and not review_messages:
            review_messages.append((
                "Review Status",
                "Document is flagged for manual review; no specific reason was supplied."
            ))

        seen_review_messages = set()

        for message_type, message in review_messages:
            message_text = cell(message)
            dedupe_key = str(message_text).strip().casefold()

            if dedupe_key in seen_review_messages:
                continue

            seen_review_messages.add(dedupe_key)

            append("Review Warnings", record, {
                "Record Type": message_type,
                "Message": message_text,
            })

    # =====================================================
    # OVERVIEW SHEET
    # =====================================================

    overview = sheets["Overview"]

    overview.append(["Source File", safe(original_name)])
    overview.append([
        "Generated At UTC",
        datetime.now(timezone.utc).isoformat(),
    ])
    overview.append(["Logical Documents", len(records)])
    overview.append(["Resume Documents", counts.get("resume", 0)])
    overview.append(["Invoice Documents", counts.get("invoice", 0)])
    overview.append(["Form Documents", counts.get("form", 0)])
    overview.append([
        "Documents Requiring Review",
        review_count,
    ])
    overview.append([
        "Relationship Key",
        "Document ID (also includes Document Index)",
    ])
    overview.append([
        "Confidence Policy",
        "Blank means unavailable; no score is inferred.",
    ])

    # =====================================================
    # WORKBOOK PRESENTATION
    # =====================================================

    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"

        if sheet.max_row >= 2:
            sheet.auto_filter.ref = sheet.dimensions

        for header_cell in sheet[1]:
            header_cell.font = Font(bold=True, color="FFFFFF")
            header_cell.fill = PatternFill(
                "solid",
                fgColor="12345A",
            )
            header_cell.alignment = Alignment(
                vertical="center",
                wrap_text=True,
            )

        for column_cells in sheet.columns:
            max_length = min(
                58,
                max(
                    12,
                    max(
                        len(str(cell.value or ""))
                        for cell in column_cells[:200]
                    ) + 2,
                ),
            )

            sheet.column_dimensions[
                get_column_letter(column_cells[0].column)
            ].width = max_length

        for row in sheet.iter_rows(min_row=2):
            for item in row:
                item.alignment = Alignment(
                    vertical="top",
                    wrap_text=True,
                )

    output = BytesIO()
    workbook.save(output)

    return (
        output.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )




def _normalise_form_csv_fields(values):
    """Normalize flattened form fields without losing canonical values."""
    result = dict(values)

    email_re = re.compile(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
        r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    )

    def leaves(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from leaves(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from leaves(item)
        elif value is not None:
            yield str(value)

    emails = []

    for key in list(result):
        compact = re.sub(r"[^a-z0-9]", "", str(key).casefold())

        if "email" in compact:
            for value in leaves(result[key]):
                emails.extend(email_re.findall(value))
            result.pop(key, None)
            continue

        if (
            compact.startswith("x")
            or "selecteachacknowledgement" in compact
            or "selectedcheckboxradiooptions" in compact
        ):
            result.pop(key, None)

    if emails:
        result["Contact Email"] = "; ".join(dict.fromkeys(emails))

    for key in ("Privacy Acknowledgement", "Terms Acceptance"):
        if key in result:
            match = re.search(
                r"(?i)\b(Not Accepted|Accepted|Rejected|Declined|Yes|No)\b",
                str(result[key] or ""),
            )
            if match:
                result[key] = match.group(1).title()

    # Split status metadata that was flattened into a single Form Status field.
    status_value = str(result.get("Form Status") or "")
    declaration_key = next(
        (
            key for key in result
            if re.sub(r"[^a-z]", "", key.casefold()) == "declarationstatement"
        ),
        None,
    )

    declaration = str(result.get(declaration_key) or "") if declaration_key else ""
    combined_status = " ".join((status_value, declaration))

    if re.search(r"(?i)\bSUBMITTED\b", combined_status):
        result["Submission Status"] = "Submitted"

    record_match = re.search(
        r"(?i)\bRecord\s+status\s*[:|-]\s*"
        r"(Complete|Incomplete|Pending|Draft)\b",
        combined_status,
    )
    if record_match:
        result["Record Status"] = record_match.group(1).title()

    date_match = re.search(
        r"(?i)\bSUBMISSION\s+DATE\s*[:#-]?\s*"
        r"(.*?)(?=\s+APPLICANT\s*/|\s+SUBMITTED\b|\s*\|\s*Record\s+status|$)",
        declaration,
    )
    if date_match and date_match.group(1).strip():
        result.setdefault("Submission Date", date_match.group(1).strip())

    person_match = re.search(
        r"(?i)\bAPPLICANT\s*/\s*AUTHORI[ZS]ED\s+PERSON"
        r"\s*[:#-]?\s*(.*?)(?=\s+SUBMITTED\b|\s*\|\s*Record\s+status|$)",
        declaration,
    )
    if person_match and person_match.group(1).strip():
        result["Declarant / Authorised Person"] = person_match.group(1).strip()

    if declaration_key:
        cut = re.search(
            r"(?i)\b(?:SUBMISSION\s+DATE|"
            r"APPLICANT\s*/\s*AUTHORI[ZS]ED\s+PERSON|"
            r"SUBMITTED\s*\|?\s*Record\s+status)\b",
            declaration,
        )
        if cut:
            result[declaration_key] = declaration[:cut.start()].strip(" :-|")

    result.pop("Form Status", None)

    title = str(result.get("Form Title") or "").strip()
    if title:
        result["Form Title"] = _normalise_form_title(title)

    return result


def build_bulk_export(doc, fmt, scope='all'):
    """Return (payload_bytes, mime_type, download_name) for every logical document."""
    fmt = str(fmt or "").lower().strip()
    scope = "current" if scope == "current" else "all"
    supported = {"json", "xml", "txt", "csv", "pdf", "docx", "xlsx"}
    if fmt not in supported:
        raise ValueError("Unsupported export format.")

    original_name, records = _records(doc)
    if not records:
        raise ValueError("No logical documents are available to export.")
    if not any(record["extracted_fields"] for record in records):
        raise ValueError("No extracted fields are available to export.")

    file_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(original_name).stem).strip("._-") or "document"
    report_metadata = {
        "source_file": original_name,
        "document_count": len(records),
        "export_scope": "all_documents",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if fmt == "json":
        payload = json.dumps(
            {"report": report_metadata, "documents": records},
            ensure_ascii=False, indent=2, default=str,
        ).encode("utf-8")
        mime = "application/json; charset=utf-8"

    elif fmt == "xml":
        root = ET.Element("idp_extraction_report", {
            "document_count": str(len(records)),
            "export_scope": "all_documents",
        })
        ET.SubElement(root, "source_file").text = original_name
        ET.SubElement(root, "generated_at").text = report_metadata["generated_at"]
        docs_node = ET.SubElement(root, "documents")
        for record in records:
            node = ET.SubElement(docs_node, "document", {
                "id": record["document_id"],
                "index": str(record["document_index"]),
                "type": record["document_type"],
                "source_pages": ",".join(map(str, record["source_pages"])),
                "review_required": str(record["review_required"]).lower(),
            })
            ET.SubElement(node, "classifier").text = record["classifier"]
            _append_xml(node, "classification", record["classification"])
            fields_node = ET.SubElement(node, "extracted_fields")
            for key, value in record["extracted_fields"].items():
                _append_xml(fields_node, key, value)
            _append_xml(node, "field_provenance", record["field_provenance"])
            _append_xml(node, "validation_issues", record["validation_issues"])
            _append_xml(node, "extraction_warnings", record["extraction_warnings"])
        payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        mime = "application/xml; charset=utf-8"

    elif fmt == "csv":
        # One row per logical document. Extracted fields become columns.
        metadata_columns = [
            "Source File",
            "Document ID",
            "Document Index",
            "Document Type",
            "Source Pages",
            "Review Required",
                    "Page Count",
                    "Review Reasons",
                    "Validation Issues",
                    "Extraction Warnings",
                    "Classification Method",
                    "Classifier Model",
                    "Classification Confidence",
                    "Confidence Basis",
        ]

        email_re = re.compile(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        )
        phone_re = re.compile(r"\+?\d[\d\s().-]{7,}\d")

        noisy_form_fields = {
            "emails",
            "email_addresses",
            "date_candidates",
            "dates",
            "amount_candidates",
            "selected_checkbox_radio_options",
            "labelled_field_values",
            "form_fields",
        }

        def clean_text(value):
            if value is None:
                return ""
            if isinstance(value, bool):
                return "Yes" if value else "No"
            return " ".join(str(value).split()).strip()

        def leaf_strings(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from leaf_strings(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    yield from leaf_strings(child)
            elif value is not None:
                yield str(value)

        def unique_join(existing, incoming):
            values = []

            for item in (existing, incoming):
                text = clean_text(item)
                if not text:
                    continue

                for part in text.split("; "):
                    if part and part not in values:
                        values.append(part)

            return "; ".join(values)

        def put(output, label, value):
            label = re.sub(
                r"\s+",
                " ",
                str(label or "").replace("_", " ")
            ).strip().title()

            if not label or value is None:
                return

            text = clean_text(value)
            if not text:
                return

            if label in output:
                output[label] = unique_join(output[label], text)
            else:
                output[label] = text

        def flatten_wide(value, path, output):
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = (
                        f"{path} > {_label(key)}"
                        if path else _label(key)
                    )
                    flatten_wide(child, child_path, output)
                return

            if isinstance(value, list):
                if not value:
                    return

                if all(
                    not isinstance(child, (dict, list, tuple))
                    for child in value
                ):
                    put(
                        output,
                        path,
                        "; ".join(
                            clean_text(child)
                            for child in value
                            if clean_text(child)
                        )
                    )
                else:
                    for index, child in enumerate(value, start=1):
                        child_path = (
                            f"{path} > Item {index}"
                            if path else f"Item {index}"
                        )
                        flatten_wide(child, child_path, output)
                return

            put(output, path or "Value", value)

        def valid_emails(value):
            found = []
            seen = set()

            for text in leaf_strings(value):
                for match in email_re.findall(text):
                    if match.casefold() not in seen:
                        seen.add(match.casefold())
                        found.append(match)

            return found

        def normalise_acceptance(value):
            text = clean_text(value)

            match = re.search(
                r"\b(Not Accepted|Accepted|Rejected|Declined|Yes|No)\s*$",
                text,
                re.IGNORECASE,
            )

            return match.group(1).title() if match else text

        def build_wide_fields(record):
            fields = record.get("extracted_fields")
            if not isinstance(fields, dict):
                fields = {}

            kind = str(
                record.get("document_type") or ""
            ).casefold()

            is_form = kind == "form"
            if kind == "invoice":
                fields = _prepare_invoice_export_fields(fields)
            output = {}

            if is_form:
                # Promote explicitly labelled fields.
                labelled = fields.get("labelled_field_values")

                if isinstance(labelled, dict):
                    for label, value in labelled.items():
                        label_text = clean_text(label)
                        low = label_text.casefold()

                        # Exclude noisy checkbox OCR headings.
                        if (
                            not label_text
                            or low.startswith("x ")
                            or low.startswith("[x]")
                            or "select each acknowledgement" in low
                        ):
                            continue

                        if "email" in low:
                            matches = valid_emails(value)
                            if matches:
                                put(
                                    output,
                                    "Contact Email",
                                    "; ".join(matches),
                                )
                            continue

                        if "phone" in low:
                            matches = []
                            for text in leaf_strings(value):
                                matches.extend(phone_re.findall(text))

                            if matches:
                                put(
                                    output,
                                    "Contact Phone",
                                    "; ".join(dict.fromkeys(matches)),
                                )
                            else:
                                put(output, label_text, value)
                            continue

                        if (
                            "acknowledgement" in low
                            or "terms acceptance" in low
                        ):
                            value = normalise_acceptance(value)

                        put(output, label_text, value)

                # Convert form label/value arrays to named columns.
                form_items = fields.get("form_fields")

                if isinstance(form_items, list):
                    for item in form_items:
                        if not isinstance(item, dict):
                            continue

                        label = clean_text(item.get("label"))

                        if label and label.casefold() not in {
                            "field", "label", "value"
                        }:
                            put(output, label, item.get("value"))

                # Convert checkbox selections to clean semantic fields.
                selections = fields.get(
                    "selected_checkbox_radio_options"
                )

                for text in leaf_strings(selections):
                    cleaned = re.sub(
                        r"^\s*\[?x\]?\s*",
                        "",
                        text,
                        flags=re.IGNORECASE,
                    )
                    low = cleaned.casefold()

                    status = re.search(
                        r"\b(Not Accepted|Accepted|Rejected|Declined|Yes|No)\s*$",
                        cleaned,
                        re.IGNORECASE,
                    )

                    if not status:
                        continue

                    if (
                        "privacy" in low
                        or "acknowledgement" in low
                    ):
                        put(
                            output,
                            "Privacy Acknowledgement",
                            status.group(1).title(),
                        )

                    elif (
                        "terms" in low
                        or "service condition" in low
                    ):
                        put(
                            output,
                            "Terms Acceptance",
                            status.group(1).title(),
                        )

            collected_emails = []
            collected_phones = []

            for key, value in fields.items():
                raw_key = str(key)
                low = raw_key.casefold().replace(" ", "_")

                if low in {
                    "labelled_field_values",
                    "form_fields",
                    "selected_checkbox_radio_options",
                }:
                    continue

                if is_form and low in noisy_form_fields:
                    continue

                if low in {
                    "emails",
                    "email_addresses",
                    "email",
                    "contact_email",
                }:
                    for match in valid_emails(value):
                        if match.casefold() not in {
                            item.casefold()
                            for item in collected_emails
                        }:
                            collected_emails.append(match)
                    continue

                if low in {
                    "phone",
                    "phones",
                    "phone_numbers",
                    "contact_phone",
                }:
                    for text in leaf_strings(value):
                        for match in phone_re.findall(text):
                            if match not in collected_phones:
                                collected_phones.append(match)
                    continue

                if is_form and low in {
                    "date_candidates",
                    "dates",
                    "amount_candidates",
                }:
                    continue

                if is_form and low == "form_title_type":
                    flatten_wide(value, "Form Title", output)
                    continue

                if is_form and (
                    low.startswith("x_")
                    or "select_each_acknowledgement" in low
                ):
                    continue

                label = _label(raw_key)

                if is_form and label in {
                    "Privacy Acknowledgement",
                    "Terms Acceptance",
                }:
                    value = normalise_acceptance(value)

                flatten_wide(value, label, output)

            if collected_emails:
                target = (
                    "Contact Email" if is_form else "Email Addresses"
                )
                put(output, target, "; ".join(collected_emails))

            if collected_phones:
                target = (
                    "Contact Phone" if is_form else "Phone Numbers"
                )
                put(output, target, "; ".join(collected_phones))

            return output

        wide_records = []
        field_columns = []

        for record in records:
            flattened = build_wide_fields(record)

            for column in flattened:
                if column not in field_columns:
                    field_columns.append(column)

            wide_records.append((record, flattened))

        preferred = [
            "Form Title",
            "Organisation Name",
            "Customer Type",
            "Primary Contact",
            "Contact Email",
            "Contact Phone",
            "Requested Plan",
            "Number Of Users",
            "Primary Use",
            "Billing Frequency",
            "Invoice Delivery Method",
            "Support Channel",
            "Privacy Acknowledgement",
            "Terms Acceptance",
            "Application Date",
            "Submission Date",
        ]

        for record, flattened in wide_records:
            if str(record.get("document_type") or "").casefold() == "form":
                normalized = _normalise_form_csv_fields(flattened)
                flattened.clear()
                flattened.update(normalized)

        field_columns = list(dict.fromkeys(
            key
            for _, flattened in wide_records
            for key in flattened
        ))

        ordered_fields = [
            column for column in preferred
            if column in field_columns
        ]

        ordered_fields.extend(
            sorted(
                column for column in field_columns
                if column not in ordered_fields
            )
        )

        headers = metadata_columns + ordered_fields

        output = StringIO(newline="")
        writer = csv.DictWriter(
            output,
            fieldnames=headers,
            extrasaction="ignore",
            lineterminator="\r\n",
        )

        writer.writeheader()

        for record, flattened in wide_records:
            row = {
                "Source File": original_name,
                "Document ID": record.get("document_id", ""),
                "Document Index": record.get("document_index", ""),
                "Document Type": record.get("document_type", "unclassified"),
                "Source Pages": ", ".join(
                    map(str, record.get("source_pages") or [])
                ),
                "Review Required": (
                    "Yes" if record.get("review_required") else "No"
                ),
            }

            row.update(flattened)
            row.update({
                "Page Count": record.get("page_count", ""),
                "Review Reasons": clean_text(
                    "; ".join(map(str, record.get("review_reasons") or []))
                    if isinstance(record.get("review_reasons"), (list, tuple))
                    else record.get("review_reasons", "")
                ),
                "Validation Issues": clean_text(
                    "; ".join(map(str, record.get("validation_issues") or []))
                    if isinstance(record.get("validation_issues"), (list, tuple))
                    else record.get("validation_issues", "")
                ),
                "Extraction Warnings": clean_text(
                    "; ".join(map(str, record.get("extraction_warnings") or []))
                    if isinstance(record.get("extraction_warnings"), (list, tuple))
                    else record.get("extraction_warnings", "")
                ),
                "Classification Method": (
                    record.get("classification_method")
                    or record.get("classification_source") or ""
                ),
                "Classifier Model": record.get("classifier", ""),
                "Classification Confidence": record.get(
                    "classification_confidence", ""
                ),
                "Confidence Basis": record.get("confidence_basis", ""),
            })
            writer.writerow(row)

        payload = output.getvalue().encode("utf-8-sig")
        mime = "text/csv; charset=utf-8"
    elif fmt == "txt":
        lines = [
            "INTELLIGENT DOCUMENT PROCESSING SYSTEM",
            "BULK EXTRACTED INFORMATION REPORT",
            f"Original file: {original_name}",
            f"Logical documents: {len(records)}",
            "",
        ]
        for position, record in enumerate(records, start=1):
            if position > 1:
                lines.extend(["=" * 88, ""])
            pages = ", ".join(map(str, record["source_pages"])) or "Unavailable"
            lines.extend([
                f"DOCUMENT {position} OF {len(records)} — {record['document_type'].upper()}",
                f"Document ID: {record['document_id']}",
                f"Source pages: {pages}",
                f"Classifier: {record['classifier']}",
                f"Review required: {'Yes' if record['review_required'] else 'No'}",
                "",
            ])
            report_fields = prepare_export_display_fields(
                record.get("document_type"), record.get("extracted_fields")
            )
            for index, (key, value) in enumerate(report_fields.items(), start=1):
                lines.append(f"{index}. {_label(key)}")
                lines.extend(_nested_lines(value, 1))
                lines.append("")
            if record["extraction_warnings"]:
                lines.append("Extraction warnings:")
                lines.extend(_nested_lines(record["extraction_warnings"], 1))
                lines.append("")
        payload = "\n".join(lines).encode("utf-8")
        mime = "text/plain; charset=utf-8"

    elif fmt == "pdf":
        payload = _build_pdf_report(records, original_name, scope)
        mime = "application/pdf"

    elif fmt == "docx":
        payload = _build_docx_report(records, original_name, scope)
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    else:  # xlsx
        payload, mime = _build_structured_xlsx_workbook(records, original_name)

    scope_suffix = (
        "all_documents"
        if scope == "all"
        else f"document_{records[0]['document_index']}"
    )
    extension = fmt
    return payload, mime, f"{file_stem}_{scope_suffix}_extracted.{extension}"



# INVOICE_FORM_EXPORT_PRESENTATION_FIX_20261116
