from app.services.bulk_export import order_extracted_fields


def test_resume_candidate_name_is_first_and_unknown_fields_survive():
    fields = {
        "emails": ["candidate@example.com"],
        "academic_achievements": ["Merit award"],
        "candidate_name": "Aarav Kulkarni",
        "custom_extracted_field": "Original value",
        "phone_numbers": ["9000010001"],
    }

    ordered = order_extracted_fields("resume", fields)

    assert list(ordered)[:3] == [
        "candidate_name", "emails", "phone_numbers"
    ]
    assert ordered["custom_extracted_field"] == "Original value"
    assert ordered["academic_achievements"] == ["Merit award"]


def test_invoice_supplier_precedes_invoice_number_and_customer():
    fields = {
        "grand_total": "1180.00",
        "customer_name": "Buyer Ltd",
        "invoice_number": "INV-001",
        "supplier_name": "Supplier Ltd",
    }

    ordered = order_extracted_fields("invoice", fields)

    assert list(ordered) == [
        "supplier_name", "invoice_number", "customer_name", "grand_total"
    ]


def test_form_applicant_name_is_first():
    fields = {
        "form_questions_answers": {"reason": "Example"},
        "application_reference_number": "APP-001",
        "form_title_type": "Admission Form",
        "applicant_name": "Priya Menon",
    }

    ordered = order_extracted_fields("form", fields)

    assert list(ordered) == [
        "applicant_name",
        "form_title_type",
        "application_reference_number",
        "form_questions_answers",
    ]


def test_unknown_document_type_preserves_original_order():
    fields = {"second": "2", "first": "1", "third": "3"}

    assert list(order_extracted_fields("unknown", fields)) == list(fields)


def test_ordering_does_not_mutate_original_fields():
    fields = {
        "emails": ["test@example.com"],
        "candidate_name": "Test Person",
    }
    original_keys = list(fields)

    order_extracted_fields("resume", fields)

    assert list(fields) == original_keys


def test_non_dictionary_input_returns_empty_dictionary():
    assert order_extracted_fields("resume", None) == {}
    assert order_extracted_fields("invoice", []) == {}
