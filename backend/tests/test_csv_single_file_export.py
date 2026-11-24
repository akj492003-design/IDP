import csv
import io

from app.services.bulk_export import build_bulk_export


def make_form_segment(index, organisation, email, plan, users):
    fields = {
        "form_title_type": "ACCOUNT INTAKE AND SERVICE",
        "application_date": "14 November 2026",
        "email": "PRIVACY, DECLARATION AND ACCEPTANCE",
        "emails": [email],
        "email_addresses": [email],
        "phone_numbers": ["+91 90000 22001"],
        "labelled_field_values": {
            "Organisation Name": organisation,
            "Primary Contact": "Test Contact",
            "Contact Email": email,
            "Contact Phone": "+91 90000 22001",
            "Requested Plan": plan,
            "Number Of Users": users,
            "Privacy Acknowledgement": "Accepted",
            "Terms Acceptance": "Accepted",
            "Select Each Acknowledgement That Applies":
                "[X] Privacy acknowledgement - Accepted",
            "X Privacy Acknowledgement Accepted":
                "[X] I accept the service terms and conditions - Accepted",
            "Form Status": "SUBMITTED | Record status: COMPLETE",
        },
        "form_fields": [
            {"label": "Industry", "value": "Business Services"},
        ],
        "date_candidates": ["14 November 2026"],
        "dates": ["14 November 2026"],
        "requested_plan": plan,
        "number_of_users": users,
        "organisation_name": organisation,
        "customer_type": "Business",
        "selected_checkbox_radio_options": [
            "Privacy acknowledgement - Accepted",
            "I accept the service terms and conditions - Accepted",
        ],
        "declaration_statement": "I confirm the information is accurate.",
    }

    return {
        "document_index": index,
        "page_range": {"start": index, "end": index},
        "page_count": 1,
        "classification": {
            "label": "form",
            "model": "EfficientNetB0",
            "classification_conflict": True,
            "visual_label": "resume",
            "visual_confidence": 0.944,
            "scores": [
                {"label": "resume", "score": 0.944},
                {"label": "form", "score": 0.047},
                {"label": "invoice", "score": 0.009},
            ],
        },
        "insights": {
            "key_fields": fields,
            "field_provenance": {},
            "validation_issues": [],
            "review_reasons": [],
        },
        "boundary_warnings": [],
    }


def make_form_document():
    return {
        "_id": "csv-form-test",
        "file_name": "five_forms.pdf",
        "result": {
            "documents": [
                make_form_segment(
                    1, "Northstar Analytics LLP",
                    "ishita.rao@example.test",
                    "Professional - Monthly", "18",
                ),
                make_form_segment(
                    2, "Maple Grove Clinic",
                    "kabir.nair@example.test",
                    "Team - Annual", "32",
                ),
            ]
        },
    }


def test_form_csv_has_one_row_per_document_and_clean_fields():
    payload, mime, filename = build_bulk_export(
        make_form_document(), "csv"
    )

    assert mime.startswith("text/csv")
    assert filename.endswith(".csv")
    assert not payload.startswith(bytes((0x50, 0x4B, 0x03, 0x04)))

    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    rows = list(reader)

    assert len(rows) == 2

    row1, row2 = rows

    assert row1["Organisation Name"] == "Northstar Analytics LLP"
    assert row1["Contact Email"] == "ishita.rao@example.test"
    assert row1["Requested Plan"] == "Professional - Monthly"
    assert row1["Number Of Users"] == "18"

    assert row2["Organisation Name"] == "Maple Grove Clinic"
    assert row2["Contact Email"] == "kabir.nair@example.test"
    assert row2["Requested Plan"] == "Team - Annual"
    assert row2["Number Of Users"] == "32"

    # Bad email text must not be exported as an email.
    assert "Email" not in reader.fieldnames
    assert "Emails" not in reader.fieldnames
    assert "Email Addresses" not in reader.fieldnames

    # Generic OCR candidates and duplicated nested containers are excluded.
    assert "Date Candidates" not in reader.fieldnames
    assert "Dates" not in reader.fieldnames
    assert not any(
        name.startswith("Labelled Field Values")
        for name in reader.fieldnames
    )

    # Checkbox values are normalized and deduplicated.
    assert row1["Privacy Acknowledgement"] == "Accepted"
    assert row1["Terms Acceptance"] == "Accepted"

    assert row1["Industry"] == "Business Services"


def test_single_form_csv_contains_exactly_one_document_row():
    document = make_form_document()
    selected = document["result"]["documents"][1]
    document["result"]["documents"] = [selected]

    payload, mime, filename = build_bulk_export(
        document, "csv", scope="current"
    )

    rows = list(
        csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    )

    assert mime.startswith("text/csv")
    assert filename.endswith(".csv")
    assert len(rows) == 1
    assert rows[0]["Document Index"] == "2"
    assert rows[0]["Organisation Name"] == "Maple Grove Clinic"
