from app.services.field_extractors import (
    extract_form_fields,
    extract_invoice_fields,
    extract_resume_fields,
)


def test_invoice_extractor_returns_supported_labelled_fields_and_item_rows():
    text = """TAX INVOICE
Invoice Number: INV-2026-001
Invoice Date: 10/11/2026
Due Date: 20/11/2026
Bill From: Example Supplier Pvt Ltd
12 Market Road, Pune
GSTIN: 27ABCDE1234F1Z5
Email: billing@example.com
Phone: 9876543210
Bill To: Example Customer Ltd
45 Business Park, Mumbai
Description\tQty\tUnit Price\tAmount
Cloud hosting\t2\t1000.00\t2000.00
Subtotal: INR 2000.00
GST Amount: INR 360.00
Grand Total: INR 2360.00
"""
    fields = extract_invoice_fields(text)
    assert fields["invoice_number"] == "INV-2026-001"
    assert fields["invoice_date"] == "10/11/2026"
    assert fields["due_date"] == "20/11/2026"
    assert "Example Supplier Pvt Ltd" in fields["seller_company_details"]
    assert "Example Customer Ltd" in fields["buyer_customer_details"]
    assert fields["gstin"] == ["27ABCDE1234F1Z5"]
    assert fields["email_addresses"] == ["billing@example.com"]
    assert fields["phone_numbers"] == ["9876543210"]
    assert fields["subtotal"] == "INR 2000.00"
    assert fields["tax_amount"] == "INR 360.00"
    assert fields["grand_total"] == "INR 2360.00"
    assert fields["line_items"][0]["description"] == "Cloud hosting"
    assert fields["line_items"][0]["quantity"] == "2"
    assert fields["line_items"][0]["unit_price"] == "1000.00"


def test_resume_extractor_returns_only_text_supported_resume_facts():
    text = """Jane Doe
jane@example.com
9876543210
Location: Pune, Maharashtra
Professional Summary
Data analyst with experience in reporting and Python.
Technical Skills
Python, SQL, Power BI
Work Experience
Data Analyst at Example Corp (2 years)
Education
BSc Statistics, Example University
Projects
Customer churn analysis
Certifications
Google Data Analytics Certificate
Academic Achievements
First class with distinction
"""
    fields = extract_resume_fields(text)
    assert fields["candidate_name"] == "Jane Doe"
    assert fields["emails"] == ["jane@example.com"]
    assert fields["phone_numbers"] == ["9876543210"]
    assert fields["location_address"] == "Pune, Maharashtra"
    assert "Data analyst with experience" in fields["professional_summary"]
    assert any("Python" in items for items in fields["technical_skills"].values())
    assert any("BSc Statistics" in str(record) for record in fields["educational_qualifications"])
    assert any("Customer churn analysis" in str(record) for record in fields["projects"])
    assert fields["experience_duration"] == ["2 years"]


def test_form_extractor_captures_labelled_fields_and_marked_options():
    text = """APPLICATION FORM
Applicant Name: Rahul Sharma
Date of Birth: 04/09/2003
Address: Solapur, Maharashtra
Email Address: rahul@example.com
Contact Number: 9876543210
Application Number: APP-1001
Reason for application: Education support
Gender: [x] Male [ ] Female
Fee Amount: INR 500
"""
    fields = extract_form_fields(text)
    assert fields["form_title_type"] == "APPLICATION FORM"
    assert fields["applicant_name"] == "Rahul Sharma"
    assert fields["date_of_birth"] == "04/09/2003"
    assert fields["address"] == "Solapur, Maharashtra"
    assert fields["application_reference_number"] == "APP-1001"
    assert fields["email_addresses"] == ["rahul@example.com"]
    assert fields["phone_numbers"] == ["9876543210"]
    assert fields["selected_checkbox_radio_options"] == ["Male"]
    assert fields["form_questions_answers"]["reason_for_application"] == "Education support"


def test_invoice_date_parser_rejects_invoice_number_fragment_and_accepts_month_name_dates():
    from app.services.field_extractors import _extract_dates

    assert _extract_dates("BP/26-27/0042\n11-Nov-2026\n25-Nov-2026") == ["11-Nov-2026", "25-Nov-2026"]


def test_invoice_tax_amount_sums_cgst_and_sgst_when_total_tax_is_not_labelled():
    text = """TAX INVOICE
Invoice Number: BP/26-27/0042
Subtotal: INR 31,000.00
CGST: INR 2,790.00
SGST: INR 2,790.00
Grand Total: INR 36,580.00
"""
    fields = extract_invoice_fields(text)
    assert fields["tax_amount"] == "5580.00"


def test_resume_name_extractor_does_not_return_form_or_invoice_headings():
    from app.services.field_extractors import _extract_resume_name

    assert _extract_resume_name("GENERAL APPLICATION FORM\nSUPPLIER DETAILS INVOICE DETAILS") is None


def test_form_extractor_supports_label_and_value_on_separate_ocr_lines():
    text = """APPLICATION FORM
Applicant Name
Aarav Kulkarni
Date of Birth
14/08/1998
Marital Status
Single
PAN Number
ABCDE1234F
"""
    fields = extract_form_fields(text)
    assert fields["applicant_name"] == "Aarav Kulkarni"
    assert fields["date_of_birth"] == "14/08/1998"
    assert fields["marital_status"] == "Single"
    assert fields["pan_number"] == "ABCDE1234F"


def test_invoice_extractor_separates_grand_total_paid_and_balance_and_validates_math():
    text = """TAX INVOICE
Invoice Number: INV-44
Invoice Date: 12-Nov-2026
Description | HSN/SAC | Qty | Unit | Unit Price | Taxable Value | GST % | Amount
Widget A | 8471 | 2 | pcs | 500.00 | 990.00 | 18% | 990.00
Subtotal: 990.00
CGST @ 9%: 89.10
SGST @ 9%: 89.10
Grand Total: 1168.20
Amount Paid: 500.00
Balance Due: 668.20
"""
    fields = extract_invoice_fields(text)

    assert fields["grand_total"] == "1168.20"
    assert fields["amount_paid"] == "500.00"
    assert fields["balance_due"] == "668.20"
    assert fields["tax_breakdown"] == {"cgst": "89.10", "sgst": "89.10"}
    assert fields["tax_amount"] == "178.20"
    assert fields["line_items"][0]["hsn_sac_code"] == "8471"
    assert fields["line_items"][0]["quantity"] == "2"
    assert fields["line_items"][0]["unit"] == "pcs"
    assert fields["line_items"][0]["unit_price"] == "500.00"
    assert fields["line_items"][0]["taxable_value"] == "990.00"
    assert fields["calculation_validation"]["grand_total"]["status"] == "match"
    assert fields["calculation_validation"]["payment_balance"]["status"] == "match"
    assert fields["calculation_validation"]["line_items_subtotal"]["status"] == "match"
    assert fields["calculation_validation"]["line_item_tax_total"]["status"] == "match"


def test_form_extractor_preserves_generic_and_separate_line_label_values():
    text = """APPLICATION FORM
Applicant Name
Aarav Kulkarni
Date of Birth
14/08/1998
Emergency Contact: 9876543210
Blood Group: O+
"""
    fields = extract_form_fields(text)
    assert fields["applicant_name"] == "Aarav Kulkarni"
    assert fields["date_of_birth"] == "14/08/1998"
    assert fields["labelled_field_values"]["emergency_contact"] == "9876543210"
    assert fields["labelled_field_values"]["applicant_name"] == "Aarav Kulkarni"


def test_resume_extractor_supports_additional_sections():
    text = """Jane Doe
Professional Summary
Data analyst.
Technical Skills
Python, SQL
Internships
Data Science Intern at Example Labs
Languages
English, Marathi
Publications
Customer Churn Study
Volunteer Experience
Community data training
"""
    fields = extract_resume_fields(text)
    assert "Data Science Intern at Example Labs" in fields["internships"]
    assert "English" in fields["languages"]
    assert "Customer Churn Study" in fields["publications"]
    assert "Community data training" in fields["volunteer_experience"]


def test_field_provenance_links_extracted_values_to_original_pages_without_faking_confidence():
    from app.services.field_extractors import build_field_provenance

    provenance = build_field_provenance(
        {"invoice_number": "INV-100", "line_items": [{"description": "Cloud hosting"}], "calculated_tax": "90.00"},
        [
            {"page_number": 1, "text": "TAX INVOICE Invoice Number: INV-100"},
            {"page_number": 2, "text": "Cloud hosting"},
        ],
    )
    assert provenance["invoice_number"]["source_pages"] == [1]
    assert provenance["line_items"]["source_pages"] == [2]
    assert provenance["calculated_tax"]["source_pages"] == []
    assert provenance["invoice_number"]["confidence"] is None


def test_invoice_calculation_mismatch_is_reported_without_changing_source_totals():
    fields = extract_invoice_fields(
        """TAX INVOICE
Invoice Number: INV-45
Subtotal: 1000.00
CGST: 90.00
SGST: 90.00
Grand Total: 1190.00
"""
    )
    check = fields["calculation_validation"]["grand_total"]
    assert check["status"] == "mismatch"
    assert fields["grand_total"] == "1190.00"
    assert check["expected_amount"] == "1180.00"


def test_form_label_without_value_does_not_consume_the_next_field_label():
    fields = extract_form_fields(
        """APPLICATION FORM
Emergency Contact:
Blood Group: O+
"""
    )
    assert "emergency_contact" not in fields.get("labelled_field_values", {})
    assert fields["labelled_field_values"]["blood_group"] == "O+"


def test_invoice_table_parser_preserves_empty_pipe_columns():
    text = """TAX INVOICE
Description | HSN/SAC | Qty | Unit | Unit Price | Taxable Value | GST % | Amount
Service A |  | 2 |  | 500.00 | 1000.00 | 18% | 1180.00
Subtotal: 1000.00
CGST: 90.00
SGST: 90.00
Grand Total: 1180.00
"""
    fields = extract_invoice_fields(text)
    item = fields["line_items"][0]
    assert item["description"] == "Service A"
    assert "hsn_sac_code" not in item
    assert item["quantity"] == "2"
    assert "unit" not in item
    assert item["unit_price"] == "500.00"
    assert item["taxable_value"] == "1000.00"
    assert item["tax_rate"] == "18%"
    assert item["line_total"] == "1180.00"
    assert fields["calculation_validation"]["line_items_subtotal"]["status"] == "match"


def test_invoice_line_total_is_not_misrepresented_as_taxable_subtotal():
    text = """TAX INVOICE
Description | Qty | Rate | Amount
Service A | 1 | 1000.00 | 1180.00
Subtotal: 1000.00
CGST: 90.00
SGST: 90.00
Grand Total: 1180.00
"""
    fields = extract_invoice_fields(text)
    assert fields["line_items"][0]["line_total"] == "1180.00"
    assert "line_items_subtotal" not in fields.get("calculation_validation", {})


def test_sample_application_form_extracts_reference_type_and_applicant_fields_without_age_as_experience():
    text = """FICTIONAL SAMPLE - FOR IDP EXTRACTION TESTING ONLY
GENERAL APPLICATION FORM
Sample completed application | Reference: APP-2026-0148 | All information below is fictional.
APPLICATION DETAILS
Application Reference Number
APP-2026-0148
Application Date
11-Nov-2026
Application Type
General Registration
Purpose / Reason
Request for registration and verification of applicant details.
APPLICANT PERSONAL INFORMATION
Applicant Name
Aarav Kulkarni
Date of Birth
14-Aug-1998
Age
28 years
Gender
Male
Email Address
aarav.kulkarni@example.com
Mobile Number
+91 98765 43210
Location Address
Solapur
Annual Income
INR 6,50,000
Declaration
I hereby declare that information is correct.
Applicant Signature
"""
    fields = extract_form_fields(text)
    resume_fields = extract_resume_fields(text)

    assert fields["form_title_type"] == "GENERAL APPLICATION FORM"
    assert fields["application_reference_number"] == "APP-2026-0148"
    assert fields["application_date"] == "11-Nov-2026"
    assert fields["application_type"] == "General Registration"
    assert fields["applicant_name"] == "Aarav Kulkarni"
    assert fields["date_of_birth"] == "14-Aug-1998"
    assert fields["age"] == "28 years"
    assert fields["gender"] == "Male"
    assert fields["address"] == "Solapur"
    assert fields["purpose_reason"].startswith("Request for registration")
    assert "request_for_registration_and_verification_of_applicant_details" not in fields.get("form_questions_answers", {})
    assert not resume_fields.get("candidate_name")
    assert "experience_duration" not in resume_fields


def test_invoice_extracts_party_names_separately_from_explicit_party_blocks():
    text = """TAX INVOICE
Invoice Number: BP/26-27/0042
BILL FROM
BluePeak Office Supplies
24 Market Road, Andheri East
Mumbai, Maharashtra 400069
GSTIN: 27ABCDE1234F1Z5
Email: billing@bluepeak.example
BILL TO
Northstar Analytics Pvt Ltd
5th Floor, Meridian Business Park
Mumbai
GSTIN: 27FGHIJ5678K1Z4
Description | HSN/SAC | Qty | Unit Price | Amount
Office Paper | 4802 | 10 | 1000.00 | 11800.00
"""
    fields = extract_invoice_fields(text)
    assert fields["supplier_name"] == "BluePeak Office Supplies"
    assert fields["customer_name"] == "Northstar Analytics Pvt Ltd"
    assert "BluePeak Office Supplies" in fields["seller_company_details"]
    assert "Northstar Analytics Pvt Ltd" in fields["buyer_customer_details"]
    assert fields["supplier_gstin"] == ["27ABCDE1234F1Z5"]
    assert fields["buyer_gstin"] == ["27FGHIJ5678K1Z4"]

def test_native_pdf_invoice_layout_extracts_line_items_parties_and_multiline_tax():
    text = """SAMPLE
BLUEPEAK OFFICE SUPPLIES
TAX INVOICE
Invoice No: BP/26-27/0042
Invoice Date: 11-Nov-2026
Due Date: 25-Nov-2026
BILL FROM
BILL TO
BluePeak Office Supplies
24 Market Road, Andheri East
Mumbai, Maharashtra 400069
GSTIN: 27ABCDE1234F1Z5
State Code: 27
Northstar Analytics Pvt Ltd
5th Floor, Meridian Business Park
Baner Road, Pune, Maharashtra 411045
GSTIN: 27FGHIJ5678K1Z4
State Code: 27
ITEM DETAILS
#
Description of Goods
HSN/SAC
Qty
Rate (INR)
Taxable Value (INR)
1
A4 Copier Paper - 75 GSM (ream)
4802
50
280.00
14,000.00
2
Laser Toner Cartridge - Black
8443
10
1,250.00
12,500.00
3
Document File Folders
4820
90
50.00
4,500.00
Taxable Amount
INR 31,000.00
CGST @ 9%
INR 2,790.00
SGST @ 9%
INR 2,790.00
Total Tax
INR 5,580.00
GRAND TOTAL
INR 36,580.00
"""

    fields = extract_invoice_fields(text)

    assert fields["invoice_number"] == "BP/26-27/0042"

    assert fields["supplier_name"] == "BluePeak Office Supplies"
    assert fields["customer_name"] == "Northstar Analytics Pvt Ltd"

    assert fields["supplier_gstin"] == ["27ABCDE1234F1Z5"]
    assert fields["buyer_gstin"] == ["27FGHIJ5678K1Z4"]

    assert "BluePeak Office Supplies" in fields["seller_company_details"]
    assert "Northstar Analytics Pvt Ltd" in fields["buyer_customer_details"]

    assert fields["line_items"] == [
        {
            "description": "A4 Copier Paper - 75 GSM (ream)",
            "hsn_sac_code": "4802",
            "quantity": "50",
            "unit_price": "280.00",
            "taxable_value": "14,000.00",
        },
        {
            "description": "Laser Toner Cartridge - Black",
            "hsn_sac_code": "8443",
            "quantity": "10",
            "unit_price": "1,250.00",
            "taxable_value": "12,500.00",
        },
        {
            "description": "Document File Folders",
            "hsn_sac_code": "4820",
            "quantity": "90",
            "unit_price": "50.00",
            "taxable_value": "4,500.00",
        },
    ]

    assert fields["tax_breakdown"] == {
        "cgst": "2,790.00",
        "sgst": "2,790.00",
    }

    assert fields["calculation_validation"]["grand_total"]["status"] == "match"
    assert fields["calculation_validation"]["line_items_subtotal"]["status"] == "match"


def test_resume_structured_sections_reconstruct_wrapped_text_and_group_records():
    text = """AARAV KULKARNI
Professional Summary
Statistics graduate with experience supporting reporting workflows
data quality checks
spreadsheet analysis
and operational dashboards.
Comfortable translating business questions into clear summaries and repeatable analytical tasks.
Technical Skills
Python, SQL, Excel, Pandas, NumPy, Power BI, data cleaning, exploratory data analysis, descriptive statistics, dashboard reporting, and documentation.
Work Experience
Data Operations Associate
Northstar Analytics Services
Jan 2024 - Oct 2026
Prepared weekly operational reports and reconciled source spreadsheets before publication.
Used SQL queries and Excel checks to identify missing
duplicated
and inconsistent records.
Coordinated with analysts to document metric definitions and recurring reporting procedures.
Reporting Intern
Cedar Metrics Studio
Jun 2023 - Dec 2023
Cleaned tabular datasets and prepared summary tables for internal reporting.
Assisted with chart preparation and validation of monthly performance summaries.
Education
Degree Bachelor of Science (Statistics)
Institution Deccan College of Arts and Science, Solapur
Graduation Year 2023
Academic Result First Class
Projects
Retail Sales Analysis - Analysed transaction data using Python and Pandas; prepared category-wise summaries and monthly trend charts.
Customer Feedback Classification - Built a baseline text-classification experiment and reviewed model predictions using a held-out
evaluation set.
Certifications and Achievements
SQL for Data Analysis - Sample Learning Academy
2025
Python for Data Analytics - Sample Learning Academy
2024
Department merit recognition - Statistics Programme
2023
"""

    fields = extract_resume_fields(text)

    # 1. Professional summary is a reconstructed paragraph.
    assert isinstance(fields["professional_summary"], str)
    assert "reporting workflows data quality checks" in fields["professional_summary"]
    assert "spreadsheet analysis and operational dashboards" in fields["professional_summary"]

    # 2. Skills are categorized; conjunctions are not standalone entries.
    skill_values = [
        item
        for group in fields["technical_skills"].values()
        for item in group
    ]
    assert "Python" in skill_values
    assert "Power BI" in skill_values
    assert "Documentation" in skill_values
    assert "and" not in [item.lower() for item in skill_values]

    # 3. Employer, title, dates and responsibilities remain grouped.
    experience = fields["work_experience"]
    assert len(experience) == 2
    assert experience[0]["job_title"] == "Data Operations Associate"
    assert experience[0]["company"] == "Northstar Analytics Services"
    assert experience[0]["start_date"] == "Jan 2024"
    assert experience[0]["end_date"] == "Oct 2026"
    assert (
        "missing, duplicated, and inconsistent records."
        in experience[0]["responsibilities"][1]
    )
    assert experience[1]["job_title"] == "Reporting Intern"
    assert experience[1]["company"] == "Cedar Metrics Studio"

    assert all(
        "Reporting Intern" not in item
        and "Cedar Metrics Studio" not in item
        for item in experience[0]["responsibilities"]
    )
    assert all(
        "Data Operations Associate" not in item
        and "Northstar Analytics Services" not in item
        for item in experience[1]["responsibilities"]
    )
    assert any(
        "Cleaned tabular datasets" in item
        for item in experience[1]["responsibilities"]
    )

    # 4. Education values become one qualification record, not duplicates.
    education = fields["educational_qualifications"]
    assert len(education) == 1
    assert education[0]["degree"] == "Bachelor of Science (Statistics)"
    assert education[0]["institution"] == "Deccan College of Arts and Science, Solapur"
    assert education[0]["graduation_year"] == "2023"
    assert education[0]["academic_result"] == "First Class"
    assert "academic_details" not in fields
    assert "college_university_details" not in fields

    # Projects preserve title and wrapped description.
    projects = fields["projects"]
    assert len(projects) == 2
    assert projects[1]["title"] == "Customer Feedback Classification"
    assert projects[1]["description"].endswith("evaluation set.")

    # 5. Combined certification heading is split into correct sections.
    assert len(fields["certifications"]) == 2
    assert len(fields["academic_achievements"]) == 1
    assert fields["certifications"][0]["year"] == "2025"
    assert fields["academic_achievements"][0]["title"] == "Department merit recognition"

    # No duplicated standalone employer/title fields.
    assert "company_names" not in fields
    assert "job_titles" not in fields


def test_account_intake_form_extracts_plan_users_and_operational_preferences():
    text = """ACCOUNT INTAKE FORM
Requested Plan: Professional - Monthly
Number of Users
18
Organisation Name: Acme Software Pvt Ltd
Customer Type
Enterprise
Primary Use: Document processing
Billing Frequency
Monthly
Invoice Delivery Method: Email
Privacy Acknowledgement: Yes
Preferred Support Channel
Email
Terms and Conditions: Accepted
"""

    fields = extract_form_fields(text)

    assert fields["form_title_type"] == "ACCOUNT INTAKE FORM"
    assert fields["requested_plan"] == "Professional - Monthly"
    assert fields["number_of_users"] == "18"
    assert fields["organisation_name"] == "Acme Software Pvt Ltd"
    assert fields["customer_type"] == "Enterprise"
    assert fields["primary_use"] == "Document processing"
    assert fields["billing_frequency"] == "Monthly"
    assert fields["invoice_delivery_method"] == "Email"
    assert fields["privacy_acknowledgement"] == "Yes"
    assert fields["support_channel"] == "Email"
    assert fields["terms_acceptance"] == "Accepted"


def test_account_intake_fields_support_all_values_on_separate_lines():
    text = """CUSTOMER ONBOARDING FORM
Requested Plan
Professional - Monthly
Number of Users
18
Organisation Name
Acme Software Pvt Ltd
Customer Type
Enterprise
Primary Use
Document processing
Billing Frequency
Monthly
Invoice Delivery Method
Email
Privacy Acknowledgement
Yes
Support Channel
Email
Terms Acceptance
Accepted
"""

    fields = extract_form_fields(text)

    expected = {
        "requested_plan": "Professional - Monthly",
        "number_of_users": "18",
        "organisation_name": "Acme Software Pvt Ltd",
        "customer_type": "Enterprise",
        "primary_use": "Document processing",
        "billing_frequency": "Monthly",
        "invoice_delivery_method": "Email",
        "privacy_acknowledgement": "Yes",
        "support_channel": "Email",
        "terms_acceptance": "Accepted",
    }
    for field, value in expected.items():
        assert fields[field] == value


def test_account_intake_form_is_not_misrouted_as_resume():
    text = """ACCOUNT INTAKE FORM
Organisation Name: Acme Software Pvt Ltd
Requested Plan: Professional - Monthly
Number of Users: 18
Customer Type: Enterprise
"""

    fields = extract_resume_fields(text)

    assert "candidate_name" not in fields



def test_marked_terms_and_privacy_choices_are_mapped_only_when_positive():
    positive = extract_form_fields("""ACCOUNT INTAKE FORM
[ x ] I agree to the Terms & Conditions
[✓] I acknowledge the Privacy Policy
""")
    assert "I agree to the Terms & Conditions" in positive["terms_acceptance"]
    assert "Privacy Policy" in positive["privacy_acknowledgement"]

    negative = extract_form_fields("""ACCOUNT INTAKE FORM
[x] I do not agree to the Terms and Conditions
""")
    assert "terms_acceptance" not in negative



def test_support_channel_phone_on_next_line_is_not_mistaken_for_a_new_field():
    fields = extract_form_fields("""ACCOUNT INTAKE FORM
Support Channel
Phone
Terms Acceptance
Accepted
""")
    assert fields["support_channel"] == "Phone"
    assert fields["terms_acceptance"] == "Accepted"



def test_form_extractor_captures_explicit_multiline_declaration_until_next_field():
    fields = extract_form_fields("""ACCOUNT INTAKE FORM
Declaration
I hereby declare that the information provided is complete and accurate.
I confirm that I am authorised to submit this request on behalf of the organisation.
Terms and Conditions: Accepted
Signature of Applicant: Rahul Sharma
""")
    statement = fields["declaration_statement"]
    assert "information provided is complete and accurate" in statement
    assert "authorised to submit this request" in statement
    assert "Terms and Conditions" not in statement
    assert "Signature" not in statement

def test_invoice_adjustments_are_not_double_counted_in_validation():
    from app.services.field_extractors import extract_invoice_fields

    cases = [
        (
            """TAX INVOICE
Description | Qty | Unit Price | Taxable Value
Workstation computer | 3 | 42000.00 | 126000.00
Barcode label printer | 2 | 8500.00 | 17000.00
Subtotal: INR 143000.00
Discount: INR 0.00
Freight / other charges: INR 1200.00
Taxable value: INR 144200.00
Total Tax: INR 25956.00
Grand Total: INR 170156.00
""",
            "143000.00",
            "144200.00",
            "170156.00",
        ),
        (
            """TAX INVOICE
Description | Qty | Unit Price | Taxable Value
Corrugated shipping boxes | 100 | 42.00 | 4200.00
Protective foam inserts | 50 | 30.00 | 1500.00
Subtotal: INR 5700.00
Discount: INR 500.00
Freight / other charges: INR 0.00
Taxable value: INR 5200.00
Total Tax: INR 936.00
Grand Total: INR 6136.00
""",
            "5700.00",
            "5200.00",
            "6136.00",
        ),
        (
            """TAX INVOICE
Description | Qty | Unit Price | Taxable Value
Document workflow subscription | 1 | 18000.00 | 18000.00
Secure storage add-on | 1 | 4500.00 | 4500.00
Subtotal: INR 22500.00
Discount: INR 0.00
Freight / other charges: INR 750.00
Taxable value: INR 23250.00
Total Tax: INR 4185.00
Grand Total: INR 27435.00
""",
            "22500.00",
            "23250.00",
            "27435.00",
        ),
    ]

    for text, expected_subtotal, expected_taxable, expected_total in cases:
        fields = extract_invoice_fields(text)
        checks = fields["calculation_validation"]

        assert fields["subtotal"].replace("INR ", "").strip() == expected_subtotal
        assert fields["taxable_amount"].replace("INR ", "").strip() == expected_taxable
        assert checks["grand_total"]["status"] == "match"
        assert checks["grand_total"]["expected_amount"] == expected_total
        assert checks["line_items_subtotal"]["status"] == "match"
def test_resume_work_experience_handles_reversed_headers_and_inline_bullets():
    from app.services.field_extractors import _parse_resume_work_experience

    lines = [
        "WORK EXPERIENCE",
        "Senior Data Analyst",
        "BluePeak Retail Analytics",
        "Apr 2023 - Present",
        "Pune, Maharashtra",
        "\u2022 Built Power BI dashboards covering sales, returns, and inventory KPIs for 12 business teams.",
        "MetricSpring Services",
        "Data Analyst",
        "\u2022 Automated weekly SQL and Python reporting, reducing manual preparation time by 35%. Jun 2021 - Mar 2023",
        "Pune, Maharashtra",
        "\u2022 Validated source files and investigated data-quality exceptions before monthly reporting.",
        "\u2022 Prepared customer segmentation and trend summaries for account managers.",
    ]

    records = _parse_resume_work_experience(lines)

    assert len(records) == 2

    first, second = records

    assert first["job_title"] == "Senior Data Analyst"
    assert first["company"] == "BluePeak Retail Analytics"
    assert first["employment_period"] == "Apr 2023 - Present"
    assert "Built Power BI dashboards" in " ".join(first["responsibilities"])

    assert second["job_title"] == "Data Analyst"
    assert second["company"] == "MetricSpring Services"
    assert second["employment_period"] == "Jun 2021 - Mar 2023"

    second_responsibilities = " ".join(second["responsibilities"])
    assert "Automated weekly SQL and Python reporting" in second_responsibilities
    assert "Validated source files" in second_responsibilities
    assert "Prepared customer segmentation" in second_responsibilities
    assert second.get("location") == "Pune, Maharashtra"

def test_invoice_number_candidates_reject_natural_language():
    from app.services.document_pipeline import extract_key_fields
    from app.services.field_extractors import extract_invoice_fields

    text = """TAX INVOICE
Invoice Number: with
Invoice Date: 15-Nov-2026
Supplier: Example Supplies
Invoice No: BC-2026-1048
Grand Total: INR 1200.00
"""

    fields = extract_invoice_fields(text)
    generic = extract_key_fields(text)

    assert fields["invoice_number"] == "BC-2026-1048"
    assert generic["invoice_numbers"] == ["BC-2026-1048"]

    invalid_only = extract_invoice_fields(
        "TAX INVOICE\nInvoice Number: with\nSupplier: Example Supplies"
    )

    assert "invoice_number" not in invalid_only

def test_amount_extraction_preserves_rupee_and_euro_currency_symbols():
    from app.services.field_extractors import _extract_amounts

    assert _extract_amounts("Subtotal: ₹1,250.50\nService fee: €75.00") == [
        "₹1,250.50",
        "€75.00",
    ]


def test_invoice_tax_breakdown_accepts_fullwidth_colon_and_rupee_values():
    text = """TAX INVOICE
Invoice Number: INV-2026-009
CGST @ 9%：₹90.00
SGST @ 9%：₹90.00
Grand Total：₹1,180.00
"""
    fields = extract_invoice_fields(text)

    assert fields["tax_breakdown"] == {
        "cgst": "90.00",
        "sgst": "90.00",
    }
    assert fields["tax_amount"] == "180.00"
    assert fields["grand_total"] == "₹1,180.00"

def test_invoice_extracts_place_of_supply_supplier_state_tax_type_and_gst_rate():
    text = """TAX INVOICE
Supplier State: Maharashtra
Place of Supply: Maharashtra
Tax Type: Intra-state (CGST + SGST)
GST Rate: 18%
Supplier: ByteCraft Office Supplies
Subtotal: INR 32500.00
Taxable Amount: INR 32500.00
CGST: 2925.00
SGST: 2925.00
Total Tax: 5850.00
Grand Total: INR 38350.00
"""

    fields = extract_invoice_fields(text)

    assert fields["place_of_supply"] == "Maharashtra"
    assert fields["supplier_state"] == "Maharashtra"
    assert fields["tax_type"] == "Intra-state (CGST + SGST)"
    assert fields["gst_rate"] == "18%"

    assert fields["tax_breakdown"]["cgst"] == "2925.00"
    assert fields["tax_breakdown"]["sgst"] == "2925.00"
    assert fields["calculation_validation"]["grand_total"]["status"] == "match"


def test_resume_ocr_corrections_are_contextual_and_preserve_raw_input():
    text = """Jane Doe
Professional Summary
Valldated pattems were sumrarized in Data Sclence Foundatlons and Analyt[cs; 12,ooo+ records reviewed.
Technical Skills
Power B1, Power Bl, SQL
Projects
CUSTOMER REVIEW SENTIMENT ANALYS1S - Built a sentiment classifier.
"""
    original_text = text

    fields = extract_resume_fields(text)

    assert text == original_text
    summary = fields["professional_summary"]
    assert "Validated patterns" in summary
    assert "summarized" in summary
    assert "Data Science Foundations" in summary
    assert "Analytics" in summary
    assert "12,000+" in summary

    skill_values = [
        item
        for group in fields["technical_skills"].values()
        for item in group
    ]
    assert "Power BI" in skill_values
    assert "Power B1" not in skill_values
    assert "Power Bl" not in skill_values

    assert fields["projects"][0]["title"] == "CUSTOMER REVIEW SENTIMENT ANALYSIS"


def test_resume_report_ocr_cleanup_repairs_confirmed_errors_and_preserves_urls():
    from app.services.field_extractors import _correct_resume_ocr_text

    raw = (
        "Statistics graduate with practical experience in data cleaning. "
        "exploratory analysis. SQL reporting.\n"
        "Cleaned and validated sales records, documenting missing values "
        "and duplicate, records.\n"
        "Wrote SQl queries to summarize monthly revenue.\n"
        "Prepared Power Bl vlsuals and weekly reports.\n"
        "Analyzed transactlon data across high-performing categorles.\n"
        "Summarized frequently occurring positive and negative thermes.\n"
        "Presented analysls methods and limitations.\n"
        "GitHub. Jupyter Notebook\n"
        "LinkedIn: https://linkedIn.com/in/aaray-mehta-demg"
    )
    original = raw

    corrected = _correct_resume_ocr_text(raw)

    assert raw == original
    assert "data cleaning, exploratory analysis, SQL reporting" in corrected
    assert "duplicate records" in corrected
    assert "Wrote SQL queries" in corrected
    assert "Power BI visuals" in corrected
    assert "transaction data" in corrected
    assert "categories" in corrected
    assert "negative themes" in corrected
    assert "analysis methods" in corrected
    assert "GitHub, Jupyter Notebook" in corrected
    assert "https://linkedIn.com/in/aaray-mehta-demg" in corrected


def test_resume_project_stack_and_relevant_coursework_are_separated():
    from app.services.field_extractors import extract_resume_fields

    text = """Aditya Kiran Jadhav
Professional Summary
MCA student with a B.Sc. in Statistics and machine learning experience.
Education
V.V.P. Institute of Engineering & Technology, Solapur 2025 ? 2027
Master of Computer Applications (MCA) Affiliated with DBATU, Lonere
Sangmeshwar College, Solapur ? Autonomous 2022 ? 2025
Bachelor of Science (B.Sc.) in Statistics Solapur
Projects
Machine Learning-Based Text Sentiment Analysis Research Python, Pandas, Scikit-learn, TF-IDF
Investigated machine learning approaches for text sentiment classification.
Performed text preprocessing and model evaluation.
Statistical Analysis of Social Media Usage Patterns R, Statistics, Google Forms
Conducted a statistical analysis project using primary survey data collected through Google Forms.
Performed data organization, cleaning, statistical analysis, visualization, and interpretation of social media usage patterns.
Applied statistical methods to identify patterns and relationships within the collected survey data.
Certifications
Python Programming - B Grade
Achievements
1st Prize - TechNova 2026 - Awarded first prize for presenting a research paper titled Sentiment Analysis Using ML.
Relevant Coursework
Machine Learning, Artificial Intelligence, Statistics, Data Structures and Algorithms, Database Management Systems, Python Pro-
gramming, Java Programming, Object-Oriented Programming,
SQL, Research Methodology, Agile Methodologies
Data Visualization: Plotly
Matplotlib
Seaborn gramming
Research Methodology
https://www.linkedin.com/in/example-profile
https://github.com/example-profile
"""

    fields = extract_resume_fields(text)

    # Education remains two separate institution/degree records.
    education = fields["educational_qualifications"]
    assert len(education) == 2

    assert education[0]["degree"] == "Master of Computer Applications (MCA)"
    assert education[0]["institution"] == (
        "V.V.P. Institute of Engineering & Technology, Solapur"
    )
    assert education[0]["graduation_year"] == "2025 \u2013 2027"

    assert education[1]["degree"] == (
        "Bachelor of Science (B.Sc.) in Statistics"
    )
    assert education[1]["institution"] == (
        "Sangmeshwar College, Solapur ? Autonomous"
    )
    assert education[1]["graduation_year"] == "2022 \u2013 2025"

    # Both project records must retain their real titles.
    projects = fields["projects"]
    assert len(projects) == 2
    assert projects[0]["title"] == (
        "Machine Learning-Based Text Sentiment Analysis Research"
    )
    assert projects[0]["technologies"] == [
        "Python",
        "Pandas",
        "scikit-learn",
        "TF-IDF",
    ]
    assert projects[0]["description"].startswith(
        "Investigated machine learning approaches"
    )
    assert projects[1]["title"] == (
        "Statistical Analysis of Social Media Usage Patterns"
    )

    # The second project's technology stack must not become its title
    # or leak into the project description.
    assert projects[1]["technologies"] == [
        "R",
        "Statistics",
        "Google Forms",
    ]
    assert "Conducted a statistical analysis project" in projects[1]["description"]
    assert "primary survey data collected through Google Forms" in projects[1]["description"]

    # Relevant Coursework must be a separate field, not an achievement.
    achievements = fields["academic_achievements"]
    assert len(achievements) == 1
    assert "TechNova 2026" in achievements[0]
    assert all(
        "Relevant Coursework" not in str(item)
        and "Machine Learning, Artificial Intelligence" not in str(item)
        for item in achievements
    )

    coursework = fields["relevant_coursework"]
    assert "Machine Learning" in coursework
    assert "Artificial Intelligence" in coursework
    assert "Python Programming" in coursework
    assert "Research Methodology" in coursework
    assert "Agile Methodologies" in coursework
    assert len(coursework) == len({
        item.casefold() for item in coursework
    })
    assert not any(
        item.casefold() in {"matplotlib", "seaborn", "plotly", "gramming"}
        for item in coursework
    )
    assert not any(
        "linkedin.com" in item.casefold()
        or "github.com" in item.casefold()
        or "data visualization" in item.casefold()
        for item in coursework
    )
    assert sum(
        item.casefold() == "research methodology"
        for item in coursework
    ) == 1



def test_plotly_is_preserved_as_a_visualization_skill():
    from app.services.field_extractors import _parse_resume_technical_skills

    skills = _parse_resume_technical_skills([
        "Data Visualization: Matplotlib, Seaborn",
        "Plotly, Matplotlib, Seaborn",
    ])

    assert "Plotly" in skills.get("Tools & Visualization", [])
