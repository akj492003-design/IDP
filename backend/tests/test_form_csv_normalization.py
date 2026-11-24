from app.services.bulk_export import _normalise_form_csv_fields


def test_duplicate_checkbox_columns_are_removed():
    fields = {
        "Organisation Name": "Northstar Analytics LLP",
        "Select Each Acknowledgement That Applies": "PRIVACY",
        "X Privacy Acknowledgement Accepted": "Accepted",
        "Selected Checkbox Radio Options": ["Privacy Accepted"],
        "Privacy Acknowledgement": "Accepted",
        "Terms Acceptance": "Accepted",
    }

    result = _normalise_form_csv_fields(fields)

    assert result["Organisation Name"] == "Northstar Analytics LLP"
    assert result["Privacy Acknowledgement"] == "Accepted"
    assert result["Terms Acceptance"] == "Accepted"
    assert "Select Each Acknowledgement That Applies" not in result
    assert "X Privacy Acknowledgement Accepted" not in result
    assert "Selected Checkbox Radio Options" not in result


def test_email_columns_are_consolidated():
    fields = {
        "Email": "PRIVACY AND DECLARATION",
        "Email Addresses": ["ishita.rao@example.test"],
    }

    result = _normalise_form_csv_fields(fields)

    assert result["Contact Email"] == "ishita.rao@example.test"


def test_declaration_metadata_is_split():
    fields = {
        "Declaration Statement": (
            "I confirm the information is accurate. "
            "SUBMISSION DATE 14 November 2026 "
            "APPLICANT / AUTHORISED PERSON Ishita Rao "
            "SUBMITTED | Record status: COMPLETE"
        ),
        "Form Status": "SUBMITTED | Record status: COMPLETE",
    }

    result = _normalise_form_csv_fields(fields)

    assert result["Declaration Statement"] == (
        "I confirm the information is accurate."
    )
    assert result["Submission Date"] == "14 November 2026"
    assert result["Declarant / Authorised Person"] == "Ishita Rao"
    assert result["Submission Status"] == "Submitted"
    assert result["Record Status"] == "Complete"
    assert "Form Status" not in result


def test_account_intake_title_is_completed():
    result = _normalise_form_csv_fields({
        "Form Title": "ACCOUNT INTAKE AND SERVICE"
    })

    assert result["Form Title"] == (
        "ACCOUNT INTAKE AND SERVICE REQUEST FORM"
    )
