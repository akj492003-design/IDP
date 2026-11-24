from app.services import field_extractors as fx


FORM_OCR_INLINE = """ACCOUNT INTAKE AND SERVICE REQUEST FORM
APPLICATION DATE
14 November 2026
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
SUBSCRIPTION AND SERVICE PREFERENCES
Requested plan
Professional - Monthly
Number of users
18
Primary use
Automated document intake and invoice review
Billing frequency
Monthly
Invoice delivery method
Email (PDF invoice)
Support channel
Email
PRIVACY, DECLARATION AND ACCEPTANCE
Privacy acknowledgement: Accepted
Terms acceptance: Accepted
Declaration: I, Ishita Rao, confirm that the information entered in this form is accurate and that I am authorised to submit this request for the applicant named above. SUBMISSION DATE 14 November 2026 APPLICANT / AUTHORISED PERSON Ishita Rao
Form status: SUBMITTED | Record status: COMPLETE"""


def test_form_promotes_primary_contact_to_top_level_field():
    fields = fx.extract_form_fields(FORM_OCR_INLINE)
    assert fields["primary_contact"] == "Ishita Rao"
    assert fields["labelled_field_values"]["primary_contact"] == "Ishita Rao"


def test_form_extracts_contact_email_and_phone_not_channel_heading():
    fields = fx.extract_form_fields(FORM_OCR_INLINE)
    assert fields["email"] == "ishita.rao@example.test"
    assert fields["phone"] == "+91 90000 22001"
    assert fields["email"] != "PRIVACY, DECLARATION AND ACCEPTANCE"


def test_form_separates_application_and_submission_dates():
    fields = fx.extract_form_fields(FORM_OCR_INLINE)
    assert fields["application_date"] == "14 November 2026"
    assert fields["submission_date"] == "14 November 2026"


def test_form_separates_authorised_person_and_cleans_declaration_inline():
    fields = fx.extract_form_fields(FORM_OCR_INLINE)
    assert fields["declarant_authorised_person"] == "Ishita Rao"
    declaration = fields["declaration_statement"]
    assert declaration.startswith("I, Ishita Rao, confirm")
    assert "SUBMISSION DATE" not in declaration.upper()
    assert "AUTHORISED PERSON" not in declaration.upper()


def test_form_separates_metadata_when_ocr_places_each_label_on_its_own_line():
    text = """ACCOUNT INTAKE FORM
Primary contact
Ishita Rao
Contact email
ishita.rao@example.test
Contact phone
+91 90000 22001
Declaration
I, Ishita Rao, confirm the submitted information is accurate.
SUBMISSION DATE
14 November 2026
APPLICANT / AUTHORISED PERSON
Ishita Rao
Form status: SUBMITTED | Record status: COMPLETE"""
    fields = fx.extract_form_fields(text)
    assert fields["primary_contact"] == "Ishita Rao"
    assert fields["submission_date"] == "14 November 2026"
    assert fields["declarant_authorised_person"] == "Ishita Rao"
    assert fields["declaration_statement"] == "I, Ishita Rao, confirm the submitted information is accurate."


def test_support_channel_email_is_not_used_as_contact_email():
    text = """ACCOUNT INTAKE FORM
Requested Plan
Professional - Monthly
Support Channel
Email
PRIVACY, DECLARATION AND ACCEPTANCE
Terms Acceptance: Accepted"""
    fields = fx.extract_form_fields(text)
    assert fields.get("email") is None
    assert fields.get("email_addresses") is None
    assert fields["support_channel"] == "Email"


def test_human_facing_export_keeps_primary_contact_and_clean_split_fields():
    from app.services import bulk_export as be

    fields = fx.extract_form_fields(FORM_OCR_INLINE)
    prepared = be.prepare_export_display_fields("form", fields)

    assert prepared["primary_contact"] == "Ishita Rao"
    assert prepared["email"] == "ishita.rao@example.test"
    assert prepared["phone"] == "+91 90000 22001"
    assert prepared["submission_date"] == "14 November 2026"
    assert prepared["declarant_authorised_person"] == "Ishita Rao"
    assert "SUBMISSION DATE" not in prepared["declaration_statement"].upper()
