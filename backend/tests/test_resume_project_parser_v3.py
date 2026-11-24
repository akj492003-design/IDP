"""Regression tests for resume project extraction."""
from app.services.field_extractors import _parse_resume_projects


def test_trailing_project_stack_moves_to_following_project():
    projects = _parse_resume_projects([
        "RETAIL SALES ANALYSIS",
        "Python | SQL | Power BI",
        (
            "Analyzed transaction data to identify seasonal sales patterns, "
            "high-performing categories, and regional differences. Python | NLP |"
        ),
        "CUSTOMER REVIEW SENTIMENT ANALYSIS",
        "scikit-learn",
        (
            "Prepared customer review text, classified sentiment, and "
            "summarized frequently occurring positive and negative themes."
        ),
    ])

    assert len(projects) == 2
    assert projects[0]["title"] == "RETAIL SALES ANALYSIS"
    assert projects[0]["technologies"] == ["Python", "SQL", "Power BI"]
    assert projects[0]["description"].endswith("regional differences.")
    assert projects[1]["title"] == "CUSTOMER REVIEW SENTIMENT ANALYSIS"
    assert projects[1]["technologies"] == ["Python", "NLP", "scikit-learn"]
    assert projects[1]["description"].startswith("Prepared customer review text")


def test_clean_project_layout_remains_unchanged():
    projects = _parse_resume_projects([
        "RETAIL SALES ANALYSIS",
        "Python | SQL | Power BI",
        "Analyzed transaction data to identify seasonal sales patterns.",
        "CUSTOMER REVIEW SENTIMENT ANALYSIS",
        "Python | NLP | scikit-learn",
        "Prepared customer review text and classified sentiment.",
    ])

    assert projects[0]["technologies"] == ["Python", "SQL", "Power BI"]
    assert projects[1]["technologies"] == ["Python", "NLP", "scikit-learn"]
    assert "Python | NLP" not in projects[0]["description"]


def test_single_technology_line_is_not_added_to_description():
    projects = _parse_resume_projects([
        "CUSTOMER REVIEW SENTIMENT ANALYSIS",
        "scikit-learn",
        "Prepared customer review text and classified sentiment.",
    ])

    assert projects[0]["technologies"] == ["scikit-learn"]
    assert projects[0]["description"] == (
        "Prepared customer review text and classified sentiment."
    )


def test_non_pipe_prose_is_not_misclassified_as_technology_stack():
    projects = _parse_resume_projects([
        "CUSTOMER REVIEW SENTIMENT ANALYSIS",
        "Prepared customer review text using Python and NLP techniques.",
    ])

    assert projects[0]["description"] == (
        "Prepared customer review text using Python and NLP techniques."
    )
    assert "technologies" not in projects[0]



def test_leading_comma_technology_stack_is_separated_from_project_description():
    projects = _parse_resume_projects([
        "Machine Learning-Based Text Sentiment Analysis Research",
        "Python, Pandas, Scikit-learn, TF-IDF ? Investigated machine learning approaches for text sentiment classification.",
        "Statistical Analysis of Social Media Usage Patterns",
        "R, Statistics, Google Forms ? Conducted a statistical analysis project using primary survey data collected through Google Forms.",
    ])

    assert len(projects) == 2

    assert projects[0]["title"] == (
        "Machine Learning-Based Text Sentiment Analysis Research"
    )
    assert projects[0]["technologies"] == [
        "Python", "Pandas", "scikit-learn", "TF-IDF"
    ]
    assert projects[0]["description"].startswith("Investigated machine learning")

    assert projects[1]["title"] == (
        "Statistical Analysis of Social Media Usage Patterns"
    )
    assert projects[1]["technologies"] == [
        "R", "Statistics", "Google Forms"
    ]
    assert projects[1]["description"].startswith(
        "Conducted a statistical analysis project"
    )
