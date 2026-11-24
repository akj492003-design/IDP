from app.services import field_extractors as fx


def test_sidebar_heading_before_candidate_name_is_not_selected():
    text = """DATA ANALYSIS
AARAV MEHTA
DATA ANALYST | PYTHON DEVELOPER
PROFESSIONAL SUMMARY
Statistics graduate with data analytics experience."""
    fields = fx.extract_resume_fields(text)
    assert fields["candidate_name"] == "AARAV MEHTA"


def test_merged_job_title_and_employer_are_separated():
    text = """Aarav Mehta
WORK EXPERIENCE
DATA ANALYST INTERN Northstar Analytics Labs
January 2024 - June 2024
Cleaned and validated sales records."""
    fields = fx.extract_resume_fields(text)
    assert fields["work_experience"][0]["job_title"] == "DATA ANALYST INTERN"
    assert fields["work_experience"][0]["company"] == "Northstar Analytics Labs"


def test_ocr_joined_project_stack_and_next_title_are_split():
    text = """Aarav Mehta
PROJECTS
RETAIL SALES ANALYSIS
Python | SQL | Power BI
Analyzed transaction data to identify seasonal sales patterns, high-performing categories, and regional differences.
Python | NLP | scikit-learn CUSTOMER REVIEW SENTIMENT ANALYSIS Prepared customer review text, classified sentiment, and summarized frequently occurring positive and negative themes.
ACHIEVEMENTS
Presented a project review explaining analysis methods and recommendations."""
    fields = fx.extract_resume_fields(text)
    assert len(fields["projects"]) == 2
    assert fields["projects"][0]["title"] == "RETAIL SALES ANALYSIS"
    assert fields["projects"][0]["technologies"] == ["Python", "SQL", "Power BI"]
    assert "CUSTOMER REVIEW SENTIMENT ANALYSIS" not in fields["projects"][0]["description"]
    assert fields["projects"][1]["title"] == "CUSTOMER REVIEW SENTIMENT ANALYSIS"
    assert fields["projects"][1]["technologies"] == ["Python", "NLP", "scikit-learn"]
    assert fields["projects"][1]["description"].startswith("Prepared customer review text")


def test_references_footer_joined_to_achievement_is_separated():
    text = """Aarav Mehta
ACADEMIC ACHIEVEMENTS
Built reusable SQL queries and data quality checks for recurring reporting tasks.
Presented a project review explaining analysis methods, limitations, and recommendations. References available upon request"""
    fields = fx.extract_resume_fields(text)
    assert len(fields["academic_achievements"]) == 2
    assert not any("references available" in item.casefold() for item in fields["academic_achievements"])
    assert fields["references"] == ["Available upon request"]


def test_compact_ocr_candidate_name_is_spaced_correctly():
    fields = fx.extract_resume_fields("""AARAVMEHTA
DATA ANALYST (PYTHON DEVELOPER
EDUCATION
B.Sc. Statistics
""")
    assert fields["candidate_name"] == "AARAV MEHTA"


def test_title_only_second_project_after_embedded_stack_is_separated():
    text = """AARAV MEHTA
PROJECTS
RETAIL SALES ANALYSIS
Python | SQL | Power BI
Analyzed transaction data to identify seasonal sales patterns and regional differences.
Python | NLP | scIkit-leam CUSTOMER REVIEW SENTIMENT ANALYS1S
Prepared customer review text, classified sentiment, and summarized recurring themes.
ACHIEVEMENTS
Presented a project review explaining analysis methods and recommendations.
"""
    fields = fx.extract_resume_fields(text)
    projects = fields["projects"]
    assert len(projects) == 2
    assert projects[0]["title"] == "RETAIL SALES ANALYSIS"
    assert projects[0]["technologies"] == ["Python", "SQL", "Power BI"]
    assert projects[1]["title"] == "CUSTOMER REVIEW SENTIMENT ANALYSIS"
    assert projects[1]["technologies"] == ["Python", "NLP", "scikit-learn"]
    assert projects[1]["description"].startswith("Prepared customer review text")


def test_ocr_spelling_of_references_footer_is_not_an_achievement():
    text = """AARAV MEHTA
ACADEMIC ACHIEVEMENTS
Built reusable SQL queries for recurring reports.
References avallable upon request
"""
    fields = fx.extract_resume_fields(text)
    assert fields["academic_achievements"] == ["Built reusable SQL queries for recurring reports."]
    assert fields["references"] == ["Available upon request"]
