from app.services import field_extractors as fx


def test_combined_certifications_and_achievements_preserves_record_schema():
    text = """AARAV KULKARNI
Certifications and Achievements
SQL for Data Analysis - Sample Learning Academy
2025
Python for Data Analytics - Sample Learning Academy
2024
Department merit recognition - Statistics Programme
2023
"""

    fields = fx.extract_resume_fields(text)

    assert fields["certifications"] == [
        {
            "title": "SQL for Data Analysis",
            "issuer": "Sample Learning Academy",
            "year": "2025",
        },
        {
            "title": "Python for Data Analytics",
            "issuer": "Sample Learning Academy",
            "year": "2024",
        },
    ]
    assert fields["academic_achievements"] == [
        {
            "title": "Department merit recognition",
            "issuer": "Statistics Programme",
            "year": "2023",
        }
    ]


def test_combined_achievement_record_keeps_metadata_when_footer_is_removed():
    text = """AARAV KULKARNI
Certifications and Achievements
Department merit recognition. References available upon request
"""

    fields = fx.extract_resume_fields(text)

    assert fields["academic_achievements"] == [
        {"title": "Department merit recognition"}
    ]
    assert fields["references"] == ["Available upon request"]


def test_existing_structured_resume_regression_keeps_achievement_record_shape():
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

    fields = fx.extract_resume_fields(text)
    achievements = fields["academic_achievements"]
    assert len(achievements) == 1
    assert achievements[0] == {
        "title": "Department merit recognition",
        "issuer": "Statistics Programme",
        "year": "2023",
    }
