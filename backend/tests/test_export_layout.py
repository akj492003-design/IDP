from io import BytesIO

import fitz
from docx import Document

from app.services.bulk_export import build_bulk_export


def sample_document():
    return {
        "_id": "stage3b-layout-test",
        "file_name": "sample_batch.pdf",
        "processed_at": "2026-11-14T10:00:00+00:00",
        "result": {
            "documents": [
                {
                    "document_index": 3,
                    "page_range": {"start": 3, "end": 4},
                    "source_pages": [3, 4],
                    "page_count": 2,
                    "classification": {
                        "label": "resume",
                        "model": "EfficientNetB0",
                    },
                    "insights": {
                        "key_fields": {
                            "candidate_name": "Priya Menon",
                            "emails": ["priya.menon@example.com"],
                            "phone_numbers": ["+91 90000 10002"],
                            "professional_summary": (
                                "Data analyst with experience in reporting and analytics."
                            ),
                        },
                        "review_required": False,
                        "review_reasons": [],
                        "validation_issues": [],
                        "field_provenance": {},
                    },
                    "boundary_warnings": [],
                    "boundary_review_required": False,
                }
            ]
        },
    }


def test_pdf_report_places_identity_before_remaining_fields():
    payload, mime, name = build_bulk_export(sample_document(), "pdf")

    assert mime == "application/pdf"
    assert name.endswith("_all_documents_extracted.pdf")

    report = fitz.open(stream=payload, filetype="pdf")

    try:
        assert len(report) >= 1

        text = "\n".join(page.get_text() for page in report)

        assert text.index("Priya Menon") < text.index("Emails")
        assert "Source pages: 3, 4" in text
        assert "EfficientNetB0" in text
        assert "Page 1 of" in text

    finally:
        report.close()


def test_docx_report_places_identity_before_remaining_fields():
    payload, mime, name = build_bulk_export(sample_document(), "docx")

    assert mime == (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )

    assert name.endswith("_all_documents_extracted.docx")

    report = Document(BytesIO(payload))
    body_text = "".join(report._element.body.itertext())

    assert body_text.index("Priya Menon") < body_text.index("Emails")
    assert "Source pages: " in body_text
    assert "EfficientNetB0" in body_text


def test_current_scope_keeps_original_document_number_in_filename():
    _, _, name = build_bulk_export(
        sample_document(),
        "pdf",
        scope="current",
    )

    assert name.endswith("_document_3_extracted.pdf")
    assert "all_documents" not in name


def test_current_docx_scope_uses_shared_renderer_and_heading():
    payload, mime, name = build_bulk_export(
        sample_document(),
        "docx",
        scope="current",
    )

    assert mime.endswith("wordprocessingml.document")
    assert name.endswith("_document_3_extracted.docx")

    report = Document(BytesIO(payload))
    body_text = "".join(report._element.body.itertext())

    assert "Document 3 \u2014 Resume" in body_text
    assert "Priya Menon" in body_text


def test_shared_docx_heading_uses_page_break_property_for_multiple_records():
    batch = sample_document()
    template = batch["result"]["documents"][0]

    second = dict(template)
    second["document_index"] = 4
    second["page_range"] = {"start": 5, "end": 5}
    second["source_pages"] = [5]

    third = dict(template)
    third["document_index"] = 5
    third["page_range"] = {"start": 6, "end": 6}
    third["source_pages"] = [6]

    batch["result"]["documents"] = [template, second, third]

    payload, _, _ = build_bulk_export(batch, "docx")

    report = Document(BytesIO(payload))

    heading_paragraphs = [
        paragraph
        for paragraph in report.paragraphs
        if paragraph.style.name == "Heading 1"
        and paragraph.text.startswith("Document ")
    ]

    assert len(heading_paragraphs) == 3

    assert heading_paragraphs[0].text == "Document 1 of 3 \u2014 Resume"
    assert heading_paragraphs[1].text == "Document 2 of 3 \u2014 Resume"
    assert heading_paragraphs[2].text == "Document 3 of 3 \u2014 Resume"

    breaks = sum(
        1
        for paragraph in heading_paragraphs
        if paragraph.paragraph_format.page_break_before
    )

    assert breaks == 2
