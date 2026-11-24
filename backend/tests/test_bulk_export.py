import csv
import io
import json
import xml.etree.ElementTree as ET

import fitz
from docx import Document
from openpyxl import load_workbook

from app.services.bulk_export import build_bulk_export


def sample_document():
    return {
        "_id": "upload-001",
        "file_name": "mixed_batch.pdf",
        "processed_at": "2026-11-14T10:00:00+00:00",
        "result": {
            "documents": [
                {
                    "document_index": 1,
                    "page_range": {"start": 1, "end": 2},
                    "page_count": 2,
                    "classification": {"label": "resume", "model": "EfficientNetB0"},
                    "insights": {
                        "key_fields": {
                            "candidate_name": "Aarav Kulkarni",
                            "emails": ["aarav@example.com"],
                            "work_experience": [{"company": "Northstar", "job_title": "Analyst"}],
                        },
                        "field_provenance": {"candidate_name": {"source_pages": [1], "confidence": 0.99}},
                        "validation_issues": [],
                        "review_reasons": [],
                    },
                    "boundary_warnings": [],
                },
                {
                    "document_index": 2,
                    "page_range": {"start": 3, "end": 3},
                    "page_count": 1,
                    "classification": {"label": "invoice", "model": "EfficientNetB0"},
                    "insights": {
                        "key_fields": {
                            "invoice_number": "INV-1002",
                            "supplier": {"name": "Example Supplies"},
                            "total_amount": 59000,
                        },
                        "field_provenance": {},
                        "validation_issues": [],
                        "review_reasons": [],
                    },
                    "boundary_warnings": [],
                },
                {
                    "document_index": 3,
                    "page_range": {"start": 4, "end": 4},
                    "page_count": 1,
                    "classification": {"label": "form", "model": "EfficientNetB0"},
                    "insights": {
                        "key_fields": {
                            "form_title": "Application Form",
                            "form_fields": [{"label": "Department", "value": "IT"}],
                        },
                        "field_provenance": {},
                        "validation_issues": [],
                        "review_reasons": [],
                    },
                    "boundary_warnings": [],
                },
            ]
        },
    }


def test_json_contains_independent_logical_documents():
    payload, mime, name = build_bulk_export(sample_document(), "json")
    parsed = json.loads(payload)
    assert len(parsed["documents"]) == 3
    assert parsed["documents"][0]["extracted_fields"]["candidate_name"] == "Aarav Kulkarni"
    assert "candidate_name" not in parsed["documents"][1]["extracted_fields"]
    assert "invoice_number" not in parsed["documents"][0]["extracted_fields"]
    assert mime.startswith("application/json")
    assert name.endswith("_all_documents_extracted.json")


def test_csv_exports_one_wide_row_per_logical_document():
    payload, mime, filename = build_bulk_export(sample_document(), "csv")

    assert mime.startswith("text/csv")
    assert filename.endswith(".csv")
    assert not payload.startswith(bytes((0x50, 0x4B, 0x03, 0x04)))

    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))

    assert len(rows) == 3

    by_index = {row["Document Index"]: row for row in rows}

    assert set(by_index) == {"1", "2", "3"}

    assert by_index["1"]["Candidate Name"] == "Aarav Kulkarni"
    assert by_index["1"]["Email Addresses"] == "aarav@example.com"
    assert by_index["1"]["Work Experience > Item 1 > Company"] == "Northstar"

    assert by_index["2"]["Invoice Number"] == "INV-1002"
    assert by_index["2"]["Supplier > Name"] == "Example Supplies"

    assert by_index["3"]["Department"] == "IT"


def test_xml_contains_three_separate_document_elements():
    payload, mime, _ = build_bulk_export(sample_document(), "xml")
    root = ET.fromstring(payload)
    docs = root.find("documents").findall("document")
    assert len(docs) == 3
    assert docs[0].get("type") == "resume"
    assert docs[1].get("type") == "invoice"
    assert docs[2].get("type") == "form"
    assert mime.startswith("application/xml")


def test_txt_separates_each_document():
    payload, mime, _ = build_bulk_export(sample_document(), "txt")
    text = payload.decode("utf-8")
    assert "DOCUMENT 1 OF 3 — RESUME" in text
    assert "DOCUMENT 2 OF 3 — INVOICE" in text
    assert "DOCUMENT 3 OF 3 — FORM" in text
    assert "aarav@example.com" in text and "INV-1002" in text
    assert mime.startswith("text/plain")


def test_pdf_starts_each_logical_document_on_a_new_page():
    payload, mime, _ = build_bulk_export(sample_document(), "pdf")
    pdf = fitz.open(stream=payload, filetype="pdf")

    page_count = len(pdf)
    page_text = [page.get_text() for page in pdf]
    pdf.close()

    assert page_count == 3, (
        "The small one-page resume, invoice, and form fixtures "
        "should each fit on one report page."
    )

    assert "DOCUMENT 1 OF 3" in page_text[0]
    assert "DOCUMENT 2 OF 3" in page_text[1]
    assert "DOCUMENT 3 OF 3" in page_text[2]

    report_text = "\\n".join(page_text)

    required_values = (
        "Aarav Kulkarni",
        "aarav@example.com",
        "INV-1002",
        "Example Supplies",
        "Application Form",
        "Department",
        "IT",
    )

    for value in required_values:
        assert value in report_text, (
            f"PDF report is missing an expected extracted value: {value}"
        )

    assert (
        "59000" in report_text or "59,000" in report_text
    ), "Invoice total is missing from the generated PDF."

    assert mime == "application/pdf"


def test_docx_has_page_breaks_between_document_sections():
    payload, mime, _ = build_bulk_export(sample_document(), "docx")
    document = Document(io.BytesIO(payload))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Document 1 of 3 — Resume" in text
    assert "Document 2 of 3 — Invoice" in text
    assert "Document 3 of 3 — Form" in text
    assert sum(1 for paragraph in document.paragraphs if "pageBreakBefore" in paragraph._p.xml) >= 2
    assert mime.startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_docx_omits_review_notes_but_json_retains_review_diagnostics():
    fixture = sample_document()
    insights = fixture["result"]["documents"][0]["insights"]
    insights["review_reasons"] = ["Review reason retained in structured export."]
    insights["review_required"] = True

    json_payload, _, _ = build_bulk_export(fixture, "json")
    structured = json.loads(json_payload)
    first_record = structured["documents"][0]

    assert first_record["review_required"] is True
    assert "Review reason retained in structured export." in first_record["review_reasons"]
    assert "Review reason retained in structured export." in first_record["extraction_warnings"]

    payload, mime, _ = build_bulk_export(fixture, "docx")
    document = Document(io.BytesIO(payload))
    report_text = "\\n".join(paragraph.text for paragraph in document.paragraphs)

    assert "Review notes" not in report_text
    assert "Review reason retained in structured export." not in report_text
    assert mime.startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_xlsx_has_summary_all_fields_and_type_sheets():
    payload, mime, _ = build_bulk_export(sample_document(), "xlsx")
    workbook = load_workbook(io.BytesIO(payload), read_only=True)
    assert {"Documents", "All Fields", "Resumes", "Invoices", "Forms", "Review Warnings"}.issubset(set(workbook.sheetnames))
    all_rows = list(workbook["All Fields"].iter_rows(values_only=True))
    assert any("aarav@example.com" in row for row in all_rows)
    assert any("INV-1002" in row for row in all_rows)
    assert mime.startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_bulk_export_supports_legacy_single_document_without_segments():
    legacy = {
        "_id": "legacy-001",
        "file_name": "single.pdf",
        "result": {
            "page_count": 1,
            "classification": {"label": "resume", "model": "EfficientNetB0"},
            "insights": {"key_fields": {"candidate_name": "Legacy Candidate"}},
        },
    }
    payload, _, _ = build_bulk_export(legacy, "json")
    parsed = json.loads(payload)
    assert len(parsed["documents"]) == 1
    assert parsed["documents"][0]["extracted_fields"]["candidate_name"] == "Legacy Candidate"



def test_bulk_export_refuses_aggregated_multiple_result_without_segments():
    legacy_batch = {
        "_id": "legacy-batch",
        "file_name": "old_batch.pdf",
        "result": {
            "classification": {"label": "multiple"},
            "insights": {"key_fields": {"emails": ["one@example.com", "two@example.com"]}},
        },
    }
    import pytest
    with pytest.raises(ValueError, match="Logical document records are missing"):
        build_bulk_export(legacy_batch, "json")





def test_resume_pdf_separates_legacy_project_stack_and_education_dashes():
    from app.services.bulk_export import prepare_export_display_fields

    fields = {
        "candidate_name": "Aditya Kiran Jadhav",
        "educational_qualifications": [
            {
                "institution": "Sangmeshwar College, Solapur ? Autonomous",
                "graduation_year": "2022 ? 2025",
                "degree": "Bachelor of Science (B.Sc.) in Statistics",
            }
        ],
        "projects": [
            {
                "title": "Machine Learning-Based Text Sentiment Analysis Research",
                "description": (
                    "Python, Pandas, Scikit-learn, TF-IDF ? "
                    "Investigated machine learning approaches for text sentiment classification."
                ),
            },
            {
                "title": "Statistical Analysis of Social Media Usage Patterns",
                "description": (
                    "R, Statistics, Google Forms ? "
                    "Conducted a statistical analysis project using Google Forms."
                ),
            },
        ],
    }

    prepared = prepare_export_display_fields("resume", fields)

    first_project = prepared["projects"][0]
    assert first_project["technologies"] == [
        "Python", "Pandas", "Scikit-learn", "TF-IDF"
    ]
    assert first_project["description"].startswith("Investigated")

    second_project = prepared["projects"][1]
    assert second_project["technologies"] == [
        "R", "Statistics", "Google Forms"
    ]
    assert second_project["description"].startswith("Conducted")

    education = prepared["educational_qualifications"][0]
    assert education["graduation_year"] == "2022 - 2025"
    assert education["institution"] == "Sangmeshwar College, Solapur - Autonomous"

    # Verify the actual generated PDF, not just the prepared dictionary.
    document = sample_document()
    document["result"]["documents"][0]["insights"]["key_fields"] = fields

    payload, mime, _ = build_bulk_export(document, "pdf")
    assert mime == "application/pdf"

    pdf = fitz.open(stream=payload, filetype="pdf")
    report_text = "\\n".join(page.get_text() for page in pdf)
    pdf.close()

    assert "Technologies:" in report_text
    assert "Python" in report_text
    assert "TF-IDF" in report_text
    assert "Google Forms" in report_text
    assert "Description: Python, Pandas" not in report_text
    assert "2022 - 2025" in report_text
    assert "Solapur - Autonomous" in report_text
