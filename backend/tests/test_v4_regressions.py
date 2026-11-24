import importlib
import logging
import sys
import types

import fitz
import pytest

from app.services import field_extractors as fx
from app.services import bulk_export as be


def _load_pipeline():
    if "flask" not in sys.modules:
        flask = types.ModuleType("flask")
        flask.current_app = object()
        sys.modules["flask"] = flask
    pipeline = importlib.import_module("app.services.document_pipeline")
    class DummyApp:
        config = {"PADDLEOCR_LANG": "en"}
        logger = logging.getLogger("idp-v4-tests")
    pipeline.current_app = DummyApp()
    return pipeline


def test_trailing_project_stack_moves_to_next_project():
    records = fx._parse_resume_projects([
        "Customer Churn Prediction - Built a churn model using Python | Pandas",
        "Sales Dashboard - Created a reporting dashboard",
    ])
    assert len(records) == 2
    assert "technologies" not in records[0]
    assert records[1]["technologies"] == ["Python", "Pandas"]
    assert records[0]["description"] == "Built a churn model using"


def test_certification_with_year_is_not_merged_into_previous_item():
    records = fx._parse_resume_credentials([
        "Google Data Analytics Professional Certificate",
        "SQL for Analytics (2023)",
    ])
    assert records == [
        {"title": "Google Data Analytics Professional Certificate"},
        {"title": "SQL for Analytics", "year": "2023"},
    ]


def test_bare_behance_profile_is_extracted_and_does_not_leak_into_languages():
    fields = fx.extract_resume_fields("""Aarav Mehta
Data Analyst
BEHANCE
behance.net/aaravmehta
LANGUAGES
English, Hindi""")
    assert fields["portfolio"] == ["https://behance.net/aaravmehta"]
    assert fields["languages"] == ["English", "Hindi"]


def test_unlabelled_city_region_location_is_extracted_conservatively():
    fields = fx.extract_resume_fields("""Aarav Mehta
Data Analyst
Solapur, Maharashtra
TECHNICAL SKILLS
Python, SQL""")
    assert fields["location_address"] == "Solapur, Maharashtra"


def test_name_plus_state_is_not_mistaken_for_unlabelled_location():
    location = fx._extract_resume_location("Aarav Mehta, Maharashtra", candidate_name="Aarav Mehta")
    assert location is None


def test_paddleocr_token_scores_are_aggregated():
    dp = _load_pipeline()
    metrics = {}
    dp._record_ocr_confidences(metrics, [0.9, 0.8, 0.6, 1.2, None])
    dp._record_ocr_confidences(metrics, [0.8, 0.9])
    assert metrics["ocr_confidence_count"] == 5
    assert metrics["ocr_confidence_mean"] == 0.8
    assert metrics["ocr_confidence_min"] == 0.6
    assert metrics["ocr_confidence_status"] == "available"


def test_paddleocr_v3_adapter_preserves_recognition_scores():
    dp = _load_pipeline()

    class Result:
        json = {"res": {"rec_texts": ["alpha", "beta"], "rec_scores": [0.91, 0.71]}}

    class Engine:
        def predict(self, path):
            return [Result()]

    paddle = types.ModuleType("paddleocr")
    paddle.PaddleOCR = lambda **kwargs: Engine()
    sys.modules["paddleocr"] = paddle
    if hasattr(dp._paddle_ocr, "engine"):
        delattr(dp._paddle_ocr, "engine")

    metrics = {}
    assert dp._paddle_ocr("fixture.png", metrics) == "alpha\nbeta"
    assert metrics["ocr_confidence_count"] == 2
    assert metrics["ocr_confidence_mean"] == 0.81


def test_paddleocr_v2_adapter_preserves_recognition_scores():
    dp = _load_pipeline()

    class Engine:
        def ocr(self, path, cls=True):
            return [[
                [None, ["alpha", 0.92]],
                [None, ["beta", 0.72]],
            ]]

    paddle = types.ModuleType("paddleocr")
    paddle.PaddleOCR = lambda **kwargs: Engine()
    sys.modules["paddleocr"] = paddle
    if hasattr(dp._paddle_ocr, "engine"):
        delattr(dp._paddle_ocr, "engine")

    metrics = {}
    assert dp._paddle_ocr("fixture.png", metrics) == "alpha\nbeta"
    assert metrics["ocr_confidence_count"] == 2
    assert metrics["ocr_confidence_mean"] == 0.82


def test_resume_coverage_counts_only_present_sections_and_nested_schema():
    dp = _load_pipeline()
    text = """Jane Doe
jane@example.com
Solapur, Maharashtra
PROFESSIONAL SUMMARY
Data analyst with reporting experience.
TECHNICAL SKILLS
Python, SQL
WORK EXPERIENCE
Data Analyst at Example Corp (2 years)
EDUCATION
BSc Statistics - Example College
PROJECTS
Sales Dashboard - Built an analytics dashboard
CERTIFICATIONS
Google Data Analytics Certificate"""
    result = dp.build_insights(
        text,
        {"label": "resume", "score_type": "softmax_probability", "scores": [{"label": "resume", "score": 0.93}]},
    )
    coverage = result["field_coverage"]
    assert coverage["coverage_percent"] == 100.0
    assert "company_names" not in coverage["missing_fields"]
    assert "job_titles" not in coverage["missing_fields"]
    assert "college_university_details" not in coverage["missing_fields"]
    assert "location_address" in coverage["extracted_fields"]


def test_low_ocr_confidence_triggers_review_but_missing_scores_stay_unknown():
    dp = _load_pipeline()
    text = """Jane Doe
jane@example.com
PROFESSIONAL SUMMARY
Data analyst.
TECHNICAL SKILLS
Python, SQL"""
    classification = {
        "label": "resume",
        "score_type": "softmax_probability",
        "scores": [{"label": "resume", "score": 0.95}],
    }
    low = dp.build_insights(
        text, classification,
        ocr_metrics={"ocr_confidence_mean": 0.55, "ocr_confidence_count": 10, "ocr_confidence_min": 0.2},
    )
    assert low["ocr_confidence_review"]["status"] == "available"
    assert low["ocr_confidence_review"]["below_threshold"] is True
    assert low["review_required"] is True

    unavailable = dp.build_insights(text, classification)
    assert unavailable["ocr_confidence_review"]["status"] == "unavailable"
    assert unavailable["ocr_confidence_review"]["confidence"] is None
    assert unavailable["review_required"] is False
    assert not any("OCR recognition confidence is below" in reason for reason in unavailable["review_reasons"])


def test_pdf_section_headings_are_kept_with_following_content():
    record = {
        "document_id": "fixture:1",
        "document_index": 1,
        "document_type": "resume",
        "source_pages": [1],
        "page_count": 1,
        "classifier": "fixture",
        "extracted_fields": {
            "candidate_name": "Jane Doe",
            "technical_skills": {"Programming": ["Python"]},
            "work_experience": [{
                "company": "Example Corp",
                "responsibilities": [
                    f"Responsibility item {i} with enough descriptive words to fill multiple report pages."
                    for i in range(1, 90)
                ],
            }],
        },
        "extraction_warnings": ["Verify one OCR boundary."],
    }
    payload = be._build_pdf_report([record], "fixture.pdf", "current")
    pdf = fitz.open(stream=payload, filetype="pdf")
    assert len(pdf) >= 2
    heading_pattern = __import__("re").compile(r"^(?:EXTRACTED DETAILS|\d+\. .+|FORM TITLE)$")
    for page in pdf:
        lines = [line.strip() for line in page.get_text().splitlines() if line.strip()]
        footer_at = next((i for i, line in enumerate(lines) if line.startswith("Generated by")), len(lines))
        content = lines[:footer_at]
        assert not content or not heading_pattern.fullmatch(content[-1])
        for index, line in enumerate(content):
            if heading_pattern.fullmatch(line):
                assert index + 1 < len(content), f"Heading stranded on page: {line}"
    assert all("REVIEW NOTES" not in page.get_text() for page in pdf)
    assert all("Verify one OCR boundary." not in page.get_text() for page in pdf)
    pdf.close()


def test_segment_ocr_confidence_uses_only_its_source_pages():
    dp = _load_pipeline()
    metrics = {}
    dp._record_ocr_confidences(metrics, [0.30, 0.50], page_number=1)
    dp._record_ocr_confidences(metrics, [0.90, 0.90], page_number=2)
    text = "Jane Doe\nPROFESSIONAL SUMMARY\nData analyst."
    classification = {
        "label": "resume",
        "score_type": "softmax_probability",
        "scores": [{"label": "resume", "score": 0.95}],
    }
    first = dp.build_insights(
        text, classification,
        source_pages=[{"page_number": 1, "text": text}],
        ocr_metrics=metrics,
    )
    second = dp.build_insights(
        text, classification,
        source_pages=[{"page_number": 2, "text": text}],
        ocr_metrics=metrics,
    )
    assert first["ocr_confidence_review"]["confidence"] == 0.4
    assert first["ocr_confidence_review"]["below_threshold"] is True
    assert second["ocr_confidence_review"]["confidence"] == 0.9
    assert second["ocr_confidence_review"]["below_threshold"] is False
