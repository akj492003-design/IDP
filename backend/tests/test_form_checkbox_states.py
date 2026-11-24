from app.services.field_extractors import extract_form_fields


def test_form_exposes_checked_and_unchecked_textual_checkbox_states():
    fields = extract_form_fields(
        "APPLICATION FORM\n"
        "Gender: [x] Male [ ] Female\n"
        "Terms and Conditions: [X] Accepted\n"
        "Marketing Consent: [ ] Yes"
    )

    states = fields["checkbox_option_states"]
    assert states == [
        {"label": "Male", "status": "checked"},
        {"label": "Female", "status": "unchecked"},
        {"label": "Accepted", "status": "checked"},
        {"label": "Yes", "status": "unchecked"},
    ]
    assert fields["selected_checkbox_radio_options"] == ["Male", "Accepted"]


def test_form_recognizes_unicode_checkbox_symbols_when_ocr_preserves_them():
    fields = extract_form_fields(
        "REGISTRATION FORM\n"
        "☒ I accept the Terms and Conditions\n"
        "☑ I acknowledge the Privacy Policy\n"
        "☐ I agree to marketing emails\n"
        "□ I request a callback"
    )

    states = fields["checkbox_option_states"]
    assert [item["status"] for item in states] == [
        "checked", "checked", "unchecked", "unchecked"
    ]
    assert fields["terms_acceptance"] == "I accept the Terms and Conditions"
    assert fields["privacy_acknowledgement"] == "I acknowledge the Privacy Policy"
    assert fields["selected_checkbox_radio_options"] == [
        "I accept the Terms and Conditions",
        "I acknowledge the Privacy Policy",
    ]


def test_form_does_not_promote_unchecked_terms_or_privacy_to_acceptance():
    fields = extract_form_fields(
        "APPLICATION FORM\n"
        "[ ] I accept the Terms and Conditions\n"
        "[ ] I acknowledge the Privacy Policy"
    )

    assert "terms_acceptance" not in fields
    assert "privacy_acknowledgement" not in fields
    assert fields.get("selected_checkbox_radio_options", []) == []
