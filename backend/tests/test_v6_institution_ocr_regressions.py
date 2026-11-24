from app.services import field_extractors as fx


def test_education_institution_intilute_ocr_typo_is_corrected():
    text = """AARAV MEHTA
EDUCATION
B.Sc. Statistics
Western Intilute of Data Studies
2020 - 2023
"""
    fields = fx.extract_resume_fields(text)
    records = fields["educational_qualifications"]
    assert records
    assert records[0]["institution"] == "Western Institute of Data Studies"


def test_labelled_institution_intilute_typo_is_corrected():
    records = fx._build_resume_education_records(
        ["Degree: B.Sc. Statistics", "Institution: Western Intilute of Data Studies"]
    )
    assert records[0]["institution"] == "Western Institute of Data Studies"


def test_institution_typo_correction_preserves_original_case_style():
    assert fx._correct_institution_ocr_spelling("western intilute") == "western institute"
    assert fx._correct_institution_ocr_spelling("Western Intilute") == "Western Institute"
    assert fx._correct_institution_ocr_spelling("WESTERN INTILUTE") == "WESTERN INSTITUTE"


def test_institution_ocr_correction_does_not_rewrite_summary_or_other_fields():
    text = """AARAV MEHTA
PROFESSIONAL SUMMARY
I noticed the word intilute in a scanned sample.
EDUCATION
B.Sc. Statistics
Western Intilute of Data Studies
2020 - 2023
"""
    fields = fx.extract_resume_fields(text)
    assert "intilute" in fields["professional_summary"].casefold()
    assert fields["educational_qualifications"][0]["institution"] == "Western Institute of Data Studies"


def test_explicit_institution_value_is_corrected_in_full_resume_extraction():
    text = """AARAV MEHTA
INSTITUTION: Western Intilute of Data Studies
EDUCATION
B.Sc. Statistics
2020 - 2023
"""
    fields = fx.extract_resume_fields(text)
    assert fields["educational_qualifications"][0]["institution"] == "Western Institute of Data Studies"


def test_combined_degree_specialization_and_institution_are_split_on_one_ocr_line():
    text = """AARAV MEHTA
EDUCATION
B.Sc. Statistics Western Instilute of Data Studles
2020 - 2023
"""
    fields = fx.extract_resume_fields(text)
    record = fields["educational_qualifications"][0]
    assert record["degree"] == "B.Sc. Statistics"
    assert record["institution"] == "Western Institute of Data Studies"
    assert record["graduation_year"] == "2020 - 2023"
