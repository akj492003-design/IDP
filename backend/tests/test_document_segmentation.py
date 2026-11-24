from app.services.document_segmentation import group_page_predictions, classification_for_group


def test_page_class_transition_splits_mixed_pdf():
    pages = ["TAX INVOICE\nInvoice Number: A-1", "APPLICATION FORM\nApplicant Name: Jane Doe"]
    classification = {"label": "invoice", "page_predictions": [
        {"label": "invoice", "confidence": 0.94, "scores": [{"label": "resume", "score": 0.02}, {"label": "invoice", "score": 0.94}, {"label": "form", "score": 0.04}]},
        {"label": "form", "confidence": 0.91, "scores": [{"label": "resume", "score": 0.03}, {"label": "invoice", "score": 0.06}, {"label": "form", "score": 0.91}]},
    ]}
    groups = group_page_predictions(pages, classification)
    assert len(groups) == 2
    assert groups[0]["label"] == "invoice"
    assert groups[1]["label"] == "form"
    assert groups[0]["start_page"] == 1 and groups[1]["start_page"] == 2


def test_group_classification_uses_only_its_pages():
    classification = {"model": "EfficientNetB0", "page_predictions": [
        {"label": "invoice", "scores": [{"label": "resume", "score": .1}, {"label": "invoice", "score": .8}, {"label": "form", "score": .1}]},
        {"label": "form", "scores": [{"label": "resume", "score": .1}, {"label": "invoice", "score": .1}, {"label": "form", "score": .8}]},
    ]}
    group = {"label": "form", "page_indexes": [1], "start_page": 2, "end_page": 2, "boundary_method": "class_transition"}
    result = classification_for_group(group, classification)
    assert result["label"] == "form"
    assert result["scores"][0]["label"] == "form"


def test_same_class_invoices_split_only_when_invoice_identifier_changes():
    pages = [
        "TAX INVOICE\nInvoice Number: INV-100\nSubtotal: 100",
        "TAX INVOICE\nInvoice Number: INV-100\nContinued terms and conditions",
        "TAX INVOICE\nInvoice Number: INV-200\nSubtotal: 200",
    ]
    predictions = [
        {"label": "invoice", "confidence": .95},
        {"label": "invoice", "confidence": .94},
        {"label": "invoice", "confidence": .96},
    ]
    groups = group_page_predictions(pages, {"page_predictions": predictions})
    assert len(groups) == 2
    assert groups[0]["page_indexes"] == [0, 1]
    assert groups[1]["page_indexes"] == [2]


def test_unclassified_segment_is_not_forced_to_resume_when_no_scores_exist():
    group = {"label": "unclassified", "page_indexes": [0], "start_page": 1, "end_page": 1, "boundary_method": "single_document_fallback"}
    result = classification_for_group(group, {"model": "Text heuristic fallback", "score_type": "cue_count", "page_predictions": [{"label": "unclassified", "scores": []}]})
    assert result["label"] == "unclassified"


def test_adjacent_resumes_without_email_split_on_distinct_name_and_section_start():
    pages = [
        "Jane Doe\nProfessional Summary\nData analyst\nTechnical Skills\nPython\nEducation",
        "John Smith\nProfessional Summary\nBackend developer\nTechnical Skills\nJava\nEducation",
    ]
    predictions = [
        {"label": "resume", "confidence": 0.97},
        {"label": "resume", "confidence": 0.96},
    ]
    groups = group_page_predictions(pages, {"page_predictions": predictions})
    assert len(groups) == 2
    assert groups[1]["boundary_method"] == "same_class_identifier_change"


def test_adjacent_forms_without_reference_number_split_on_distinct_applicant():
    pages = [
        "APPLICATION FORM\nApplicant Name: Jane Doe\nDate of Birth: 01/01/2000",
        "APPLICATION FORM\nApplicant Name: John Smith\nDate of Birth: 02/02/2001",
    ]
    predictions = [
        {"label": "form", "confidence": 0.95},
        {"label": "form", "confidence": 0.94},
    ]
    groups = group_page_predictions(pages, {"page_predictions": predictions})
    assert len(groups) == 2


def test_low_confidence_class_transition_is_preserved_for_manual_review():
    pages = ["TAX INVOICE\nInvoice Number: INV-1", "APPLICATION FORM\nApplicant Name: Jane Doe"]
    predictions = [
        {"label": "invoice", "confidence": 0.94},
        {"label": "form", "confidence": 0.52},
    ]
    groups = group_page_predictions(pages, {"page_predictions": predictions})
    assert len(groups) == 1
    assert groups[0]["boundary_review_required"] is True
    assert groups[0]["boundary_warnings"]


def test_adjacent_invoices_without_invoice_numbers_can_use_supplier_and_date_identity():
    pages = [
        "TAX INVOICE\nInvoice Date: 10/11/2026\nBill From: Supplier A",
        "TAX INVOICE\nInvoice Date: 11/11/2026\nBill From: Supplier A",
    ]
    predictions = [
        {"label": "invoice", "confidence": 0.96},
        {"label": "invoice", "confidence": 0.95},
    ]
    groups = group_page_predictions(pages, {"page_predictions": predictions})
    assert len(groups) == 2
    assert groups[1]["boundary_method"] == "same_class_identifier_change"


def test_group_classification_ignores_malformed_score_payloads():
    group = {
        "label": "invoice",
        "page_indexes": [0],
        "start_page": 1,
        "end_page": 1,
        "boundary_method": "first_page",
    }
    result = classification_for_group(
        group,
        {
            "page_predictions": [
                {"label": "invoice", "scores": "not-a-list"}
            ]
        },
    )
    assert result["label"] == "invoice"
    assert result["scores"] == [
        {"label": "resume", "score": 0.0},
        {"label": "invoice", "score": 0.0},
        {"label": "form", "score": 0.0},
    ]


def test_ocr_structural_evidence_overrides_confident_resume_prediction_for_application_form():
    from app.services.document_segmentation import reconcile_page_predictions

    form_text = """GENERAL APPLICATION FORM
Application Reference Number
APP-2026-0148
Application Date
11-Nov-2026
Application Type
General Registration
Applicant Name
Aarav Kulkarni
Date of Birth
14-Aug-1998
Age
28 years
Gender
Male
Declaration
I hereby declare the information is correct.
"""
    visual = {
        "label": "resume",
        "model": "EfficientNetB0",
        "score_type": "softmax_probability",
        "scores": [
            {"label": "resume", "score": 0.918},
            {"label": "form", "score": 0.058},
            {"label": "invoice", "score": 0.024},
        ],
        "page_predictions": [{
            "page_number": 1,
            "label": "resume",
            "confidence": 0.918,
            "scores": [
                {"label": "resume", "score": 0.918},
                {"label": "form", "score": 0.058},
                {"label": "invoice", "score": 0.024},
            ],
        }],
    }

    result = reconcile_page_predictions([form_text], visual)

    assert result["label"] == "form"
    assert result["score_type"] == "hybrid_decision"
    assert result["page_predictions"][0]["visual_label"] == "resume"
    assert result["page_predictions"][0]["label"] == "form"
    assert result["classification_review_required"] is True
    assert result["page_predictions"][0]["text_evidence"]["strength"] == "strong"


def test_ocr_weak_keywords_do_not_override_visual_prediction():
    from app.services.document_segmentation import reconcile_page_predictions

    text = "A resume applicant should complete the form and include their invoice reference."
    visual = {
        "label": "resume",
        "model": "EfficientNetB0",
        "page_predictions": [{"page_number": 1, "label": "resume", "confidence": 0.91, "scores": []}],
    }
    result = reconcile_page_predictions([text], visual)
    assert result["label"] == "resume"
    assert result["ocr_override_count"] == 0

def test_adjacent_resumes_with_alternate_heading_vocabulary_are_split():
    pages = [
        "\n".join([
            "AARAV KULKARNI", "aarav@example.com",
            "PROFESSIONAL SUMMARY", "Data analyst",
            "TECHNICAL SKILLS", "Python", "EDUCATION", "BSc",
        ]),
        "\n".join([
            "PRIYA MENON", "priya@example.com",
            "PROFILE SUMMARY", "Software engineer",
            "CORE COMPETENCIES", "Java, Python",
            "EMPLOYMENT HISTORY", "Developer",
            "ACADEMIC QUALIFICATIONS", "BSc",
            "PERSONAL PROJECTS", "Application",
            "PROFESSIONAL CERTIFICATIONS", "Cloud",
        ]),
    ]

    predictions = [
        {"label": "resume", "confidence": 0.97},
        {"label": "resume", "confidence": 0.96},
    ]

    groups = group_page_predictions(
        pages, {"page_predictions": predictions}
    )

    assert len(groups) == 2
    assert groups[0]["page_indexes"] == [0]
    assert groups[1]["page_indexes"] == [1]
    assert groups[1]["boundary_method"] == "same_class_identifier_change"


def test_three_page_resume_with_repeated_identity_remains_one_document():
    pages = [
        "\n".join([
            "AARAV KULKARNI", "aarav@example.com",
            "PROFESSIONAL SUMMARY", "Data analyst",
        ]),
        "\n".join([
            "AARAV KULKARNI", "aarav@example.com",
            "EMPLOYMENT HISTORY", "Analyst",
            "ACADEMIC QUALIFICATIONS", "BSc",
            "PERSONAL PROJECTS", "Dashboard",
        ]),
        "\n".join([
            "AARAV KULKARNI", "aarav@example.com",
            "PROFESSIONAL CERTIFICATIONS", "Cloud certification",
            "VOLUNTEER EXPERIENCE", "Mentoring",
        ]),
    ]

    predictions = [
        {"label": "resume", "confidence": 0.97},
        {"label": "resume", "confidence": 0.96},
        {"label": "resume", "confidence": 0.95},
    ]

    groups = group_page_predictions(
        pages, {"page_predictions": predictions}
    )

    assert len(groups) == 1
    assert groups[0]["page_indexes"] == [0, 1, 2]


def test_strong_ocr_override_splits_low_confidence_page_as_reviewable_boundary():
    from app.services.document_segmentation import reconcile_page_predictions

    resume_text = """Jane Doe
    Professional Summary
    Data analyst with reporting experience
    Work Experience
    Reporting Analyst
    Technical Skills
    Python SQL
    Education
    Statistics Graduate
    """

    form_text = """GENERAL APPLICATION FORM
    Application Reference Number
    APP-2026-0148
    Application Date
    11-Nov-2026
    Application Type
    General Registration
    Applicant Name
    Aarav Kulkarni
    Date of Birth
    14-Aug-1998
    Age
    28 years
    Gender
    Male
    Declaration
    I hereby declare the information is correct.
    """

    visual = {
        "label": "resume",
        "model": "EfficientNetB0",
        "page_predictions": [
            {
                "page_number": 1,
                "label": "resume",
                "confidence": 0.96,
                "scores": [],
            },
            {
                "page_number": 2,
                "label": "resume",
                "confidence": 0.52,
                "scores": [],
            },
        ],
    }

    reconciled = reconcile_page_predictions(
        [resume_text, form_text],
        visual,
    )

    assert reconciled["page_predictions"][1]["label"] == "form"
    assert reconciled["page_predictions"][1]["classification_conflict"] is True

    groups = group_page_predictions(
        [resume_text, form_text],
        reconciled,
    )

    assert len(groups) == 2
    assert groups[0]["label"] == "resume"
    assert groups[1]["label"] == "form"
    assert groups[1]["boundary_review_required"] is True
    assert groups[1]["boundary_warnings"]



def test_account_intake_pages_split_on_distinct_organisation_identity():
    pages = [
        "ACCOUNT INTAKE FORM\nOrganisation Name: Acme Software Pvt Ltd\nRequested Plan: Professional - Monthly\nNumber of Users: 18",
        "ACCOUNT INTAKE FORM\nOrganisation Name: Northstar Analytics Ltd\nRequested Plan: Basic - Annual\nNumber of Users: 6",
    ]
    predictions = [
        {"label": "form", "confidence": 0.96},
        {"label": "form", "confidence": 0.95},
    ]

    groups = group_page_predictions(pages, {"page_predictions": predictions})

    assert len(groups) == 2
    assert groups[1]["boundary_method"] == "same_class_identifier_change"



def test_account_intake_form_is_recognised_from_multiple_independent_ocr_cues():
    from app.services.document_segmentation import classify_text_structure

    text = """ACCOUNT INTAKE FORM
Organisation Name: Acme Software Pvt Ltd
Requested Plan: Professional - Monthly
Number of Users: 18
Customer Type: Enterprise
Primary Use: Document processing
Billing Frequency: Monthly
Invoice Delivery Method: Email
Privacy Acknowledgement: Yes
Support Channel: Email
Terms and Conditions: Accepted
"""

    label, evidence = classify_text_structure(text)

    assert label == "form"
    assert evidence["strength"] == "strong"
    assert "requested_plan" in evidence["matched_cues"]



def test_five_account_intake_forms_split_by_inline_form_reference_and_multiline_identity():
    pages = [
        """ACCOUNT INTAKE AND SERVICE REQUEST FORM
Customer onboarding | Form reference: AIN-2026-001
APPLICANT DETAILS
Organisation name
Northstar Analytics LLP
Customer type
Business
Primary contact
Ishita Rao
Contact email
ishita.rao@example.test
Contact phone
+91 90000 22001
REQUESTED PLAN
Professional - Monthly
NUMBER OF USERS
18""",

        """ACCOUNT INTAKE AND SERVICE REQUEST FORM
Customer onboarding | Form reference: AIN-2026-002
APPLICANT DETAILS
ORGANISATION NAME
Maple Grove Clinic
CUSTOMER TYPE
Business
PRIMARY CONTACT
Dr. Kabir Nair
CONTACT EMAIL
kabir.nair@example.test
CONTACT PHONE
+91 90000 22002
REQUESTED PLAN
Team - Annual
NUMBER OF USERS
32""",

        """ACCOUNT INTAKE AND SERVICE REQUEST FORM
Customer onboarding | Form reference: AIN-2026-003
APPLICANT DETAILS
FIELD
SUBMITTED VALUE
Organisation name
Aster Learning Centre
Customer type
Education
Primary contact
Mira Thomas
Contact email
mira.thomas@example.test
Contact phone
+91 90000 22003
REQUESTED PLAN
Starter - Monthly
NUMBER OF USERS
7""",

        """ACCOUNT INTAKE AND SERVICE REQUEST FORM
Customer onboarding | Form reference: AIN-2026-004
APPLICANT DETAILS
ORGANISATION NAME
Riverbend Components Pvt Ltd
CUSTOMER TYPE
Business
PRIMARY CONTACT
Dev Malhotra
CONTACT EMAIL
dev.malhotra@example.test
CONTACT PHONE
+91 90000 22004
REQUESTED PLAN
Enterprise - Annual
NUMBER OF USERS
125""",

        """ACCOUNT INTAKE AND SERVICE REQUEST FORM
Customer onboarding | Form reference: AIN-2026-005
APPLICANT DETAILS
ORGANISATION NAME
Cedar & Finch Design Studio
CUSTOMER TYPE
Individual / Sole proprietor
PRIMARY CONTACT
Anaya Fernandes
CONTACT EMAIL
anaya.fernandes@example.test
CONTACT PHONE
+91 90000 22005
REQUESTED PLAN
Professional - Quarterly
NUMBER OF USERS
4""",
    ]

    predictions = [
        {"label": "form", "confidence": 0.96},
        {"label": "form", "confidence": 0.95},
        {"label": "form", "confidence": 0.94},
        {"label": "form", "confidence": 0.95},
        {"label": "form", "confidence": 0.96},
    ]

    groups = group_page_predictions(
        pages,
        {"page_predictions": predictions}
    )

    assert len(groups) == 5
    assert [group["page_indexes"] for group in groups] == [
        [0], [1], [2], [3], [4]
    ]

    assert all(
        group["label"] == "form"
        for group in groups
    )


def test_form_continuation_with_same_inline_reference_is_not_split():
    pages = [
        """ACCOUNT INTAKE AND SERVICE REQUEST FORM
Customer onboarding | Form reference: AIN-2026-001
Organisation name
Northstar Analytics LLP
Primary contact
Ishita Rao""",

        """CONTINUED
Customer onboarding | Form reference: AIN-2026-001
Declaration
I confirm the submitted details are accurate.""",
    ]

    predictions = [
        {"label": "form", "confidence": 0.96},
        {"label": "form", "confidence": 0.95},
    ]

    groups = group_page_predictions(
        pages,
        {"page_predictions": predictions}
    )

    assert len(groups) == 1
    assert groups[0]["page_indexes"] == [0, 1]


