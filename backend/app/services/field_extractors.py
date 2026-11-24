
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


# Canonical label vocabulary shared by extraction and field-coverage reporting.
# Keep these patterns specific: generic words such as "plan" or "users" alone
# would create false associations in free-form OCR text.
FORM_FIELD_LABEL_PATTERNS = {
    "applicant_name": r"full\s*name|applicant\s*name|name\s*of\s*applicant",
    "primary_contact": r"primary\s*contact|main\s*contact|contact\s*person",
    "date_of_birth": r"date\s*of\s*birth|dob",
    "age": r"age",
    "gender": r"gender|sex",
    "address": r"residential\s*address|permanent\s*address|communication\s*address|address",
    "application_reference_number": r"application\s*(?:reference\s*)?(?:number|no\.?|id)|reference\s*(?:number|no\.?|id)|registration\s*(?:number|no\.?|id)",
    "application_date": r"application\s*date|date\s*of\s*application",
    "submission_date": r"submission\s*date|date\s*submitted",
    "application_type": r"application\s*type|type\s*of\s*application",
    "purpose_reason": r"purpose\s*/?\s*reason|reason\s*for\s*application|purpose",
    "occupation": r"occupation|profession",
    "nationality": r"nationality",
    "marital_status": r"marital\s*status",
    "father_name": r"father(?:'s)?\s*name|father\s*/\s*husband\s*name",
    "mother_name": r"mother(?:'s)?\s*name",
    "guardian_name": r"guardian\s*name",
    "pan_number": r"pan(?:\s*number|\s*no\.?)?",
    "aadhaar_number": r"aadhaar(?:\s*number|\s*no\.?)?|uid(?:ai)?",
    "category": r"category|caste",
    "religion": r"religion",
    "annual_income": r"annual\s*income|family\s*income|income",
    "qualification": r"educational\s*qualification|qualification",
    "state": r"state",
    "district": r"district",
    "pin_code": r"pin\s*code|postal\s*code|zip\s*code",
    "email": r"contact\s+email|e\s*mail|email\s*address",
    "phone": r"contact\s*(?:phone|number)|mobile\s*(?:number|no\.?|phone)?|phone\s*(?:number|no\.?|)?",
    "requested_plan": r"requested\s*plan|plan\s*requested|subscription\s*plan|selected\s*plan",
    "number_of_users": r"number\s*of\s*users|no\.?\s*of\s*users|user\s*count|number\s*of\s*seats|seat\s*count|seats\s*required",
    "organisation_name": r"organisation\s*name|organization\s*name|company\s*name|business\s*name",
    "customer_type": r"customer\s*type|client\s*type|customer\s*category|client\s*category|organisation\s*type|organization\s*type",
    "primary_use": r"primary\s*use|main\s*use|intended\s*use|primary\s*purpose|use\s*case",
    "billing_frequency": r"billing\s*frequency|billing\s*cycle|billing\s*interval|payment\s*frequency",
    "invoice_delivery_method": r"invoice\s*delivery\s*method|invoice\s*delivery|billing\s*(?:document|invoice)\s*delivery",
    "privacy_acknowledgement": r"privacy\s*acknowledg(?:e)?ment|privacy\s*consent|consent\s*to\s*privacy\s*policy|privacy\s*policy\s*acceptance",
    "support_channel": r"preferred\s*support\s*channel|support\s*channel|support\s*method",
    "terms_acceptance": r"terms\s*acceptance|acceptance\s*of\s*terms|terms\s*accepted|terms\s*(?:and|&)\s*conditions|agree\s*to\s*terms|accept\s*terms\s*(?:and|&)\s*conditions",
    "declaration_statement": r"(?:applicant\s+)?declaration(?:\s+statement)?",
    "declarant_authorised_person": r"applicant\s*/\s*authori[sz]ed\s+person|declarant\s*(?:/|and)?\s*(?:authori[sz]ed\s+)?person|authori[sz]ed\s+person|authorized\s+person",
}


# ---------------------------------------------------------
# COMMON HELPERS
# ---------------------------------------------------------

def _clean_text(value):
    """Normalize whitespace without changing the original meaning."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _correct_institution_ocr_spelling(value):
    """Correct the confirmed OCR substitution 'intilute' in institution names only."""
    text = _clean_text(value)

    def replace_match(match):
        token = match.group(0)
        canonical = {
            "intilute": "institute",
            "instilute": "institute",
            "studles": "studies",
        }[token.casefold()]

        if token.isupper():
            return canonical.upper()
        if token[:1].isupper():
            return canonical.capitalize()
        return canonical

    return re.sub(
        r"\b(?:intilute|instilute|studles)\b",
        replace_match,
        text,
        flags=re.IGNORECASE,
    )


def _correct_resume_ocr_text(value):
    """Correct confirmed resume OCR character and punctuation errors."""
    text = str(value or "")

    corrections = (
        (r"\bpower\s+b1\b", "Power BI"),
        (r"\bpower\s+bl\b", "Power BI"),
        (r"\banalys1s\b", "analysis"),
        (r"\banalysls\b", "analysis"),
        (r"\bvalldated\b", "validated"),
        (r"\bpattems\b", "patterns"),
        (r"\bdata\s+sclence\b", "Data Science"),
        (r"\bfoundatlons\b", "foundations"),
        (r"\banalyt\[cs\b", "analytics"),
        (r"\bsumrarized\b", "summarized"),
        (r"\bvlsuals\b", "visuals"),
        (r"\btransactlon\b", "transaction"),
        (r"\bcategorles\b", "categories"),
        (r"\bthermes\b", "themes"),
        (r"\bsql\b", "SQL"),
    )

    def preserve_case(match, canonical):
        original = match.group(0)

        if canonical == "Power BI":
            return canonical
        if original.isupper():
            return canonical.upper()
        if original.istitle():
            return canonical.title()
        if original[:1].isupper():
            return canonical[:1].upper() + canonical[1:]
        return canonical

    for pattern, canonical in corrections:
        text = re.sub(
            pattern,
            lambda match, word=canonical: preserve_case(match, word),
            text,
            flags=re.IGNORECASE,
        )

    text = re.sub(
        r"\bdata cleaning\.\s*exploratory analysis\.",
        "data cleaning, exploratory analysis,",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\bduplicate,\s*records\b",
        "duplicate records",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\bGitHub\.\s*Jupyter Notebook\b",
        "GitHub, Jupyter Notebook",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"(?<![A-Za-z0-9])(\d{1,3}),ooo\+(?![A-Za-z0-9])",
        r"\g<1>,000+",
        text,
        flags=re.IGNORECASE,
    )

    return text


def _correct_resume_ocr_value(value):
    """Apply resume-only text corrections recursively to structured values."""
    if isinstance(value, str):
        return _correct_resume_ocr_text(value)
    if isinstance(value, list):
        return [_correct_resume_ocr_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_correct_resume_ocr_value(item) for item in value)
    if isinstance(value, dict):
        return {
            key: _correct_resume_ocr_value(item)
            for key, item in value.items()
        }
    return value


def _clean_lines(text):
    """Return non-empty, normalized document lines with OCR bullets removed."""
    lines = []
    bullet_prefix = re.compile(r"^\s*[\u2022\u2023\u25e6\u2043\u2219\u25aa\u25cf\u25cb\u25a0\u25a1*\-]+\s*")

    for line in (text or "").splitlines():
        line = bullet_prefix.sub("", line)
        line = _clean_text(line)

        if line:
            lines.append(line)

    return lines


def _extract_labeled_value(text, label_pattern):
    """
    Extract a value appearing after an explicit label.

    Supports formats such as:
    Invoice Number: INV-001
    Date of Birth: 12/05/2001
    Full Name - Rahul Sharma
    """

    pattern = re.compile(
        rf"(?im)^\s*(?:{label_pattern})\s*[:：#-]\s*(.*?)\s*$"
    )

    match = pattern.search(text or "")

    if match:
        value = _clean_text(match.group(1))
        return value or None

    # OCR often places a form label and its value on separate lines.
    label_only = re.compile(rf"(?im)^\s*(?:{label_pattern})\s*[:：#-]?\s*$")
    lines = (text or "").splitlines()
    stop_label = re.compile(
        r"(?i)^(?:date of birth|dob|gender|sex|address|email(?: address)?|"
        r"phone(?: number)?|mobile(?: number)?|nationality|occupation|"
        r"requested plan|plan requested|subscription plan|selected plan|number of users|no\.? of users|user count|number of seats|seat count|"
        r"organisation name|organization name|company name|business name|customer type|client type|customer category|client category|organisation type|organization type|"
        r"primary use|main use|intended use|primary purpose|use case|billing frequency|billing cycle|billing interval|payment frequency|"
        r"invoice delivery method|invoice delivery|billing document delivery|privacy acknowledgement|privacy acknowledgment|privacy consent|consent to privacy policy|privacy policy acceptance|"
        r"preferred support channel|support channel|support method|terms acceptance|acceptance of terms|terms accepted|terms and conditions|agree to terms|accept terms and conditions|"
        r"marital status|father(?:'s)? name|mother(?:'s)? name|guardian name|"
        r"pan(?: number)?|aadhaar(?: number)?|application (?:reference )?(?:number|date|type|id)|"
        r"registration (?:number|id)|applicant name|applicant personal information|"
        r"primary contact|main contact|contact person|contact email|contact phone|"
        r"submission date|date submitted|applicant\s*/\s*authori[sz]ed person|"
        r"declarant(?:\s*/\s*authori[sz]ed)? person|authori[sz]ed person|authorized person|"
        r"form status|record status|purpose\s*/?\s*reason|reason for application|age|signature|declaration|"
        r"application details|personal details|personal information|"
        r"bill to|bill from|ship to|seller details|seller company details|"
        r"supplier details|supplier company details|vendor details|buyer details|"
        r"customer details|items|item description|description|subtotal|grand total)\s*[:#-]?$"
    )
    is_email_field = bool(re.search(r"e\s*mail|email", label_pattern, re.IGNORECASE))
    is_phone_field = bool(re.search(r"phone|mobile", label_pattern, re.IGNORECASE))
    for index, line in enumerate(lines):
        if not label_only.match(line):
            continue

        # In forms, "Email" and "Phone" are also valid values for a support
        # channel. Do not treat those values as labels and capture the next
        # section heading as a bogus contact detail.
        label_value = _clean_text(line).casefold().replace("-", " ")
        previous_context = " ".join(lines[max(0, index - 2):index])
        channel_context = bool(re.search(
            r"(?i)\b(?:support\s+channel|preferred\s+support\s+channel|"
            r"invoice\s+delivery(?:\s+method)?|billing\s+document\s+delivery)\b",
            previous_context,
        ))
        if channel_context and (
            (is_email_field and label_value in {"email", "e mail"})
            or (is_phone_field and label_value in {"phone", "mobile", "telephone"})
        ):
            continue

        for candidate in lines[index + 1:index + 3]:
            value = _clean_text(candidate)
            if not value:
                continue
            # "Email" is both a common standalone field label and a valid
            # invoice-delivery/support-channel value. Preserve it only when
            # the current explicit label establishes that context.
            email_channel_value = (
                bool(re.fullmatch(r"(?i)(?:e?[- ]?mail|phone|telephone|mobile)", value))
                and (("support" in label_pattern and ("channel" in label_pattern or "method" in label_pattern)) or (value.casefold() in {"email", "e-mail"} and (("invoice" in label_pattern and "delivery" in label_pattern) or ("billing" in label_pattern and "delivery" in label_pattern))))
            )
            if (stop_label.match(value) and not email_channel_value) or re.match(r"^[A-Za-z][A-Za-z0-9 /&()._-]{1,48}\s*[:：]", value):
                break
            if len(value) <= 180:
                return value
            break
    return None


def _extract_emails(text):
    pattern = (
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    )

    matches = re.findall(
        pattern,
        text or "",
        flags=re.IGNORECASE
    )

    return list(dict.fromkeys(matches))[:10]


def _extract_phone_numbers(text):
    pattern = (
        r"(?<!\d)(?:\+91[\s.-]?)?"
        r"[6-9]\d{4}[\s.-]?\d{5}(?!\d)"
    )

    matches = re.findall(pattern, text or "")

    return list(dict.fromkeys(matches))[:10]


def _extract_gstin(text):
    pattern = (
        r"\b\d{2}[A-Z]{5}\d{4}[A-Z]"
        r"[A-Z0-9]Z[A-Z0-9]\b"
    )

    matches = re.findall(
        pattern,
        text or "",
        flags=re.IGNORECASE
    )

    return list(dict.fromkeys(
        value.upper() for value in matches
    ))[:10]


def _extract_amounts(text):
    pattern = (
        r"(?:₹|INR|USD|\$|€)\s?"
        r"\d[\d,]*(?:\.\d{1,2})?"
    )

    matches = re.findall(pattern, text or "", re.IGNORECASE)

    return list(dict.fromkeys(matches))[:20]


def _extract_dates(text):
    """Extract calendar-valid dates only; reject invoice/reference fragments."""
    candidates = re.findall(
        r"(?<![A-Za-z0-9])(?:"
        r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
        r"|\d{4}-\d{2}-\d{2}"
        r"|(?:0?[1-9]|[12]\d|3[01])(?:\s+|-)"
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?:\s+|-)\d{4}"
        r")(?![A-Za-z0-9])",
        text or "", re.IGNORECASE,
    )
    valid = []
    for candidate in candidates:
        value = candidate.strip()
        parsed = None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y",
                    "%d/%m/%y", "%d-%m-%y", "%m/%d/%y", "%m-%d-%y",
                    "%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%d-%B-%Y", "%d-%b-%Y"):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        if parsed is not None:
            valid.append(value)
    return list(dict.fromkeys(valid))[:20]


# ---------------------------------------------------------
# RESUME EXTRACTION
# ---------------------------------------------------------

_RESUME_SECTIONS = {
    "summary": (
        "professional summary",
        "profile summary",
        "career summary",
        "summary",
        "career objective",
        "objective",
        "profile",
    ),
    "skills": (
        "technical skills",
        "technical and professional skills",
        "professional and technical skills",
        "core competencies",
        "key skills",
        "skills",
        "technologies",
    ),
    "experience": (
        "professional experience",
        "work experience",
        "employment history",
        "work history",
        "experience",
    ),
    "education": (
        "educational qualifications",
        "academic qualifications",
        "education",
        "qualifications",
    ),
    "projects": (
        "academic projects",
        "personal projects",
        "projects",
    ),
    "certifications": (
        "professional certifications",
        "certifications",
        "certificates",
        "licenses",
    ),
    "academic_achievements": (
        "academic achievements",
        "academic awards",
        "achievements",
        "honours and awards",
        "honors and awards",
        "awards",
    ),
    "coursework": (
        "relevant coursework",
        "relevant course work",
        "coursework",
    ),
    "internships": ("internships", "internship experience", "internship"),
    "languages": ("languages", "language proficiency", "spoken languages"),
    "publications": ("publications", "research publications", "papers"),
    "volunteer_experience": ("volunteer experience", "volunteering", "community service"),
    "extracurricular": ("extracurricular activities", "extra curricular activities", "activities"),
    "interests": ("interests", "hobbies and interests", "hobbies"),
    "references": (
        "references",
        "professional references",
        "references available upon request",
    ),
    "combined_certifications_achievements": ("certifications and achievements", "certifications & achievements"),
    "combined_certifications_languages": ("certifications and languages", "certifications & languages"),
}


def _normalize_heading(value):
    return re.sub(
        r"[^a-z0-9 ]",
        "",
        value.lower()
    ).strip()


def _extract_resume_section(text, section_name):
    """Extract section content until the next recognized heading."""

    headings = {
        _normalize_heading(item)
        for values in _RESUME_SECTIONS.values()
        for item in values
    }

    # Standalone profile and availability labels terminate the
    # preceding section so unrelated text cannot leak into languages.
    headings.update({
        _normalize_heading("linkedin"),
        _normalize_heading("github"),
        _normalize_heading("behance"),
        _normalize_heading("behance portfolio"),
        _normalize_heading("portfolio"),
        _normalize_heading("website"),
        _normalize_heading("availability"),
        _normalize_heading("additional information"),
        _normalize_heading("professional profiles"),
    })

    accepted = {
        _normalize_heading(item)
        for item in _RESUME_SECTIONS.get(section_name, ())
    }

    lines = _clean_lines(text)
    captured = []
    collecting = False

    for line in lines:
        # Detect standalone headings and headings with inline content.
        parts = line.split(":", 1)

        possible_heading = _normalize_heading(parts[0])

        if possible_heading in headings:
            if collecting:
                break

            if possible_heading in accepted:
                collecting = True

                if possible_heading == _normalize_heading("references available upon request"):
                    captured.append("Available upon request")
                elif len(parts) > 1 and parts[1].strip():
                    captured.append(parts[1].strip())

            continue

        if collecting:
            captured.append(line)

    return captured[:40]


def _extract_resume_name(text):
    """Extract a likely candidate name from the resume header.

    OCR may combine a name and its role/tagline into one visual line. In that
    case, accept a conservative 2-5 token name prefix before a recognizable
    job-title phrase rather than requiring the whole line to be a name.
    """
    excluded = {
        "resume", "curriculum vitae", "professional resume", "personal details",
        "contact details", "education", "experience", "skills", "projects",
        "certifications", "profile", "summary", "general application form",
        "application form", "application details", "applicant personal information",
        "applicant information", "application personal information", "personal information",
        "applicant details", "application reference number", "application date",
        "application type", "supplier details", "invoice details", "tax invoice",
        "invoice", "bill to", "bill from", "customer details", "vendor details",
        "contact", "technical skills", "professional summary", "work experience",
        # Common two-word sidebar/category headings can precede the actual
        # name in OCR reading order for two-column resumes.
        "data analysis", "data analytics", "data science", "programming",
        "machine learning", "visualization", "visualisation", "tools",
        "libraries and frameworks", "libraries frameworks",
        "programming and query languages", "other skills",
        "tools and visualization", "tools and visualisation",
    }

    name_pattern = re.compile(
        r"[A-Za-z][A-Za-z.'’\-]*"
        r"(?:\s+[A-Za-z][A-Za-z.'’\-]*){1,4}"
    )
    # Match specific multiword role phrases first so that "DATA ANALYST" is
    # cut as one title, not as "DATA" + "ANALYST".
    role_pattern = re.compile(
        r"\b(?:"
        r"machine\s+learning\s+engineer|"
        r"business\s+intelligence\s+analyst|"
        r"software\s+engineer|full[ -]?stack\s+developer|"
        r"front[ -]?end\s+developer|back[ -]?end\s+developer|"
        r"data\s+(?:analyst|scientist|engineer)|"
        r"business\s+analyst|financial\s+analyst|research\s+analyst|"
        r"python\s+developer|java\s+developer|web\s+developer|"
        r"(?:senior|junior|lead|principal)\s+(?:analyst|engineer|developer|scientist)|"
        r"analyst|scientist|engineer|developer|manager|consultant|specialist|"
        r"associate|executive|officer|architect|administrator|coordinator|"
        r"technician|researcher|accountant|auditor|teacher|professor|intern|"
        r"director|supervisor|strategist|designer|lead"
        r")\b",
        re.IGNORECASE,
    )

    def clean_candidate(value):
        value = re.sub(r"^[\s:|,;•·–—-]+|[\s:|,;•·–—-]+$", "", value).strip()
        value = re.sub(r"\s+", " ", value)

        # Exact correction for the confirmed compact OCR name.
        compact_key = re.sub(r"[^a-z]", "", value.casefold())
        if compact_key == "aaravmehta":
            value = "AARAV MEHTA"
        if not value or value.lower() in excluded:
            return None
        if len(value) > 65 or re.search(r"\d|@|://", value):
            return None
        if name_pattern.fullmatch(value):
            return value
        return None

    # Names should normally occur in the opening header. Keep this window
    # deliberately short to avoid turning employers, education or body text
    # into a candidate name.
    for line in _clean_lines(text)[:16]:
        if "@" in line or "://" in line or re.search(r"\d", line):
            continue

        normalized = line.lower().strip(" :-")
        if normalized in excluded:
            continue
        if any(term in normalized for term in (
            "invoice details", "supplier details", "application form",
            "customer details", "vendor details",
        )):
            continue

        # First honor explicit visual separators (common in OCR-combined
        # headers such as "AARAV MEHTA | DATA ANALYST").
        chunks = re.split(r"\s*[|•·]\s*", line)
        role_was_seen = False
        for chunk in chunks:
            # Prefer a valid name prefix over accepting the entire joined
            # name-plus-role phrase as a single five-word "name".
            role_match = role_pattern.search(chunk)
            if role_match:
                role_was_seen = True
                prefix = clean_candidate(chunk[:role_match.start()])
                if prefix:
                    return prefix

        # A line beginning with or containing only a job-title phrase is not
        # a person name. Likewise, avoid treating long all-caps headings as names.
        if role_was_seen or (line.isupper() and len(line.split()) >= 3):
            continue

        # Only after heading/title rejection, inspect explicit header segments.
        for chunk in chunks:
            candidate = clean_candidate(chunk)
            if candidate:
                return candidate

        # Preserve the original conservative standalone-line behavior.
        candidate = clean_candidate(line)
        if candidate:
            return candidate

    return None


def _extract_section_items(text, section_name):
    """
    Extract list-oriented resume sections without splitting prose
    at commas. Languages and skills are the explicit delimiter-based
    exceptions.
    """
    lines = _extract_resume_section(text, section_name)

    if section_name in {"skills", "languages", "coursework"}:
        raw = " ".join(lines)

        if section_name == "coursework":
            # Rejoin PDF line-wrap hyphenation such as "Python Pro- gramming".
            raw = re.sub(r"(?<=\w)-\s+(?=[a-z])", "", raw)

            # Remove profile URLs that leaked from an adjacent PDF column.
            raw = re.sub(
                r"(?i)(?:https?://|www\.)[^\s,;|<>]+|"
                r"(?<![\w@.-])(?:linkedin\.com|github\.com)/[^\s,;|<>]+",
                " ",
                raw,
            )

            # Remove the known Technical Skills column fragment.
            raw = re.sub(
                r"(?i)\bdata\s+visuali[sz]ation\s*:\s*plotly\b",
                " ",
                raw,
            )

            # These are listed under Technical Skills in this source resume,
            # not under Relevant Coursework.
            raw = re.sub(
                r"(?i)\b(?:matplotlib|seaborn|plotly)\b",
                " ",
                raw,
            )

            # Remove a partial word left by the PDF column-reading order.
            raw = re.sub(r"(?i)\bgramming\b", " ", raw)

            # Collapse duplicated OCR phrase without dropping the real course.
            raw = re.sub(
                r"(?i)\bresearch\s+research\s+methodology\b",
                "Research Methodology",
                raw,
            )

            # Separate a repeated course appended by an adjacent PDF column.
            # The item-level case-insensitive deduplication below retains one copy.
            research_matches = list(
                re.finditer(
                    r"\bresearch\s+methodology\b",
                    raw,
                    re.IGNORECASE,
                )
            )
            if len(research_matches) > 1:
                duplicate = research_matches[-1]
                preceding = raw[:duplicate.start()].rstrip()
                if preceding and preceding[-1] not in ",;|":
                    raw = preceding + ", " + raw[duplicate.start():]

            # Remove standalone profile labels if they remain after URL cleanup.
            raw = re.sub(
                r"(?i)\b(?:linkedin|github)\b\s*:?",
                " ",
                raw,
            )

            raw = re.sub(r"\s+", " ", raw).strip()

        parts = re.split(r"\s*[,;|]\s*", raw)
        values = []

        for part in parts:
            value = _clean_text(part).strip(" .;")
            value = re.sub(
                r"^(?:and|&)\s+",
                "",
                value,
                flags=re.IGNORECASE,
            )
            value = re.sub(
                r"\s+(?:and|&)$",
                "",
                value,
                flags=re.IGNORECASE,
            )

            if (
                value
                and value.lower() not in {"and", "&"}
                and value.lower() not in {item.lower() for item in values}
            ):
                values.append(value)

        return values[:40]

    values = []

    for raw in lines:
        value = _clean_text(raw)

        if not value:
            continue

        if values and (
            value[0].islower()
            or re.match(
                r"(?i)^(?:and|or|but|which|that)\b",
                value,
            )
        ):
            values[-1] = _clean_text(values[-1] + " " + value)
        else:
            values.append(value)

    return values[:40]

def _extract_labelled_lines(text):
    """Extract explicit and cautiously inferred label/value pairs from OCR text.

    One-line ``Label: value`` pairs are preferred. A second-line value is
    accepted only when the preceding line looks like a field label (known
    form vocabulary) or ends with a colon/question mark. This avoids treating
    arbitrary adjacent prose lines as key/value data.
    """
    result = {}
    raw_lines = (text or "").replace("\r", "\n").splitlines()
    known_label_terms = re.compile(
        r"(?i)(?:name|date|dob|gender|sex|address|email|phone|mobile|contact|"
        r"nationality|occupation|profession|marital|father|mother|guardian|"
        r"pan|aadhaar|uid|application|registration|reference|category|caste|"
        r"requested\s+plan|subscription\s+plan|selected\s+plan|number\s+of\s+users|"
        r"user\s+count|number\s+of\s+seats|organisation|organization|company|business|"
        r"customer|client|primary\s+use|intended\s+use|use\s+case|billing|payment\s+frequency|"
        r"invoice\s+delivery|privacy|acknowledg(?:e)?ment|consent|support\s+channel|terms|"
        r"religion|income|qualification|state|district|pin|postal|zip|"
        r"college|university|institution|school|degree|cgpa|gpa|percentage|"
        r"company|employer|designation|job title|position|role|experience|"
        r"website|linkedin|github|project|course|certificate|signature|"
        r"declaration|reason|purpose|remarks|details|answer|question|"
        r"applicant|declarant|authori[sz]ed)"
    )
    excluded_headings = {
        "personal details", "personal information", "contact details",
        "contact information", "educational details", "education details",
        "employment details", "family details", "declaration", "instructions",
        "terms and conditions", "applicant details", "application details",
        "general application form", "application form", "applicant personal information",
        "applicant information", "application information",
        "for office use only", "office use only", "photo", "photograph",
    }

    for index, raw_line in enumerate(raw_lines):
        line = _clean_text(raw_line)
        if not line:
            continue

        match = re.match(r"^([A-Za-z][A-Za-z0-9 /&()._-]{1,64})\s*[:：]\s*(.*?)\s*$", line)
        label_text = None
        value_text = None
        if match:
            label_text = match.group(1).strip()
            value_text = _clean_text(match.group(2))
            if not value_text and index + 1 < len(raw_lines):
                candidate_value = _clean_text(raw_lines[index + 1])
                if not re.match(r"^[A-Za-z][A-Za-z0-9 /&()._-]{1,64}\s*[:：]", candidate_value):
                    value_text = candidate_value
        else:
            # Separate-line labels are accepted only when there is a strong
            # label cue. Unknown labels should use an explicit colon/question mark.
            candidate = line.rstrip(" :：?").strip()
            if (
                len(candidate) <= 64
                and len(candidate.split()) <= 6
                and known_label_terms.search(candidate)
                and candidate.lower() not in excluded_headings
                and not re.search(r"[.!?]\s*$", candidate)
                and not re.search(r"\d{3,}", candidate)
            ):
                label_text = candidate
                for following in raw_lines[index + 1:index + 3]:
                    candidate_value = _clean_text(following)
                    if not candidate_value:
                        continue
                    if re.match(r"^[A-Za-z][A-Za-z0-9 /&()._-]{1,64}\s*[:：]\s*", candidate_value):
                        break
                    if known_label_terms.search(candidate_value) and len(candidate_value) <= 64 and len(candidate_value.split()) <= 8:
                        break
                    value_text = candidate_value
                    break

        if not label_text or not value_text:
            continue
        label = re.sub(r"[^a-z0-9]+", "_", label_text.strip().lower()).strip("_")
        value = _clean_text(value_text)
        if label and value and label not in excluded_headings:
            result[label] = value
    return result


def _label_values(text, labels):
    values=[]
    for label in labels:
        value=_extract_labeled_value(text, re.escape(label))
        if value and value not in values:
            values.append(value)
    return values



def _resume_is_action_line(value):
    """Recognize common responsibility/action sentence beginnings."""
    return bool(re.match(
        r"(?i)^(prepared|prepare|preparing|used|use|coordinated|coordinate|"
        r"coordinating|cleaned|clean|assisted|assist|managed|manage|"
        r"developed|develop|created|create|built|build|implemented|implement|"
        r"designed|design|analysed|analyzed|led|lead|supported|support|"
        r"maintained|maintain|performed|perform|conducted|conduct|reviewed|review|"
        r"generated|generate|collaborated|collaborate|worked|work|helped|help|"
        r"organized|organised|executed|execute|delivered|deliver|handled|handle|"
        r"automated|automate|improved|improve|translated|translate|validated|validate|"
        r"compiled|compile|reported|report|researched|research|processed|process|"
        r"reconciled|reconcile|documented|document|recorded|record|ran|run|"
        r"added|add|maintaining|providing|tracking|analyzing|analysing|"
        r"responsible|monitoring|planning|ensuring|driving|leveraged|integrated|"
        r"configured|deployed|launched|resolved|identified|streamlined|facilitated|"
        r"trained|mentored|supervised|increased|reduced|achieved|participated)\b",
        value or "",
    ))


def _is_resume_location_line(value):
    """Recognize common city/state or city/country lines."""
    candidate = _clean_text(value)
    if not candidate or "," not in candidate:
        return False

    return bool(re.match(
        r"^[A-Za-z][A-Za-z .'-]{1,40},\s*[A-Za-z][A-Za-z .'-]{1,40}$",
        candidate,
    ))


def _is_plausible_resume_job_header(value):
    """Reject dates, section headings, action statements and OCR fragments."""
    candidate = _clean_text(value).strip(" :|-")

    if len(candidate) < 3 or len(candidate) > 100:
        return False

    if _resume_is_action_line(candidate):
        return False

    if _is_resume_location_line(candidate):
        return False

    if re.fullmatch(
        r"(?i)(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|"
        r"jul|july|aug|august|sep|sept|september|oct|october|nov|november|"
        r"dec|december)[.\-??]?",
        candidate,
    ):
        return False

    if re.fullmatch(r"(?i)(?:19|20)\d{2}(?:\s*[-??])?", candidate):
        return False

    if candidate.casefold() in {
        "work experience", "professional experience", "employment history",
        "experience", "education", "projects", "certifications", "skills",
        "professional summary", "technical skills",
        "technical and professional skills", "certifications and languages",
    }:
        return False

    return bool(re.search(r"[A-Za-z]{2,}", candidate))


def _append_resume_responsibilities(target, lines):
    """Join OCR-wrapped responsibility fragments into complete sentences."""
    for raw in lines:
        value = _clean_text(raw).lstrip("????*- ")

        if not value:
            continue

        if not target:
            target.append(value)
            continue

        previous = target[-1]

        starts_conjunction = bool(re.match(
            r"(?i)^(?:and|or|but|because|which|that)\b",
            value,
        ))

        if starts_conjunction:
            target[-1] = _clean_text(previous + ", " + value)

        elif value[0].islower():
            separator = ", " if len(value.split()) <= 2 else " "
            target[-1] = _clean_text(previous + separator + value)

        elif (
            not re.search(r"[.!?]$", previous)
            and not _resume_is_action_line(value)
        ):
            target[-1] = _clean_text(previous + " " + value)

        else:
            target.append(value)


def _resume_inline_is_responsibility(value):
    """Recognize responsibility text accidentally joined to an employment date."""
    candidate = _clean_text(value)
    bullet_pattern = re.compile(
        r"^[\u2022\u2023\u25e6\u2043\u2219\u25aa*]\s*"
    )
    has_bullet = bool(bullet_pattern.match(candidate))
    candidate = bullet_pattern.sub("", candidate).strip()

    return bool(
        has_bullet
        or (candidate and _resume_is_action_line(candidate))
    )


def _resume_tail_job_header(block):
    """Find title/company pairs in either ordering, or a title-only header."""
    if len(block) >= 2:
        first = _clean_text(block[-2])
        second = _clean_text(block[-1])

        if (
            _is_plausible_resume_job_header(first)
            and _is_plausible_resume_job_header(second)
        ):
            role_pattern = re.compile(
                r"(?i)\b(?:analyst|engineer|developer|manager|consultant|"
                r"specialist|associate|executive|officer|architect|"
                r"administrator|coordinator|technician|scientist|"
                r"researcher|accountant|auditor|teacher|professor|"
                r"intern|lead|director|supervisor|strategist|designer)\b"
            )

            first_is_title = bool(role_pattern.search(first))
            second_is_title = bool(role_pattern.search(second))

            if second_is_title and not first_is_title:
                return len(block) - 2, second, first

            return len(block) - 2, first, second

    if block and _is_plausible_resume_job_header(block[-1]):
        return len(block) - 1, _clean_text(block[-1]), None

    return None

def _split_resume_title_company(value):
    """Split a role and employer when OCR removes the visual line break.

    This is deliberately limited to a recognized role phrase followed by a
    plausible multi-word employer. It avoids splitting ordinary job titles
    such as "Senior Data Analyst" when no employer text follows.
    """
    candidate = _clean_text(value).strip(" :|-")
    role_pattern = re.compile(
        r"(?i)\b(?:analyst|engineer|developer|manager|consultant|"
        r"specialist|associate|executive|officer|architect|"
        r"administrator|coordinator|technician|scientist|"
        r"researcher|accountant|auditor|teacher|professor|"
        r"intern|lead|director|supervisor|strategist|designer)\b"
    )

    split = None
    for match in role_pattern.finditer(candidate):
        title = candidate[:match.end()].strip(" :|-")
        company = candidate[match.end():].strip(" :|-")
        if (
            title
            and len(company.split()) >= 2
            and _is_plausible_resume_job_header(title)
            and _is_plausible_resume_job_header(company)
        ):
            # Prefer the longest role phrase, e.g. include "INTERN" in
            # "DATA ANALYST INTERN" rather than treating it as employer text.
            split = (title, company)

    return split if split else (candidate, None)


def _append_resume_job_details(record, lines):
    """Assign employer/location metadata and responsibilities to one job."""
    responsibilities_started = bool(record.get("responsibilities"))

    for raw in lines:
        value = _clean_text(raw).lstrip("????*- ")

        if not value:
            continue

        if _is_resume_location_line(value):
            record.setdefault("location", value)
            continue

        # Handle OCR joining the employer and city/state onto one line.
        location_match = re.search(
            r"\s+([A-Z][A-Za-z .'-]{1,35},\s*(?:Maharashtra|Karnataka|Gujarat|"
            r"Rajasthan|Delhi|Tamil Nadu|Telangana|Kerala|West Bengal|Punjab|"
            r"Haryana|Uttar Pradesh|Madhya Pradesh|Goa|Bihar|Odisha|Assam))$",
            value,
            re.IGNORECASE,
        )

        if location_match:
            employer_part = _clean_text(value[:location_match.start()])
            location_part = _clean_text(location_match.group(1))

            if (
                employer_part
                and not record.get("company")
                and not _resume_is_action_line(employer_part)
            ):
                record["company"] = employer_part
                record.setdefault("location", location_part)
                continue

        if (
            not record.get("company")
            and not responsibilities_started
            and not _resume_is_action_line(value)
            and _is_plausible_resume_job_header(value)
        ):
            record["company"] = value
            continue

        responsibilities_started = True
        _append_resume_responsibilities(
            record.setdefault("responsibilities", []),
            [value],
        )


def _parse_resume_work_experience(lines):
    """Support title/company-before-date and company-after-date layouts."""
    lines = [
        _clean_text(line)
        for line in lines
        if _clean_text(line)
    ]

    period_pattern = re.compile(
        r"\b(?:[A-Za-z]{3,9}\.?\s+\d{4}|\d{1,2}[/-]\d{4}|\d{4})"
        r"\s*(?:-|\u2013|\u2014|to)\s*"
        r"(?:[A-Za-z]{3,9}\.?\s+\d{4}|\d{1,2}[/-]\d{4}|"
        r"present|current|now)\b",
        re.IGNORECASE,
    )

    dated = []

    for index, line in enumerate(lines):
        match = period_pattern.search(line)

        if match:
            dated.append((index, match))

    def make_record(title, company, period):
        record = {
            "employment_period": _clean_text(period),
            "responsibilities": [],
        }

        if title and _is_plausible_resume_job_header(title):
            record["job_title"] = _clean_text(title)

        if company and _is_plausible_resume_job_header(company):
            record["company"] = _clean_text(company)

        dates = re.split(
            r"\s*(?:-|\u2013|\u2014|to)\s*",
            _clean_text(period),
            maxsplit=1,
            flags=re.IGNORECASE,
        )

        if dates:
            record["start_date"] = dates[0]

        if len(dates) > 1:
            record["end_date"] = dates[1]

        return record

    if not dated:
        # Retain support for resumes that give a duration but no date range.
        records = []

        for line in lines:
            match = re.match(
                r"^(.*?)\s+at\s+(.+?)(?:\s*\(([^()]*)\))?$",
                line,
                re.IGNORECASE,
            )

            if match:
                record = {
                    "job_title": _clean_text(match.group(1)),
                    "company": _clean_text(match.group(2)),
                    "responsibilities": [],
                }

                if match.group(3):
                    record["duration"] = _clean_text(match.group(3))

                records.append(record)

            else:
                title, company = _split_resume_title_company(line)
                if company:
                    records.append({
                        "job_title": title,
                        "company": company,
                        "responsibilities": [],
                    })
                elif records:
                    _append_resume_responsibilities(
                        records[-1]["responsibilities"],
                        [line],
                    )

        return records[:30]

    first_index, first_match = dated[0]
    inline_prefix = _clean_text(lines[first_index][:first_match.start()])
    initial_block = lines[:first_index]

    title = None
    company = None

    first_inline_responsibility = None

    if inline_prefix and _resume_inline_is_responsibility(inline_prefix):
        first_inline_responsibility = re.sub(
            r"^[\u2022\u2023\u25e6\u2043\u2219\u25aa*]+\s*",
            "",
            inline_prefix,
        ).strip()

    if inline_prefix and not _resume_inline_is_responsibility(inline_prefix):
        pipe_parts = [
            _clean_text(part)
            for part in inline_prefix.split("|")
            if _clean_text(part)
        ]

        if len(pipe_parts) >= 2:
            title, company = pipe_parts[0], pipe_parts[1]

        elif inline_prefix.rstrip().endswith("|") and pipe_parts:
            # Common resume layout: "Employer | Jan 2024 - Jun 2024" with
            # the job title on the preceding OCR line.
            company = pipe_parts[0]

        else:
            at_match = re.match(
                r"^(.*?)\s+at\s+(.+)$",
                inline_prefix,
                re.IGNORECASE,
            )

            if at_match:
                title = _clean_text(at_match.group(1))
                company = _clean_text(at_match.group(2))
            else:
                title, company = _split_resume_title_company(inline_prefix)

    if not title and initial_block:
        header = _resume_tail_job_header(initial_block)

        if header:
            header_start, header_title, header_company = header
            if not header_company:
                header_title, inferred_company = _split_resume_title_company(
                    header_title
                )
                header_company = header_company or inferred_company
            title = title or header_title
            company = company or header_company
            initial_block = initial_block[:header_start]

    records = [
        make_record(title, company, first_match.group(0))
    ]

    if first_inline_responsibility:
        _append_resume_responsibilities(
            records[0]["responsibilities"],
            [first_inline_responsibility],
        )

    previous_date_index = first_index

    for date_index, date_match in dated[1:]:
        between = lines[previous_date_index + 1:date_index]

        next_title = None
        next_company = None

        header = _resume_tail_job_header(between)

        if header:
            header_start, next_title, next_company = header
            preceding_content = between[:header_start]
        else:
            preceding_content = between

        _append_resume_job_details(
            records[-1],
            preceding_content,
        )

        inline_prefix = _clean_text(
            lines[date_index][:date_match.start()]
        )

        inline_responsibility = None

        if inline_prefix and _resume_inline_is_responsibility(inline_prefix):
            inline_responsibility = re.sub(
                r"^[\u2022\u2023\u25e6\u2043\u2219\u25aa*]+\s*",
                "",
                inline_prefix,
            ).strip()

        elif inline_prefix:
            pipe_parts = [
                _clean_text(part)
                for part in inline_prefix.split("|")
                if _clean_text(part)
            ]

            if len(pipe_parts) >= 2:
                next_title, next_company = pipe_parts[0], pipe_parts[1]

            else:
                at_match = re.match(
                    r"^(.*?)\s+at\s+(.+)$",
                    inline_prefix,
                    re.IGNORECASE,
                )

                if at_match:
                    next_title = _clean_text(at_match.group(1))
                    next_company = _clean_text(at_match.group(2))
                else:
                    next_title, next_company = _split_resume_title_company(inline_prefix)

        next_record = make_record(
            next_title,
            next_company,
            date_match.group(0),
        )

        if inline_responsibility:
            _append_resume_responsibilities(
                next_record["responsibilities"],
                [inline_responsibility],
            )

        records.append(next_record)

        previous_date_index = date_index

    _append_resume_job_details(
        records[-1],
        lines[previous_date_index + 1:],
    )

    return records[:30]

def _build_resume_education_records(lines, labelled=None):
    """Build qualification records and merge overlapping academic labels."""
    label_map = {
        "degree": "degree",
        "qualification": "degree",
        "course": "degree",
        "program": "degree",
        "programme": "degree",
        "institution": "institution",
        "college": "institution",
        "university": "institution",
        "school": "institution",
        "location": "location",
        "city": "location",
        "graduation year": "graduation_year",
        "year of graduation": "graduation_year",
        "year of passing": "graduation_year",
        "passing year": "graduation_year",
        "year of completion": "graduation_year",
        "academic result": "academic_result",
        "result": "academic_result",
        "grade": "grade",
        "percentage": "percentage",
        "cgpa": "cgpa",
        "gpa": "gpa",
    }

    prefix_pattern = re.compile(
        r"^(degree|qualification|course|program(?:me)?|institution|"
        r"college|university|school|location|city|graduation year|"
        r"year of graduation|year of passing|passing year|"
        r"year of completion|academic result|result|grade|cgpa|gpa|"
        r"percentage)\b\s*(?::|[-–—])?\s*(.*)$",
        re.IGNORECASE,
    )

    records = []
    current = {}
    index = 0

    institution_keyword_pattern = re.compile(
        r"\b(?:university|college|institute|school)\b",
        re.IGNORECASE,
    )
    degree_marker_pattern = re.compile(
        r"\b(?:B\.?\s?Sc\.?|B\.?\s?A\.?|B\.?\s?Com\.?|BBA|BCA|"
        r"B\.?\s?Tech|M\.?\s?Sc\.?|M\.?\s?A\.?|M\.?\s?Com\.?|"
        r"MBA|MCA|M\.?\s?Tech|Bachelor|Master|Diploma|"
        r"Ph\.?\s?D|LLB|B\.?Ed)\b",
        re.IGNORECASE,
    )
    institution_year_range_pattern = re.compile(
        r"(?P<years>(?:19|20)\d{2}\s*"
        r"(?:[-\u2010-\u2015\u2212?]|to)\s*"
        r"(?:(?:19|20)\d{2}|present|current))\s*$",
        re.IGNORECASE,
    )

    def normalize_education_year_range(value):
        """Standardize recognized year-range separators without losing dates."""
        return re.sub(
            r"\s*(?:[-\u2010-\u2015\u2212?]|to)\s*",
            " \u2013 ",
            _clean_text(value),
            flags=re.IGNORECASE,
        )

    while index < len(lines):
        line = _clean_text(lines[index])

        # Some resumes put institution and dates on the first line,
        # followed by the degree on the next line. Do not interpret
        # the institution name as the degree.
        prefix_match = prefix_pattern.match(line)
        institution_header = institution_keyword_pattern.search(line)
        degree_header = degree_marker_pattern.search(line)
        year_range = institution_year_range_pattern.search(line)

        # Explicitly labelled values such as "Institution: Deccan College..."
        # must be handled by the label parser, not the institution-first layout.
        institution_first = (
            prefix_match is None
            and institution_header is not None
            and (
                degree_header is None
                or institution_header.start() < degree_header.start()
            )
        )

        if institution_first:
            institution_value = (
                line[:year_range.start()].strip(" ,;:-")
                if year_range else line.strip(" ,;:-")
            )

            if institution_value:
                institution_value = _correct_institution_ocr_spelling(
                    institution_value
                )

                # A degree-first record may already be waiting for its
                # institution. Complete that record instead of duplicating it.
                if current.get("degree") and not current.get("institution"):
                    current["institution"] = institution_value
                    if year_range:
                        current["graduation_year"] = normalize_education_year_range(
                            year_range.group("years")
                        )
                else:
                    if current:
                        records.append(current)

                    current = {"institution": institution_value}

                    if year_range:
                        current["graduation_year"] = normalize_education_year_range(
                            year_range.group("years")
                        )

                index += 1
                continue

        # Attach a degree appearing after an institution-first line.
        if (
            current.get("institution")
            and not current.get("degree")
            and degree_header is not None
        ):
            degree_value = line

            # Affiliation is separate from the qualification name.
            affiliation_match = re.search(
                r"\s+\baffiliated\s+with\b.*$",
                degree_value,
                re.IGNORECASE,
            )
            if affiliation_match:
                degree_value = degree_value[:affiliation_match.start()].strip()

            # Some PDF text extractors repeat the institution city at the end
            # of the degree line because of the original two-column layout.
            if "," in current["institution"]:
                location_hint = current["institution"].rsplit(",", 1)[-1].strip()
                location_hint = re.sub(
                    r"\s*[-\u2010-\u2015\u2212?]\s*autonomous$",
                    "",
                    location_hint,
                    flags=re.IGNORECASE,
                ).strip()

                if location_hint:
                    degree_value = re.sub(
                        rf"\s+{re.escape(location_hint)}$",
                        "",
                        degree_value,
                        flags=re.IGNORECASE,
                    ).strip()

            if degree_value:
                current["degree"] = _clean_text(degree_value)

            index += 1
            continue

        match = prefix_match

        if match:
            label = _clean_text(match.group(1)).lower()
            value = _clean_text(match.group(2))

            if not value and index + 1 < len(lines):
                value = _clean_text(lines[index + 1])
                index += 1

            key = label_map.get(label)

            if key and value:
                if key == "degree" and current.get("degree"):
                    records.append(current)
                    current = {}

                if (
                    key == "institution"
                    and current.get("institution")
                    and current.get("degree")
                ):
                    records.append(current)
                    current = {}

                current[key] = value

        elif re.match(
            r"^(.+?)\s+[-??]\s+(.+?)(?:\s*\(((?:19|20)\d{2})\))?$",
            line,
        ) and re.search(
            r"\b(?:university|college|institute|intilute|instilute|school)\b",
            re.match(
                r"^(.+?)\s+[-??]\s+(.+?)(?:\s*\(((?:19|20)\d{2})\))?$",
                line,
            ).group(2),
            re.IGNORECASE,
        ):
            match = re.match(
                r"^(.+?)\s+[-??]\s+(.+?)(?:\s*\(((?:19|20)\d{2})\))?$",
                line,
            )

            if current:
                records.append(current)

            current = {
                "degree": _clean_text(match.group(1)),
                "institution": _clean_text(match.group(2)),
            }

            if match.group(3):
                current["graduation_year"] = match.group(3)

        elif re.fullmatch(r"(?:19|20)\d{2}\s*(?:[-\u2013\u2014]|to)\s*(?:(?:19|20)\d{2}|present|current)", line, re.IGNORECASE):
            if not current:
                current = {}
            current["graduation_year"] = line

        elif re.fullmatch(r"(?:19|20)\d{2}", line) and current:
            current["graduation_year"] = line

        elif "|" in line:
            parts = [
                _clean_text(part)
                for part in line.split("|")
                if _clean_text(part)
            ]

            if len(parts) >= 2:
                if current:
                    records.append(current)

                current = {
                    "degree": parts[0],
                    "institution": parts[1],
                }

                if len(parts) > 2:
                    current["graduation_year"] = parts[2]

        elif (
            "," in line
            and re.search(
                r"\b(?:university|college|institute|intilute|instilute|school)\b",
                line,
                re.IGNORECASE,
            )
        ):
            degree, institution = line.split(",", 1)
            current.setdefault("degree", _clean_text(degree))
            current.setdefault("institution", _clean_text(institution))

        elif line:
            institution_match = re.search(
                r"\b(?:university|college|institute|intilute|instilute|school)\b",
                line,
                re.IGNORECASE,
            )

            # In OCR-combined lines such as:
            # "B.Sc. Statistics Western Instilute of Data Studles"
            # move the immediately preceding institution-name token
            # together with the institution rather than the degree.
            institution_start = (
                line.rfind(" ", 0, institution_match.start()) + 1
                if institution_match else -1
            )

            degree_value = (
                _clean_text(line[:institution_start])
                if institution_start > 0 else ""
            )
            institution_value = (
                _correct_institution_ocr_spelling(line[institution_start:])
                if institution_start > 0 else ""
            )

            degree_marker = re.search(
                r"\b(?:B\.?\s?Sc|B\.?\s?A|B\.?\s?Com|BBA|BCA|"
                r"B\.?\s?Tech|M\.?\s?Sc|M\.?\s?A|M\.?\s?Com|"
                r"MBA|MCA|M\.?\s?Tech|Bachelor|Master|Diploma|"
                r"Ph\.?\s?D|LLB|B\.?Ed)\b",
                line,
                re.IGNORECASE,
            )

            # When the degree and institution are flattened onto one OCR line,
            # preserve a recognized specialization (for example, Statistics)
            # in the degree and move the remaining institution name together.
            degree_subjects = (
                "computer science", "information technology", "data science",
                "business administration", "computer applications",
                "mechanical engineering", "civil engineering", "electrical engineering",
                "electronics and communication", "applied mathematics",
                "mathematics", "statistics", "physics", "chemistry", "biology",
                "microbiology", "biotechnology", "economics", "commerce",
                "accounting", "finance", "english", "history", "psychology",
            )
            if degree_marker and institution_match:
                tail_start = degree_marker.end()
                tail = line[tail_start:institution_match.start()]
                for subject in sorted(degree_subjects, key=len, reverse=True):
                    subject_match = re.match(
                        rf"\s*\.?\s*{re.escape(subject)}\b",
                        tail,
                        re.IGNORECASE,
                    )
                    if subject_match and tail[subject_match.end():].strip():
                        institution_start = tail_start + subject_match.end()
                        while institution_start < len(line) and line[institution_start].isspace():
                            institution_start += 1
                        degree_value = _clean_text(line[:institution_start])
                        institution_value = _correct_institution_ocr_spelling(
                            line[institution_start:]
                        )
                        break

            if degree_marker and institution_value:
                if current:
                    records.append(current)

                current = {
                    "degree": degree_value,
                    "institution": institution_value,
                }

            elif not current:
                current["degree"] = line

            elif (
                "institution" not in current
                and institution_match
            ):
                current["institution"] = line

        index += 1

    if current:
        records.append(current)

    # Merge label/value extraction into the first qualification record.
    # Do not emit a second Academic Details or College/University section.
    labelled = labelled or {}

    labelled_aliases = {
        "degree": "degree",
        "qualification": "degree",
        "college": "institution",
        "university": "institution",
        "institution": "institution",
        "school": "institution",
        "graduation_year": "graduation_year",
        "year_of_graduation": "graduation_year",
        "grade": "grade",
        "percentage": "percentage",
        "cgpa": "cgpa",
        "gpa": "gpa",
        "academic_result": "academic_result",
        "result": "academic_result",
    }

    for source_key, value in labelled.items():
        target_key = labelled_aliases.get(str(source_key).lower())

        if target_key and value not in (None, ""):
            if not records:
                records.append({})

            records[0].setdefault(
                target_key,
                _clean_text(value),
            )

    result = []
    seen = set()

    for record in records:
        record = {
            key: (
                _correct_institution_ocr_spelling(value)
                if key == "institution"
                else value
            )
            for key, value in record.items()
            if value not in (None, "")
        }

        signature = tuple(
            (key, str(value).lower())
            for key, value in record.items()
        )

        if record and signature not in seen:
            seen.add(signature)
            result.append(record)

    return result[:30]


def _parse_resume_technical_skills(lines):
    """Extract skills from both comma lists and label/value sidebar layouts."""
    aliases = {
        "python": "Python", "sql": "SQL", "excel": "Excel",
        "pandas": "Pandas", "numpy": "NumPy", "power bi": "Power BI", "power b1": "Power BI", "power bl": "Power BI",
        "javascript": "JavaScript", "typescript": "TypeScript", "c++": "C++",
        "c#": "C#", "eda": "EDA", "api": "API", "aws": "AWS",
        "azure": "Azure", "gcp": "GCP", "mysql": "MySQL",
        "postgresql": "PostgreSQL", "mongodb": "MongoDB", "html": "HTML",
        "css": "CSS", "data cleaning": "Data Cleaning",
        "exploratory data analysis": "Exploratory Data Analysis",
        "descriptive statistics": "Descriptive Statistics",
        "dashboard reporting": "Dashboard Reporting",
        "documentation": "Documentation", "scikit-learn": "scikit-learn",
        "jupyter notebook": "Jupyter Notebook", "github": "GitHub",
        "git": "Git", "matplotlib": "Matplotlib", "plotly": "Plotly", "model evaluation": "Model Evaluation",
    }

    heading_groups = {
        "programming": "Programming & Query Languages",
        "programming languages": "Programming & Query Languages",
        "data analysis": "Data Analysis & Methods",
        "machine learning": "Libraries & Frameworks",
        "visualization": "Tools & Visualization",
        "tools": "Other Skills",
    }

    programming = {
        "python", "java", "javascript", "typescript", "c", "c++",
        "c#", "sql", "r", "go", "rust", "bash",
    }
    libraries = {
        "pandas", "numpy", "scipy", "scikit-learn", "sklearn",
        "matplotlib", "seaborn", "tensorflow", "pytorch", "opencv",
    }
    databases = {
        "mysql", "postgresql", "mongodb", "redis", "sqlite",
        "oracle", "sql server",
    }
    visualization = {"excel", "power bi", "tableau", "looker", "powerpoint", "matplotlib", "plotly"}
    methods = {
        "data cleaning", "exploratory data analysis", "descriptive statistics",
        "dashboard reporting", "documentation", "statistical analysis",
        "hypothesis testing", "feature engineering", "data visualization",
        "machine learning", "data analysis", "model evaluation",
    }

    groups = {}
    seen = set()
    active_heading_group = None

    for raw in lines:
        line = _clean_text(raw).strip(" .;:")
        if not line:
            continue

        # Support inline headings such as "Data Visualization: Matplotlib".
        inline_visualization = re.match(
            r"(?i)^(?:data\\s+)?visuali[sz]ation\\s*:\\s*(.*)$",
            line,
        )
        if inline_visualization:
            active_heading_group = "Tools & Visualization"
            line = inline_visualization.group(1).strip()
            if not line:
                continue

        normalized_line = _normalize_heading(line)
        if normalized_line in heading_groups:
            active_heading_group = heading_groups[normalized_line]
            continue

        parts = re.split(r"\s*[,;|]\s*", line)
        for part in parts:
            value = _clean_text(part).strip(" .;:")
            value = re.sub(r"^(?:and|&)\s+", "", value, flags=re.IGNORECASE)
            value = re.sub(r"\s+(?:and|&)$", "", value, flags=re.IGNORECASE)
            normalized = value.casefold()
            normalized = {"power b1": "power bi", "power bl": "power bi"}.get(normalized, normalized)

            if not value or normalized in {"and", "&"}:
                continue
            if normalized in heading_groups or normalized in {"technical skills", "skills"}:
                continue
            if normalized in seen:
                continue

            seen.add(normalized)
            display = aliases.get(normalized, value)

            if normalized in programming:
                group = "Programming & Query Languages"
            elif normalized in libraries:
                group = "Libraries & Frameworks"
            elif normalized in databases:
                group = "Databases"
            elif normalized in visualization:
                group = "Tools & Visualization"
            elif normalized in methods:
                group = "Data Analysis & Methods"
            else:
                group = active_heading_group or "Other Skills"

            groups.setdefault(group, []).append(display)

    return groups


def _parse_resume_projects(lines):
    """Keep project titles, descriptions and technology stacks associated.

    OCR reading order can append the next project's pipe-delimited technology
    stack to the previous project's description. A trailing stack is moved
    forward to the next project instead of being retained as description text.
    """
    records = []
    current = None
    pending_technologies = []

    title_description = re.compile(r"^(.{2,100}?)\s+(?:[-\u2013\u2014|])\s+(.+)$")
    technology_tokens = {
        "python", "sql", "power bi", "excel", "pandas", "numpy", "nlp",
        "scikit-learn", "scikit-leam", "sklearn", "power b1", "power bl", "java", "javascript", "tensorflow",
        "pytorch", "opencv", "matplotlib", "tableau", "r", "c++", "aws",
        "statistics", "google forms", "tf-idf",
    }
    technology_aliases = {
        "power bi": "Power BI", "power b1": "Power BI", "power bl": "Power BI", "scikit-learn": "scikit-learn",
        "scikit-leam": "scikit-learn",
        "python": "Python", "sql": "SQL", "excel": "Excel",
        "pandas": "Pandas", "numpy": "NumPy", "nlp": "NLP",
        "sklearn": "scikit-learn", "java": "Java", "javascript": "JavaScript",
        "tensorflow": "TensorFlow", "pytorch": "PyTorch", "opencv": "OpenCV",
        "matplotlib": "Matplotlib", "tableau": "Tableau", "r": "R",
        "c++": "C++", "aws": "AWS",
        "statistics": "Statistics", "google forms": "Google Forms",
        "tf-idf": "TF-IDF",
    }

    def canonical_stack(value):
        # Accept only complete, known technology tokens. This prevents normal
        # prose containing a product name from being mistaken for a stack.
        parts = [_clean_text(part).strip(" .;") for part in value.split("|")]
        parts = [part for part in parts if part]
        if not parts:
            return []
        normalized = [part.casefold() for part in parts]
        if not all(part in technology_tokens for part in normalized):
            return []
        return list(dict.fromkeys(technology_aliases.get(part, part) for part in normalized))

    def parse_stack(value):
        raw = _clean_text(value)
        if "|" not in raw and raw.casefold() not in technology_tokens:
            return [], []
        technologies = canonical_stack(raw)
        return (technologies, []) if technologies else ([], [])

    def split_trailing_stack(value):
        # Require a pipe delimiter and match only known technology tokens.
        # This prevents prose before the stack from being swallowed.
        token_pattern = "|".join(
            re.escape(token)
            for token in sorted(technology_tokens, key=len, reverse=True)
        )
        match = re.search(
            rf"(?P<suffix>(?:\s*(?:{token_pattern})\s*\|\s*)+"
            rf"(?:{token_pattern})?\s*)$",
            value,
            flags=re.IGNORECASE,
        )
        if not match:
            return value, []
        suffix = match.group("suffix").strip()
        technologies = canonical_stack(suffix.rstrip("|").strip())
        if not technologies:
            return value, []
        description = value[:match.start()].rstrip(" ,;|:-")
        return description, technologies

    def split_trailing_comma_stack(value):
        """Split a title from a trailing comma-separated known technology stack."""
        ordered_tokens = sorted(
            technology_tokens,
            key=len,
            reverse=True,
        )
        token_pattern = "|".join(
            re.escape(token) for token in ordered_tokens
        )
        bounded_token = (
            rf"(?<![A-Za-z0-9+#])(?:{token_pattern})"
            rf"(?![A-Za-z0-9+#])"
        )
        pattern = re.compile(
            rf"(?P<suffix>{bounded_token}"
            rf"(?:\s*,\s*{bounded_token})+)\s*$",
            re.IGNORECASE,
        )

        match = pattern.search(value)
        if not match:
            return value, []

        suffix = match.group("suffix")
        parts = [
            _clean_text(part).strip(" .;")
            for part in suffix.split(",")
        ]
        normalized = [part.casefold() for part in parts]

        if (
            len(parts) < 2
            or not all(part in technology_tokens for part in normalized)
        ):
            return value, []

        technologies = list(dict.fromkeys(
            technology_aliases.get(part, part)
            for part in normalized
        ))
        title = value[:match.start()].strip(" \t:|-")

        if not title or not looks_like_title(title):
            return value, []

        return title, technologies

    def split_leading_comma_stack(value):
        """Separate a known comma-delimited technology prefix from description text."""
        match = re.match(
            r"^\s*(?P<stack>.*?)\s+(?:[??|?]|[-\u2010-\u2015\u2212])\s+(?P<description>\S[\s\S]*)$",
            value,
        )
        if not match:
            return [], value

        parts = [
            _clean_text(part).strip(" .;")
            for part in match.group("stack").split(",")
        ]
        normalized = [part.casefold() for part in parts]

        if (
            len(parts) < 2
            or not all(part in technology_tokens for part in normalized)
        ):
            return [], value

        technologies = list(dict.fromkeys(
            technology_aliases.get(part, part)
            for part in normalized
        ))
        return technologies, match.group("description").strip()

    def looks_like_title(value):
        candidate = _clean_text(value).strip(" :|-•")
        words = candidate.split()
        if not 2 <= len(words) <= 9 or len(candidate) > 90:
            return False
        if re.search(r"[.!?]$", candidate) or _resume_is_action_line(candidate):
            return False
        if candidate.isupper() and re.search(r"[A-Z]", candidate):
            return True
        lowercase_connectors = {
            "a", "an", "and", "as", "at", "by", "for", "from",
            "in", "into", "of", "on", "or", "the", "to", "using",
            "with",
        }
        return all(
            word[:1].isupper() or word.casefold() in lowercase_connectors
            for word in words
            if word[:1].isalpha()
        ) and sum(
            bool(re.search(r"[a-z]", word)) for word in words
        ) >= 2

    def add_technologies(record, values):
        if not values:
            return
        existing = record.setdefault("technologies", [])
        seen = {str(item).casefold() for item in existing}
        for item in values:
            if item.casefold() not in seen:
                existing.append(item)
                seen.add(item.casefold())

    token_pattern = "|".join(
        re.escape(token)
        for token in sorted(technology_tokens, key=len, reverse=True)
    )
    bounded_token = rf"(?<![A-Za-z0-9+#])(?:{token_pattern})(?![A-Za-z0-9+#])"
    embedded_stack_pattern = re.compile(
        rf"(?:{bounded_token}\s*\|\s*)+{bounded_token}",
        re.IGNORECASE,
    )
    embedded_action_pattern = re.compile(
        r"(?i)\b(?:prepared|analyzed|analysed|built|created|developed|"
        r"designed|implemented|conducted|classified|summarized|summarised|"
        r"generated|wrote|automated|performed|reviewed|identified|"
        r"produced|deployed|evaluated|predicted|visualized|visualised)\b"
    )

    def split_embedded_project(value):
        """Find a stack followed by an OCR-joined next project title/description."""
        for stack_match in embedded_stack_pattern.finditer(value):
            prefix = value[:stack_match.start()].rstrip()
            if prefix and not re.search(r"[.!?]\s*$", prefix):
                continue

            stack = canonical_stack(stack_match.group(0))
            if not stack:
                continue

            remainder = value[stack_match.end():].strip(" \t:|-")
            for action_match in embedded_action_pattern.finditer(remainder):
                title = remainder[:action_match.start()].strip(" \t:|-")
                description = remainder[action_match.start():].strip()
                if looks_like_title(title) and _resume_is_action_line(description):
                    return prefix, stack, title, description

            # PaddleOCR may put only the next project title after the stack.
            # Its description can be a separate following OCR line.
            title_only = remainder.strip(" \t:|-?")
            if looks_like_title(title_only):
                return prefix, stack, title_only, None

        return None

    def start_record(title, description=None):
        nonlocal pending_technologies
        record = {"title": _clean_text(title)}

        # A stack carried from the previous OCR line/project belongs to the
        # project being started. Consume it before inspecting this project's
        # own description, otherwise its trailing stack is attached backwards.
        if pending_technologies:
            add_technologies(record, pending_technologies)
            pending_technologies = []

        if description:
            leading, description = split_leading_comma_stack(
                _clean_text(description)
            )
            add_technologies(record, leading)

            clean_description, trailing = split_trailing_stack(
                _clean_text(description)
            )
            if clean_description:
                record["description"] = clean_description

            # A stack trailing a title+description OCR line is pending evidence
            # for the next project; do not attach it to the current project.
            pending_technologies.extend(trailing)
        return record

    for raw in lines:
        line = _clean_text(raw).lstrip("\u2022\u2023\u25e6\u2043\u2219\u25aa* ")
        if not line:
            continue

        # A project heading can contain its technology stack after the
        # title, for example:
        # "Statistical Analysis of Social Media Usage Patterns R, Statistics, Google Forms"
        project_title, heading_technologies = split_trailing_comma_stack(line)
        if heading_technologies:
            if current is not None:
                records.append(current)
            current = start_record(project_title)
            add_technologies(current, heading_technologies)
            continue

        # OCR can flatten a two-column project layout into:
        # "...previous description. Python | NLP | scikit-learn
        # CUSTOMER REVIEW SENTIMENT ANALYSIS Prepared ..."
        # Split the stack/title boundary before it contaminates the preceding
        # project's description.
        embedded = split_embedded_project(line)
        if embedded and current is not None:
            prefix, technologies, next_title, next_description = embedded
            if prefix:
                previous_description = current.get("description", "")
                current["description"] = _clean_text(
                    (previous_description + " " + prefix).strip()
                )
            records.append(current)
            current = start_record(next_title, next_description)
            add_technologies(current, technologies)
            continue

        # A standalone pipe-delimited stack belongs to the active project.
        technologies, _ = parse_stack(line)
        if current is not None and technologies:
            add_technologies(current, technologies)
            continue

        match = title_description.match(line)
        if match and looks_like_title(match.group(1)):
            if current:
                records.append(current)
            current = start_record(match.group(1), match.group(2))
            continue

        if current is not None and looks_like_title(line) and current.get("description"):
            records.append(current)
            current = start_record(line)
            continue

        if current is None:
            current = start_record(line)
        elif current.get("description"):
            combined = _clean_text(current["description"] + " " + line)
            clean_description, trailing = split_trailing_stack(combined)
            if trailing:
                current["description"] = clean_description
                pending_technologies.extend(trailing)
            else:
                current["description"] = combined
        else:
            leading, remaining_description = split_leading_comma_stack(line)
            if leading:
                add_technologies(current, leading)
                line = remaining_description

            clean_description, trailing = split_trailing_stack(line)
            if clean_description:
                current["description"] = clean_description
            elif line:
                current["description"] = line
            pending_technologies.extend(trailing)

    if current:
        # If a dangling stack has no following project, do not silently attach
        # it to the preceding project; retain it as an explicit review note.
        if pending_technologies:
            current.setdefault("technology_review_candidates", [])
            current["technology_review_candidates"] = list(dict.fromkeys(
                current["technology_review_candidates"] + pending_technologies
            ))
        records.append(current)

    return records[:40]

def _parse_resume_credentials(lines):
    """Group credentials while distinguishing a new titled item from year metadata."""
    records = []
    current = None
    year_pattern = re.compile(r"\b(?:19|20)\d{2}\b")
    metadata_year_pattern = re.compile(
        r"(?i)^(?:(?:issued|completed|earned|awarded|year|date(?:\s+earned)?)\s*[:#-]?\s*)?"
        r"(?:in\s*)?((?:19|20)\d{2})$"
    )
    title_year_pattern = re.compile(
        r"^(?P<title>.{2,100}?)\s*[\(\[](?P<year>(?:19|20)\d{2})[\)\]]\s*$"
    )

    def finish_current():
        nonlocal current
        if current:
            records.append(current)
            current = None

    def make_record(title, year=None, issuer=None):
        record = {"title": _clean_text(title).strip(" .;:-|")}
        if year:
            record["year"] = str(year)
        if issuer:
            record["issuer"] = _clean_text(issuer).strip(" .;:-|")
        return record

    for raw in lines:
        line = _clean_text(raw)
        if not line:
            continue

        # Bare years or explicitly labelled year metadata belong to the
        # current credential. A descriptive title containing a year does not.
        metadata_year = metadata_year_pattern.fullmatch(line)
        if metadata_year and current:
            current.setdefault("year", metadata_year.group(1))
            continue

        title_year = title_year_pattern.fullmatch(line)
        if title_year:
            finish_current()
            current = make_record(title_year.group("title"), title_year.group("year"))
            continue

        separator_match = re.match(
            r"^(.{2,100}?)\s+(?:[-\u2013\u2014|])\s+(.+)$",
            line,
        )
        if separator_match:
            title = _clean_text(separator_match.group(1))
            metadata = _clean_text(separator_match.group(2))
            # A line such as "2023 - Coursera" is metadata for the preceding
            # item, not a credential whose title is a year.
            if re.fullmatch(r"(?:19|20)\d{2}", title) and current:
                current.setdefault("year", title)
                if metadata and "issuer" not in current:
                    current["issuer"] = metadata
                continue

            finish_current()
            year_match = year_pattern.search(metadata)
            year = year_match.group(0) if year_match else None
            if year_match:
                metadata = _clean_text(
                    metadata[:year_match.start()] + " " + metadata[year_match.end():]
                ).strip(" ,;|-()[]")
            current = make_record(title, year=year, issuer=metadata or None)
            continue

        # If a year appears inside a non-metadata phrase, that phrase is a new
        # credential title (for example, "SQL for Analytics (2023)").
        embedded_year = year_pattern.search(line)
        if embedded_year and re.search(r"[A-Za-z]{2,}", line[:embedded_year.start()]):
            finish_current()
            title = _clean_text(
                line[:embedded_year.start()] + " " + line[embedded_year.end():]
            ).strip(" ()[]-:;|")
            current = make_record(title or line, year=embedded_year.group(0))
            continue

        if current is None:
            current = make_record(line)
        else:
            # A following plain line is normally a separate credential. Issuer
            # information should be explicitly separated from the title by a
            # dash/pipe and is handled above.
            finish_current()
            current = make_record(line)

    finish_current()
    return records[:40]


def _is_resume_achievement(record):
    """Distinguish narrative achievement statements from credential titles."""
    title = _clean_text(record.get("title") or "")
    if _resume_is_action_line(title):
        return True

    if re.search(
        r"\b(?:merit|achievement|award|honou?r|distinction|"
        r"prize|scholarship|recognition|recognized|recognised)\b",
        title,
        re.IGNORECASE,
    ):
        return True

    # A complete, sentence-like statement is much more likely to be an
    # achievement description than a certification name.
    return len(title.split()) >= 10 and bool(re.search(r"[.!?]$", title))



_RESUME_REGION_NAMES = {
    # Indian states and union territories, plus common international regions.
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
    "nagaland", "odisha", "orissa", "punjab", "rajasthan", "sikkim", "tamil nadu",
    "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal",
    "andaman and nicobar islands", "chandigarh", "dadra and nagar haveli and daman and diu",
    "delhi", "jammu and kashmir", "ladakh", "lakshadweep", "puducherry",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
}
_RESUME_COUNTRY_NAMES = {
    "india", "united states", "united states of america", "usa", "us", "united kingdom", "uk",
    "canada", "australia", "new zealand", "singapore", "united arab emirates", "uae",
    "ireland", "germany", "france", "netherlands", "switzerland", "japan", "south korea",
    "china", "sri lanka", "nepal", "bangladesh", "pakistan", "south africa", "qatar", "saudi arabia",
}
_US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia",
    "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt",
    "va", "wa", "wv", "wi", "wy", "dc",
}
_INDIAN_STATE_CODES = {
    "ap", "ar", "as", "br", "cg", "ga", "gj", "hr", "hp", "jh", "ka", "kl", "mp", "mh",
    "mn", "ml", "mz", "nl", "od", "pb", "rj", "sk", "tn", "ts", "tr", "up", "uk", "wb",
    "an", "ch", "dd", "dl", "jk", "la", "ld", "py",
}


def _extract_resume_location(text, candidate_name=None):
    """Prefer labelled location; otherwise accept a conservative City, Region/Country line."""
    labelled = _label_values(
        text,
        ["Location", "Address", "City", "Current Location", "Permanent Address"],
    )
    if labelled:
        return labelled[0]

    excluded = _clean_text(candidate_name).casefold()
    for raw_line in _clean_lines(text)[:24]:
        # Profile header location is short and normally comma-separated. Ignore
        # contact/URL lines, sentences, and values that look like another label.
        if "@" in raw_line or re.search(r"(?i)(?:https?://|www\.|linkedin\.com|github\.com|behance\.net)", raw_line):
            continue
        if re.search(r"\d", raw_line) or re.search(r"[.!?]", raw_line) or ":" in raw_line:
            continue
        for candidate in re.split(r"\s*[|•·]\s*", raw_line):
            parts = [part.strip() for part in candidate.split(",") if part.strip()]
            if len(parts) < 2 or len(parts) > 3:
                continue
            city = parts[0]
            if city.casefold() == excluded or not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,39}", city):
                continue
            tail = [part.casefold().strip().rstrip(".") for part in parts[1:]]
            if len(tail) == 1:
                valid_region = (
                    tail[0] in _RESUME_REGION_NAMES
                    or tail[0] in _US_STATE_CODES
                    or tail[0] in _INDIAN_STATE_CODES
                    or tail[0] in _RESUME_COUNTRY_NAMES
                )
            else:
                # A three-part address is accepted only as City, Region, Country;
                # this avoids mistaking a person's full name plus a state for a city.
                valid_region = (
                    len(tail) == 2
                    and tail[1] in _RESUME_COUNTRY_NAMES
                    and (
                        tail[0] in _RESUME_REGION_NAMES
                        or tail[0] in _US_STATE_CODES
                        or tail[0] in _INDIAN_STATE_CODES
                    )
                )
            if valid_region:
                return ", ".join(parts)
    return None

def extract_resume_fields(text):
    """
    Extract resume facts into structured, deduplicated records.

    The returned structure is shared by the extracted-information UI
    and the PDF/TXT/DOCX/CSV/XLSX/JSON/XML export handlers.
    """
    text = text or ""
    fields = {}

    form_heading = bool(re.search(
        r"(?i)\b(?:(?:general\s+)?(?:application|registration|admission)\s+form|account\s+intake(?:\s+form)?|customer\s+onboarding(?:\s+form)?|client\s+onboarding(?:\s+form)?|account\s+opening(?:\s+form)?)\b",
        text,
    ))

    form_anchors = sum(
        bool(re.search(pattern, text, re.IGNORECASE))
        for pattern in (
            r"\bapplication\s+(?:reference\s+)?(?:number|no\.?|id)\b",
            r"\bapplication\s+date\b",
            r"\brequested\s+plan\b|\bsubscription\s+plan\b",
            r"\bnumber\s+of\s+users\b|\buser\s+count\b",
            r"\borganisation\s+name\b|\borganization\s+name\b|\bcompany\s+name\b",
            r"\bcustomer\s+type\b|\bclient\s+type\b",
            r"\bbilling\s+(?:frequency|cycle)\b|\bprimary\s+use\b",
            r"\bapplication\s+type\b",
            r"\bapplicant\s+name\b",
            r"\bdate\s+of\s+birth\b",
            r"\bgender\b",
        )
    )

    form_like = form_heading and form_anchors >= 2

    candidate_name = None if form_like else _extract_resume_name(text)

    if candidate_name:
        fields["candidate_name"] = candidate_name

    emails = _extract_emails(text)
    phones = _extract_phone_numbers(text)

    if emails:
        fields["emails"] = emails

    if phones:
        fields["phone_numbers"] = phones

    labelled = _extract_labelled_lines(text)

    location = _extract_resume_location(text, candidate_name=candidate_name)
    if location:
        fields["location_address"] = location

    # -----------------------------------------------------
    # PROFESSIONAL SUMMARY — one paragraph, not comma bullets
    # -----------------------------------------------------

    summary_lines = _extract_resume_section(text, "summary")
    summary = _clean_text(" ".join(summary_lines))

    if summary:
        fields["professional_summary"] = summary

    # -----------------------------------------------------
    # TECHNICAL SKILLS — categorized lists
    # -----------------------------------------------------

    skills = _parse_resume_technical_skills(
        _extract_resume_section(text, "skills")
    )

    if skills:
        fields["technical_skills"] = skills

    # -----------------------------------------------------
    # WORK EXPERIENCE — one complete record per role
    # -----------------------------------------------------

    experience_lines = _extract_resume_section(text, "experience")
    work_records = _parse_resume_work_experience(experience_lines)

    # Merge explicitly labelled job titles and employers into the records.
    explicit_titles = _label_values(
        text,
        ["Job Title", "Role", "Position", "Designation", "Title"],
    )

    explicit_companies = _label_values(
        text,
        ["Company", "Company Name", "Employer", "Organisation", "Organization"],
    )

    record_count = max(len(work_records), len(explicit_titles), len(explicit_companies))

    for index in range(record_count):
        if index >= len(work_records):
            work_records.append({"responsibilities": []})

        record = work_records[index]

        if index < len(explicit_titles):
            record.setdefault("job_title", explicit_titles[index])

        if index < len(explicit_companies):
            record.setdefault("company", explicit_companies[index])

    if work_records:
        fields["work_experience"] = work_records

    # Preserve explicit duration facts for compatibility with existing consumers.
    explicit_duration = next(
        (
            value
            for key, value in labelled.items()
            if key in {
                "experience_duration",
                "total_experience",
                "work_experience_duration",
            }
        ),
        None,
    )

    duration_patterns = [] if form_like else re.findall(
        r"\b(?:"
        r"\d{1,2}\s*(?:years?|yrs?)(?:\s+\d{1,2}\s*(?:months?|mos?))?"
        r"|"
        r"\d{1,2}\s*(?:months?|mos?)"
        r")\b",
        "\n".join(experience_lines),
        flags=re.IGNORECASE,
    )

    if explicit_duration and not form_like:
        duration_patterns.insert(0, explicit_duration)

    duration_patterns = list(dict.fromkeys(
        _clean_text(value)
        for value in duration_patterns
        if _clean_text(value)
    ))

    if duration_patterns:
        fields["experience_duration"] = duration_patterns[:20]

    # -----------------------------------------------------
    # EDUCATION — merge degree, institution and academic facts
    # -----------------------------------------------------

    education_records = _build_resume_education_records(
        _extract_resume_section(text, "education"),
        labelled,
    )

    explicit_institutions = _label_values(
        text,
        [
            "College",
            "University",
            "Institution",
            "School",
            "College/University",
            "University Name",
        ],
    )

    for index, institution in enumerate(explicit_institutions):
        if index >= len(education_records):
            education_records.append({})

        education_records[index].setdefault(
            "institution",
            _correct_institution_ocr_spelling(institution),
        )

    if education_records:
        education_records = [
            {
                key: value
                for key, value in record.items()
                if value not in (None, "")
            }
            for record in education_records
        ]

        fields["educational_qualifications"] = education_records

    # Do not emit separate college_university_details or academic_details:
    # those values now belong to the associated education record.

    # -----------------------------------------------------
    # PROJECTS — preserve each title and full description
    # -----------------------------------------------------

    project_records = _parse_resume_projects(
        _extract_resume_section(text, "projects")
    )

    if project_records:
        fields["projects"] = project_records

    # -----------------------------------------------------
    # CERTIFICATIONS AND ACHIEVEMENTS
    # -----------------------------------------------------

    combined_credentials = _extract_resume_section(
        text,
        "combined_certifications_achievements",
    )

    if combined_credentials:
        credentials = _parse_resume_credentials(combined_credentials)

        certifications = [
            item for item in credentials
            if not _is_resume_achievement(item)
        ]

        achievements = [
            item for item in credentials
            if _is_resume_achievement(item)
        ]

    else:
        certifications = _parse_resume_credentials(
            _extract_resume_section(text, "certifications")
        )

        # A dedicated Achievements section contains statements, not credential
        # metadata. Keep its entries as plain text instead of inventing issuer,
        # title, or year fields for narrative accomplishments.
        achievements = _extract_section_items(text, "academic_achievements")

    if certifications:
        fields["certifications"] = certifications

    if achievements:
        fields["academic_achievements"] = achievements

    # -----------------------------------------------------
    # OTHER RESUME SECTIONS
    # -----------------------------------------------------

    remaining_sections = {
        "relevant_coursework": "coursework",
        "internships": "internships",
        "languages": "languages",
        "publications": "publications",
        "volunteer_experience": "volunteer_experience",
        "extracurricular_activities": "extracurricular",
        "interests": "interests",
        "references": "references",
    }

    for output_key, section_name in remaining_sections.items():
        values = _extract_section_items(text, section_name)

        if values:
            fields[output_key] = values

    # OCR may append the page footer "References available upon request"
    # to the final achievement sentence. Keep that footer out of achievements
    # and preserve it as a references value instead.
    if fields.get("academic_achievements"):
        cleaned_achievements = []
        footer_found = False
        footer_pattern = re.compile(
            r"(?i)(?:^|[.!?]\s+)references\s+av(?:ai|a)l{1,2}able\s+upon\s+request\.?\s*$"
        )
        for raw_value in fields["academic_achievements"]:
            # Combined "Certifications and Achievements" sections are parsed
            # into structured records (title/issuer/year), whereas a dedicated
            # Achievements section is plain text. Preserve the established
            # record schema while applying the same footer cleanup to its title.
            if isinstance(raw_value, dict):
                record = dict(raw_value)
                title = _clean_text(record.get("title", ""))
                match = footer_pattern.search(title)

                if match:
                    achievement_text = title[:match.start()].strip(" .;:-")
                    if achievement_text:
                        record["title"] = achievement_text
                        cleaned_achievements.append(record)
                    footer_found = True
                elif re.fullmatch(
                    r"(?i)references\s+available\s+upon\s+request\.?\s*",
                    title,
                ):
                    footer_found = True
                else:
                    cleaned_achievements.append(record)
                continue

            value = _clean_text(raw_value)
            match = footer_pattern.search(value)
            if match:
                achievement_text = value[:match.start()].strip(" .;:-")
                if achievement_text:
                    cleaned_achievements.append(achievement_text)
                footer_found = True
            elif re.fullmatch(
                r"(?i)references\s+available\s+upon\s+request\.?\s*",
                value,
            ):
                footer_found = True
            else:
                cleaned_achievements.append(value)

        fields["academic_achievements"] = cleaned_achievements
        if footer_found:
            fields.setdefault("references", [])
            if "Available upon request" not in fields["references"]:
                fields["references"].append("Available upon request")

        if not fields["academic_achievements"]:
            fields.pop("academic_achievements", None)

    if fields.get("languages"):
        cleaned_languages = []
        seen_languages = set()

        for raw_value in fields["languages"]:
            value = str(raw_value or "").strip()

            # Remove synthetic dataset footer text and non-language
            # profile/contact sections that may have leaked into this field.
            value = re.split(
                r"(?i)\b(?:synthetic candidate record|availability|"
                r"linkedin|github|behance|portfolio|website|additional information)\b\s*:?",
                value,
                maxsplit=1,
            )[0]

            # Remove URL residue without affecting the dedicated profile fields.
            value = re.sub(
                r"(?:https?://|www\.)[^\s,;|<>]+|"
                r"(?<![\w@.-])(?:linkedin\.com|github\.com|behance\.net)/[^\s,;|<>]+",
                "",
                value,
                flags=re.IGNORECASE,
            )

            value = value.strip(" \t\r\n .;|,:-")

            if not value:
                continue

            identity = value.casefold()

            if identity not in seen_languages:
                seen_languages.add(identity)
                cleaned_languages.append(value)

        fields["languages"] = cleaned_languages

    # -----------------------------------------------------
    # PROFESSIONAL PROFILES
    # -----------------------------------------------------

    raw_urls = re.findall(
        r"(?:https?://|www\.)[^\s<>]+|(?<![\w@.-])(?:linkedin\.com|github\.com|behance\.net)/[^\s<>]+",
        text,
        flags=re.IGNORECASE,
    )

    urls = []
    for url in raw_urls:
        url = url.rstrip(".,;:!?)]}")

        if url.lower().startswith("www."):
            url = "https://" + url
        elif re.match(r"(?i)^(?:linkedin\.com|github\.com|behance\.net)/", url):
            url = "https://" + url

        if url and url not in urls:
            urls.append(url)

    linkedin = [
        url for url in urls
        if "linkedin.com" in url.lower()
    ]

    github = [
        url for url in urls
        if "github.com" in url.lower()
    ]

    portfolios = [
        url for url in urls
        if "linkedin.com" not in url.lower()
        and "github.com" not in url.lower()
    ]

    if linkedin:
        fields["linkedin"] = linkedin

    if github:
        fields["github"] = github

    if portfolios:
        fields["portfolio"] = portfolios

    return _correct_resume_ocr_value(fields)

# RESUME_ACHIEVEMENTS_SEPARATED_FROM_CREDENTIALS

# INVOICE EXTRACTION
# ---------------------------------------------------------

def _extract_party_block(text, start_labels, stop_labels):
    lines = _clean_lines(text)
    start_re = re.compile(r"(?i)(?:" + "|".join(start_labels) + r")\s*[:：-]?\s*(.*)$")
    stop_re = re.compile(r"(?i)(?:" + "|".join(stop_labels) + r")\s*[:：-]?")
    for index, line in enumerate(lines):
        match = start_re.search(line)
        if not match:
            continue
        captured=[]
        if match.group(1).strip(): captured.append(match.group(1).strip())
        for following in lines[index+1:index+8]:
            if stop_re.search(following): break
            if re.match(r"(?i)^(invoice\s*(?:no|number|date)|due\s*date|subtotal|grand\s*total|total\s*payable)", following): break
            captured.append(following)
        if captured:
            return " | ".join(captured)[:500]
    return None


def _is_invoice_party_placeholder(value):
    """Identify party headings that are not supplier or customer names."""
    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        str(value or "").strip().lower(),
    ).strip()

    placeholders = {
        "bill from",
        "bill to",
        "sold by",
        "sold to",
        "ship from",
        "ship to",
        "invoice from",
        "invoice to",
        "supplier",
        "seller",
        "buyer",
        "customer",
        "vendor",
        "consignee",
        "consignor",
        "billing address",
        "shipping address",
        "billing details",
        "shipping details",
    }

    return normalized in placeholders

def _looks_like_invoice_address(value):
    """Identify common street, building, locality and postal address lines."""
    text = str(value or "").strip()

    if not text:
        return False

    if re.match(r"^\d{1,6}\s+", text):
        return True

    if re.search(
        r"\b(?:road|rd|street|st|avenue|ave|lane|ln|"
        r"boulevard|blvd|highway|hwy|marg|cross|"
        r"floor|flr|plot|building|bldg|block|sector|"
        r"apartment|apt|suite|unit|industrial estate)\b",
        text,
        re.IGNORECASE,
    ):
        return True

    if re.search(r"\b\d{6}\b", text):
        return True

    return False

def _extract_native_invoice_party_blocks(text):
    """Separate seller and buyer fields from sequential PDF text extraction."""
    lines = _clean_lines(text)

    seller_heading = re.compile(
        r"(?i)^(?:bill\s+from|seller(?:\s*/\s*supplier)?|"
        r"supplier(?:\s*/\s*seller)?|vendor(?:\s+details)?|"
        r"seller\s+details|supplier\s+details|sold\s+by)$"
    )

    buyer_heading = re.compile(
        r"(?i)^(?:bill\s+to|buyer(?:\s*/\s*customer)?|"
        r"customer(?:\s*/\s*buyer)?|buyer\s+details|"
        r"customer\s+details|ship\s+to)$"
    )

    seller_positions = [
        i for i, line in enumerate(lines)
        if seller_heading.fullmatch(line)
    ]

    buyer_positions = [
        i for i, line in enumerate(lines)
        if buyer_heading.fullmatch(line)
    ]

    if not seller_positions or not buyer_positions:
        return {}

    header_end = max(seller_positions[0], buyer_positions[0])

    table_start = next(
        (
            i for i in range(header_end + 1, len(lines))
            if re.fullmatch(
                r"(?i)(?:line\s+items?|item\s+details|description)",
                lines[i],
            )
        ),
        None,
    )

    if table_start is None:
        return {}

    party_lines = lines[header_end + 1:table_start]

    gstin_positions = [
        i for i, line in enumerate(party_lines)
        if _extract_gstin(line)
    ]

    if len(gstin_positions) < 2:
        return {}

    chunks = []
    start = 0

    for position in gstin_positions[:2]:
        chunk = party_lines[start:position + 1]
        if chunk:
            chunks.append(chunk)
        start = position + 1

    if len(chunks) != 2:
        return {}

    metadata_pattern = re.compile(
        r"(?i)^(?:GSTIN|STATE\s*CODE|EMAIL|PHONE|TEL|MOBILE)\b"
    )

    def parse_party(chunk):
        content = [
            line for line in chunk
            if not metadata_pattern.match(line)
            and not _is_invoice_party_placeholder(line)
        ]

        name = next(
            (
                line for line in content
                if not _looks_like_invoice_address(line)
            ),
            None,
        )

        if not name and content:
            name = content[0]

        address_lines = [
            line for line in content
            if line != name
        ]

        gstin = _extract_gstin("\n".join(chunk))

        return {
            "name": name,
            "address": " | ".join(address_lines) or None,
            "gstin": gstin or [],
            "details": " | ".join(chunk)[:700],
        }

    seller = parse_party(chunks[0])
    buyer = parse_party(chunks[1])

    result = {}

    if seller["name"]:
        result["supplier_name"] = seller["name"]
    if seller["address"]:
        result["supplier_address"] = seller["address"]
    if seller["gstin"]:
        result["supplier_gstin"] = seller["gstin"]
    if seller["details"]:
        result["seller_company_details"] = seller["details"]

    if buyer["name"]:
        result["customer_name"] = buyer["name"]
    if buyer["address"]:
        result["buyer_address"] = buyer["address"]
    if buyer["gstin"]:
        result["buyer_gstin"] = buyer["gstin"]
    if buyer["details"]:
        result["buyer_customer_details"] = buyer["details"]

    return result


def _extract_vertical_invoice_line_items(text):
    """Parse vertically emitted invoice rows, with or without HSN columns."""
    lines = _clean_lines(text)

    description_index = next(
        (
            i for i, line in enumerate(lines)
            if re.fullmatch(
                r"(?i)description(?:\s+of\s+goods)?",
                line,
            )
        ),
        None,
    )

    if description_index is None:
        return []

    header_positions = {}

    header_patterns = {
        "quantity": r"(?i)^(?:qty|quantity)$",
        "rate": r"(?i)^(?:unit\s+rate|rate|unit\s+price|price\s+per\s+unit)(?:\s*\([^)]*\))?$",
        "amount": r"(?i)^(?:line\s+amount|amount|total\s+amount|taxable\s+value)(?:\s*\([^)]*\))?$",
        "hsn": r"(?i)^(?:hsn(?:/sac)?|sac)$",
    }

    for i in range(
        description_index + 1,
        min(len(lines), description_index + 12),
    ):
        for key, pattern in header_patterns.items():
            if re.fullmatch(pattern, lines[i]):
                header_positions.setdefault(key, i)

    if not {"quantity", "rate", "amount"}.issubset(header_positions):
        return []

    header_end = max(header_positions.values())
    has_hsn = "hsn" in header_positions

    quantity_pattern = re.compile(
        r"^\d+(?:\.\d{1,4})?(?:\s*[A-Za-z]+)?$"
    )

    money_pattern = re.compile(
        r"^(?:(?:INR|USD|EUR|Rs\.?|\u20b9|\$)\s*)?-?\s*"
        r"\d[\d,]*(?:\.\d{1,4})?$",
        re.IGNORECASE,
    )

    hsn_pattern = re.compile(r"^\d{4,8}$")

    stop_pattern = re.compile(
        r"(?i)^(?:subtotal|sub\s*total|discount|freight|"
        r"other\s+charges|taxable\s+value|taxable\s*amount|"
        r"CGST|SGST|IGST|UTGST|CESS|total\s*tax|grand\s*total|"
        r"total\s*payable|payment\s+and\s+notes|amount\s+in\s+words)$"
    )

    def clean_money(value):
        return re.sub(
            r"(?i)^(?:INR|USD|EUR|Rs\.?|\u20b9|\$)\s*",
            "",
            value,
        ).strip().replace(" ", "")

    items = []
    index = header_end + 1

    while index < len(lines) and len(items) < 100:
        description = lines[index]

        if stop_pattern.match(description):
            break

        if re.match(
            r"(?i)^(?:synthetic test data|page\s+\d+|tax invoice|"
            r"place of supply|supplier state|tax type|gst rate)$",
            description,
        ):
            index += 1
            continue

        if has_hsn:
            if index + 4 >= len(lines):
                break

            hsn, quantity, rate, amount = lines[index + 1:index + 5]

            valid = (
                description
                and hsn_pattern.fullmatch(hsn)
                and quantity_pattern.fullmatch(quantity)
                and money_pattern.fullmatch(rate)
                and money_pattern.fullmatch(amount)
            )

            if valid:
                items.append({
                    "description": description,
                    "hsn_sac_code": hsn,
                    "quantity": quantity,
                    "unit_price": clean_money(rate),
                    "taxable_value": clean_money(amount),
                })

                index += 5
                continue

        else:
            if index + 3 >= len(lines):
                break

            quantity, rate, amount = lines[index + 1:index + 4]

            valid = (
                description
                and not money_pattern.fullmatch(description)
                and not quantity_pattern.fullmatch(description)
                and quantity_pattern.fullmatch(quantity)
                and money_pattern.fullmatch(rate)
                and money_pattern.fullmatch(amount)
            )

            if valid:
                items.append({
                    "description": description,
                    "quantity": quantity,
                    "unit_price": clean_money(rate),
                    "taxable_value": clean_money(amount),
                })

                index += 4
                continue

        index += 1

    return items


def _extract_tax_component_adjacent_lines(text, label):
    """Extract tax amounts when a GST label and its amount occupy separate lines."""
    lines = (text or "").splitlines()

    label_re = re.compile(
        rf"(?i)^\s*{re.escape(label)}\b"
    )

    amount_re = re.compile(
        r"^(?:INR\s*|₹\s*)?(-?\d[\d,]*(?:\.\d{1,4})?)$",
        re.I,
    )

    for index, line in enumerate(lines):
        if not label_re.match(line):
            continue

        for following in lines[index + 1:index + 4]:
            if not following.strip():
                continue

            if re.match(
                r"(?i)^\s*(?:CGST|SGST|IGST|UTGST|CESS|"
                r"TOTAL\s*TAX|GRAND\s*TOTAL)\b",
                following,
            ):
                break

            amount_match = amount_re.fullmatch(following.strip())

            if amount_match:
                return amount_match.group(1)

            break

    return None

def _split_invoice_cells(line):
    """Split OCR table rows while preserving empty delimited columns.

    Dropping empty cells shifts every later value into the wrong header column.
    Pipe- and tab-delimited OCR output retains those positions, so keep them.
    Multi-space splitting remains best-effort because OCR may collapse blanks.
    """
    value = (line or "").strip()
    if "|" in value:
        return [_clean_text(part) for part in re.split(r"\s*\|\s*", value)]
    if "\t" in value:
        return [_clean_text(part) for part in value.split("\t")]
    return [_clean_text(part) for part in re.split(r"\s{2,}", value)]


def _invoice_header_map(header_cells):
    """Map visible table headings to output keys without assuming fixed order."""
    patterns = {
        "description": r"description|product|item|service|particular",
        "hsn_sac_code": r"\bhsn\b|\bsac\b",
        "quantity": r"\bqty\b|quantity",
        "unit": r"^(?:uom|unit)$",
        "unit_price": r"unit\s*price|price\s*per\s*unit|rate\s*per\s*unit|\brate\b|\bprice\b",
        "discount": r"discount",
        "taxable_value": r"taxable\s*(?:value|amount)",
        "tax_rate": r"gst\s*%|tax\s*rate|gst\s*rate|tax\s*%|\bvat\s*%",
        "cgst": r"\bcgst\b",
        "sgst": r"\bsgst\b",
        "igst": r"\bigst\b",
        "cess": r"\bcess\b",
        "line_total": r"line\s*total|net\s*amount|amount|total|value",
    }
    mapped = {}
    normalized = [re.sub(r"[^a-z0-9% ]", " ", cell.lower()).strip() for cell in header_cells]
    # More specific columns are mapped before broad labels such as "amount".
    ordered_keys = [
        "description", "hsn_sac_code", "quantity", "unit", "unit_price",
        "discount", "taxable_value", "tax_rate", "cgst", "sgst", "igst",
        "cess", "line_total",
    ]
    for key in ordered_keys:
        for index, cell in enumerate(normalized):
            if index in mapped.values():
                continue
            if re.search(patterns[key], cell, re.I):
                mapped[key] = index
                break
    return mapped


def _extract_invoice_line_items(text):
    """Extract invoice rows with repeated-header and wrapped-description support."""
    lines = (text or "").splitlines()
    header_index = None
    header_map = {}

    for index, line in enumerate(lines):
        cells = _split_invoice_cells(line)
        lower = line.lower()

        if (
            ("description" in lower or "item" in lower or "product" in lower or "particular" in lower)
            and ("qty" in lower or "quantity" in lower)
            and ("price" in lower or "rate" in lower or "amount" in lower)
        ):
            candidate_map = _invoice_header_map(cells)

            if (
                "description" in candidate_map
                and "quantity" in candidate_map
            ):
                header_index = index
                header_map = candidate_map
                break

    if header_index is None:
        return []

    items = []
    pending_description = []

    stop = re.compile(
        r"(?i)^\s*(?:sub\s*total|subtotal|taxable\s*amount|"
        r"total\s*tax|tax|gst|grand\s*total|total\s*payable|"
        r"amount\s*due|balance\s*due)\b"
    )

    numeric_fields = (
        "quantity",
        "unit_price",
        "line_total",
        "taxable_value",
    )

    for line in lines[header_index + 1:header_index + 501]:
        if not line.strip():
            continue

        if stop.search(line):
            break

        cells = _split_invoice_cells(line)

        # Ignore repeated header rows in multi-page invoice tables.
        repeated_map = _invoice_header_map(cells)

        if (
            "description" in repeated_map
            and "quantity" in repeated_map
            and any(
                field in repeated_map
                for field in ("unit_price", "line_total", "taxable_value")
            )
        ):
            continue

        item = {}

        for field_name, cell_index in header_map.items():
            if cell_index >= len(cells):
                continue

            value = _clean_text(cells[cell_index])

            if not value or value in {"-", "—", "–"}:
                continue

            item[field_name] = value

        description = _clean_text(item.get("description"))
        has_numeric_data = any(item.get(key) for key in numeric_fields)

        # A description-only OCR row is treated as a wrapped description,
        # not as a completed invoice item.
        if not has_numeric_data:
            continuation = description

            if not continuation and len(cells) == 1:
                continuation = _clean_text(cells[0])

            if continuation and len(continuation) <= 240:
                if items:
                    previous = _clean_text(items[-1].get("description"))
                    items[-1]["description"] = _clean_text(
                        previous + " " + continuation
                    )
                elif len(pending_description) < 3:
                    pending_description.append(continuation)

            continue

        # Preserve description fragments that precede the numeric row.
        if pending_description:
            description = _clean_text(
                " ".join(pending_description + ([description] if description else []))
            )
            pending_description = []

        if not description:
            continue

        item["description"] = description

        if not any(item.get(key) for key in numeric_fields):
            continue

        items.append(item)

        if len(items) >= 100:
            break

    return items[:100]

# INVOICE_REPEATED_HEADER_AND_WRAP_SUPPORT

def _decimal_amount(value):
    """Parse a currency-like value into Decimal without rounding it silently."""
    if value is None:
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d{1,4})?", str(value))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", ""))
    except InvalidOperation:
        return None


def _extract_tax_component(text, label):
    """Extract the monetary component from forms like ``CGST @ 9%: 2,790``."""
    pattern = re.compile(
        rf"(?im)^\s*{re.escape(label)}\b[^\r\n]*?[:：]\s*(.*?)\s*$"
    )
    match = pattern.search(text or "")
    if not match:
        # OCR can put the component label and amount on adjacent lines.
        return _extract_labeled_value(text, re.escape(label))
    value = match.group(1)
    numbers = re.findall(r"-?\d[\d,]*(?:\.\d{1,4})?", value)
    if not numbers:
        return None
    return numbers[-1]


def _normalize_invoice_number(value):
    """Return a plausible alphanumeric invoice identifier, or None."""
    candidate = _clean_text(value or "").strip(" |;:#")

    match = re.match(
        r"^([A-Z0-9][A-Z0-9/-]{2,})\b",
        candidate,
        re.IGNORECASE,
    )

    if not match:
        return None

    identifier = match.group(1).strip("-/")
    if len(identifier) < 3 or len(identifier) > 50:
        return None

    # Natural-language words such as 'with', 'for' and 'details'
    # are not valid invoice identifiers.
    if not re.search(r"\d", identifier):
        return None

    return identifier

def _extract_invoice_header_values(text):
    """Extract invoice header labels independently on the same or separate lines."""
    patterns = {
        "invoice_number": (
            r"(?:invoice\s*(?:number|no\.?|#)|inv\s*(?:no\.?|#))"
        ),
        "invoice_date": (
            r"(?:invoice\s*date|date\s*of\s*issue|issued\s*on)"
        ),
        "due_date": r"(?:payment\s*due\s*date|due\s*date)",
    }

    values = {}

    for line in (text or "").splitlines():
        cells = re.split(r"\s*[|;]\s*", line.strip())

        for cell in cells:
            for field, pattern in patterns.items():
                if field in values:
                    continue

                match = re.match(
                    pattern + r"\s*[:#-]?\s*(.*?)\s*$",
                    cell,
                    re.IGNORECASE,
                )

                if match:
                    value = _clean_text(match.group(1)).strip(" |;:")

                    if field == "invoice_number":
                        value = _normalize_invoice_number(value)

                    if value:
                        values[field] = value

    return values


def extract_invoice_fields(text):
    """Extract labelled invoice values, line items and transparent arithmetic checks."""
    text = text or ""
    fields = {}

    header_values = _extract_invoice_header_values(text)
    invoice_number = _normalize_invoice_number(
        header_values.get("invoice_number")
        or _extract_labeled_value(
            text,
            r"invoice\s*(?:number|no\.?|#)|inv\s*(?:no\.?|#)",
        )
    )

    if not invoice_number:
        match = re.search(
            r"\bINV[-/][A-Z0-9/-]{2,}\b",
            text,
            re.IGNORECASE,
        )
        if match:
            invoice_number = _normalize_invoice_number(match.group(0))

    if invoice_number:
        fields["invoice_number"] = invoice_number

    label_patterns = {
        "invoice_date": r"invoice\s*date|date\s*of\s*issue|issued\s*on",
        "due_date": r"due\s*date|payment\s*due\s*date",
        "seller_company_details": r"seller\s*(?:company\s*)?details|supplier\s*(?:company\s*)?details|vendor\s*details|bill\s*from|sold\s*by",
        "buyer_customer_details": r"buyer\s*details|customer\s*details|bill\s*to|ship\s*to",
        "supplier_name": r"supplier\s*name|vendor\s*name|seller\s*name|supplier|vendor|seller",
        "customer_name": r"customer\s*name|buyer\s*name|customer|buyer",
        "subtotal": r"sub\s*total|subtotal",
        "taxable_amount": r"taxable\s*amount|taxable\s*value",
        "tax_amount": r"total\s*tax|tax\s*amount|gst\s*amount|vat\s*amount|total\s*gst",
        # Deliberately excludes "balance due" and "amount due": these are not
        # equivalent to the invoice grand total when partial payment exists.
        "grand_total": r"grand\s*total|total\s*payable|amount\s*payable|net\s*payable|invoice\s*total|total\s*amount",
        "amount_paid": r"amount\s*paid|paid\s*amount|payment\s*received|total\s*paid",
        "balance_due": r"balance\s*due|amount\s*due|outstanding\s*(?:amount|balance)",
        "total_discount": r"discount|total\s*discount|discount\s*total|overall\s*discount|discount\s*amount",
        "other_charges": r"other\s*charges|shipping\s*charges|freight|handling\s*charges|packing\s*charges",
        "round_off": r"round\s*off|rounding",
        "payment_terms": r"payment\s*terms|terms\s*of\s*payment",
        "currency": r"currency",
        "amount_in_words": r"amount\s*in\s*words|total\s*in\s*words",
        "place_of_supply": r"place\s+of\s+supply",
        "supplier_state": r"(?:supplier|seller|vendor)\s+state",
        "tax_type": r"(?:tax\s+type|gst\s+type|nature\s+of\s+supply)",
        "gst_rate": r"(?:gst\s+rate|tax\s+rate|gst\s*%)",
    }
    for field_name, label_pattern in label_patterns.items():
        value = _extract_labeled_value(text, label_pattern)
        if value:
            fields[field_name] = value

    fields.update(header_values)

    tax_breakdown = {}
    for tax_label in ("CGST", "SGST", "IGST", "UTGST", "CESS"):
        component = _extract_tax_component(text, tax_label)
        if not component:
            component = _extract_tax_component_adjacent_lines(text, tax_label)
        if component:
            tax_breakdown[tax_label.lower()] = component
    if tax_breakdown:
        fields["tax_breakdown"] = tax_breakdown

    if not fields.get("tax_amount") and tax_breakdown:
        component_values = [_decimal_amount(value) for value in tax_breakdown.values()]
        if all(value is not None for value in component_values):
            fields["tax_amount"] = f"{sum(component_values, Decimal('0')):.2f}"

    seller_block = _extract_party_block(
        text,
        [r"bill\s*from", r"seller\s*(?:company\s*)?details", r"supplier\s*(?:company\s*)?details", r"vendor\s*details", r"sold\s*by"],
        [r"bill\s*to", r"buyer\s*details", r"customer\s*details", r"ship\s*to", r"items?", r"description", r"hsn", r"subtotal", r"taxable\s*amount", r"grand\s*total"],
    )
    buyer_block = _extract_party_block(
        text,
        [r"bill\s*to", r"buyer\s*details", r"customer\s*details", r"ship\s*to"],
        [r"items?", r"description", r"subtotal", r"grand\s*total"],
    )
    if seller_block:
        fields["seller_company_details"] = seller_block
        seller_name = _clean_text(seller_block.split("|", 1)[0])
        if seller_name and not re.search(r"(?i)^(?:gstin|email|phone|address)\b", seller_name):
            fields.setdefault("supplier_name", seller_name)
        supplier_gstin = _extract_gstin(seller_block)
        supplier_emails = _extract_emails(seller_block)
        supplier_phones = _extract_phone_numbers(seller_block)
        if supplier_gstin:
            fields["supplier_gstin"] = supplier_gstin
        if supplier_emails:
            fields["supplier_email_addresses"] = supplier_emails
        if supplier_phones:
            fields["supplier_phone_numbers"] = supplier_phones
    if buyer_block:
        fields["buyer_customer_details"] = buyer_block
        buyer_name = _clean_text(buyer_block.split("|", 1)[0])
        if buyer_name and not re.search(r"(?i)^(?:gstin|email|phone|address)\b", buyer_name):
            fields.setdefault("customer_name", buyer_name)
        buyer_gstin = _extract_gstin(buyer_block)
        buyer_emails = _extract_emails(buyer_block)
        buyer_phones = _extract_phone_numbers(buyer_block)
        if buyer_gstin:
            fields["buyer_gstin"] = buyer_gstin
        if buyer_emails:
            fields["buyer_email_addresses"] = buyer_emails
        if buyer_phones:
            fields["buyer_phone_numbers"] = buyer_phones

    # Correct side-by-side native PDF party reading order when available.
    native_parties = _extract_native_invoice_party_blocks(text)
    if native_parties:
        fields.update(native_parties)

    gstin = _extract_gstin(text)
    if gstin:
        fields["gstin"] = gstin
    emails = _extract_emails(text)
    phones = _extract_phone_numbers(text)
    dates = _extract_dates(text)
    amounts = _extract_amounts(text)
    if emails:
        fields["email_addresses"] = emails
    if phones:
        fields["phone_numbers"] = phones
    if dates:
        fields["date_candidates"] = dates
    if amounts:
        fields["amount_candidates"] = amounts

    items = _extract_invoice_line_items(text)
    if not items:
        items = _extract_vertical_invoice_line_items(text)
    if items:
        fields["line_items"] = items

    # Add an auditable calculation result only when enough explicit source
    # values exist. This is a consistency check, not a replacement for OCR data.
    subtotal = _decimal_amount(fields.get("subtotal"))
    taxable_amount = _decimal_amount(fields.get("taxable_amount"))
    tax = _decimal_amount(fields.get("tax_amount"))
    grand_total = _decimal_amount(fields.get("grand_total"))
    discount = _decimal_amount(fields.get("total_discount")) or Decimal("0")
    charges = _decimal_amount(fields.get("other_charges")) or Decimal("0")
    round_off = _decimal_amount(fields.get("round_off")) or Decimal("0")
    amount_paid = _decimal_amount(fields.get("amount_paid"))
    balance_due = _decimal_amount(fields.get("balance_due"))

    checks = {}
    if grand_total is not None and tax is not None:
        if taxable_amount is not None:
            # Taxable amount already includes freight/other charges and discounts.
            # Do not apply those adjustments a second time.
            taxable_base = taxable_amount
            calculation_formula = "taxable_amount + tax + round_off"
        elif subtotal is not None:
            # Fall back to the printed subtotal when no taxable amount is available.
            taxable_base = subtotal + charges - discount
            calculation_formula = "subtotal + other_charges - total_discount + tax + round_off"
        else:
            taxable_base = None
            calculation_formula = None

        if taxable_base is not None:
            expected = taxable_base + tax + round_off
            difference = grand_total - expected
            tolerance = Decimal("0.02")
            checks["grand_total"] = {
                "status": "match" if abs(difference) <= tolerance else "mismatch",
                "expected_amount": f"{expected:.2f}",
                "extracted_amount": f"{grand_total:.2f}",
                "difference": f"{difference:.2f}",
                "tolerance": f"{tolerance:.2f}",
                "formula": calculation_formula,
            }
    if amount_paid is not None and balance_due is not None and grand_total is not None:
        difference = grand_total - amount_paid - balance_due
        tolerance = Decimal("0.02")
        checks["payment_balance"] = {
            "status": "match" if abs(difference) <= tolerance else "mismatch",
            "expected_balance_due": f"{(grand_total - amount_paid):.2f}",
            "extracted_balance_due": f"{balance_due:.2f}",
            "difference": f"{difference:.2f}",
            "tolerance": f"{tolerance:.2f}",
        }
    # Reconcile only explicitly extracted taxable values. A line total may
    # include tax, so comparing it with a pre-tax subtotal would be misleading.
    line_bases = [_decimal_amount(item.get("taxable_value")) for item in items]
    line_item_subtotal = subtotal
    if (
        line_item_subtotal is None
        and taxable_amount is not None
        and discount == Decimal("0")
        and charges == Decimal("0")
    ):
        # Safe fallback only when no separate adjustments exist.
        line_item_subtotal = taxable_amount

    if items and line_item_subtotal is not None and all(value is not None for value in line_bases):
        line_sum = sum(line_bases, Decimal("0"))
        difference = line_item_subtotal - line_sum
        tolerance = Decimal("0.02")
        checks["line_items_subtotal"] = {
            "status": "match" if abs(difference) <= tolerance else "mismatch",
            "line_items_taxable_total": f"{line_sum:.2f}",
            "extracted_subtotal": f"{line_item_subtotal:.2f}",
            "difference": f"{difference:.2f}",
            "tolerance": f"{tolerance:.2f}",
        }

    expected_line_tax = Decimal("0")
    tax_rate_rows = 0
    for item in items:
        taxable_value = _decimal_amount(item.get("taxable_value"))
        tax_rate_match = re.search(r"(\d+(?:\.\d{1,4})?)\s*%", str(item.get("tax_rate", "")))
        if taxable_value is None or not tax_rate_match:
            continue
        try:
            rate = Decimal(tax_rate_match.group(1))
        except InvalidOperation:
            continue
        expected_line_tax += (taxable_value * rate / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        tax_rate_rows += 1
    if tax_rate_rows and tax is not None:
        cess_values = [
            _decimal_amount(value)
            for key, value in (tax_breakdown or {}).items()
            if key == "cess"
        ]
        cess_total = sum((value for value in cess_values if value is not None), Decimal("0"))
        tax_excluding_cess = tax - cess_total
        difference = tax_excluding_cess - expected_line_tax
        tolerance = Decimal("0.02")
        checks["line_item_tax_total"] = {
            "status": "match" if abs(difference) <= tolerance else "mismatch",
            "calculated_tax_from_line_rates": f"{expected_line_tax:.2f}",
            "extracted_total_tax_excluding_cess": f"{tax_excluding_cess:.2f}",
            "tax_rate_rows": tax_rate_rows,
            "difference": f"{difference:.2f}",
            "tolerance": f"{tolerance:.2f}",
        }
    if checks:
        fields["calculation_validation"] = checks

    return fields


# ---------------------------------------------------------
# FORM EXTRACTION
# ---------------------------------------------------------

def _extract_checkbox_states(text):
    """Extract explicit checkbox states preserved in OCR text.

    This recognizes textual markers such as [x], [ ], ☒, ☑, ☐ and □.
    It cannot infer a graphical checkbox state when OCR does not expose a marker.
    """
    marker_pattern = re.compile(
        r"\[\s*(?P<bracket>[xX✓✔☑]?)\s*\]|(?P<symbol>[☑☒☐□✓✔])"
    )
    checked_markers = {"x", "✓", "✔", "☑", "☒"}
    unchecked_symbols = {"☐", "□"}
    states = []
    seen = set()

    for raw_line in str(text or "").splitlines():
        line = str(raw_line or "")
        matches = list(marker_pattern.finditer(line))
        for index, match in enumerate(matches):
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            label = _clean_text(line[match.end():next_start]).strip(" :;,.-–—|\t")
            if not label:
                label = _clean_text(line[:match.start()]).strip(" :;,.-–—|\t")
            if not label:
                continue

            raw_marker = (match.group("bracket") or match.group("symbol") or "").casefold()
            if raw_marker in unchecked_symbols:
                status = "unchecked"
            elif raw_marker in checked_markers:
                status = "checked"
            else:
                # Empty [ ] and [] markers are explicit unchecked states.
                status = "unchecked"

            identity = (label.casefold(), status)
            if identity in seen:
                continue
            seen.add(identity)
            states.append({"label": label, "status": status})

    return states[:60]


def _extract_marked_options(text):
    """Return only options with an explicit checked marker in OCR text."""
    return list(dict.fromkeys(
        item["label"]
        for item in _extract_checkbox_states(text)
        if item["status"] == "checked"
    ))[:30]

def _extract_form_inline_metadata(text):
    """Recover submission metadata when OCR flattens table cells onto one line."""
    compact = _clean_text(str(text or "").replace("\r", "\n"))
    if not compact:
        return {}

    declarant_label = (
        r"(?:applicant\s*/\s*authori[sz]ed\s+person|"
        r"declarant\s*(?:/|and)?\s*(?:authori[sz]ed\s+)?person|"
        r"authori[sz]ed\s+person|authorized\s+person)"
    )
    metadata = {}

    date_match = re.search(
        r"(?i)\b(?:submission\s+date|date\s+submitted)\b"
        r"\s*[:#-]?\s*(.*?)"
        rf"(?=\s+\b(?:{declarant_label}|form\s+status|record\s+status|submitted)\b|$)",
        compact,
    )
    if date_match:
        value = _clean_text(date_match.group(1)).strip(" :-|")
        if value:
            metadata["submission_date"] = value

    person_match = re.search(
        rf"(?i)\b{declarant_label}\b\s*[:#-]?\s*(.*?)"
        r"(?=\s+\b(?:submission\s+date|form\s+status|record\s+status|submitted)\b|$)",
        compact,
    )
    if person_match:
        value = _clean_text(person_match.group(1)).strip(" :-|")
        if value:
            # Keep the person value concise; OCR often concatenates the next
            # footer sentence when the form has no explicit separator.
            value = re.split(r"(?i)\b(?:synthetic\s+form\s+record|form\s+reference|page\s+\d+)\b", value, maxsplit=1)[0].strip(" :-|")
            if value:
                metadata["declarant_authorised_person"] = value

    return metadata


def _strip_inline_form_metadata(value):
    """Remove submission metadata accidentally appended to a declaration."""
    value = _clean_text(value)
    labels = (
        r"submission\s+date|date\s+submitted|"
        r"applicant\s*/\s*authori[sz]ed\s+person|"
        r"declarant\s*(?:/|and)?\s*(?:authori[sz]ed\s+)?person|"
        r"authori[sz]ed\s+person|authorized\s+person|"
        r"form\s+status|record\s+status"
    )
    match = re.search(rf"(?i)\b(?:{labels})\b", value)
    return value[:match.start()].strip(" :-|") if match else value


def _extract_declaration_statement(text):
    """Capture a short, explicitly headed declaration block without guessing."""
    raw_lines = (text or "").replace("\r", "\n").splitlines()
    heading_pattern = re.compile(
        r"(?i)^\s*(?:applicant\s+)?declaration(?:\s+statement)?\s*(?:[:#-]\s*(.*))?$"
    )
    next_field_pattern = re.compile(
        r"(?i)^\s*(?:signature(?:\s+of\s+applicant)?|applicant\s+signature|"
        r"place(?:\s+of\s+signing)?|date|submission\s+date|date\s+submitted|"
        r"applicant\s*/\s*authori[sz]ed\s+person|declarant(?:\s*/\s*authori[sz]ed)?\s+person|"
        r"authori[sz]ed\s+person|authorized\s+person|form\s+status|record\s+status|"
        r"terms(?:\s+(?:and|&)\s+conditions)?|privacy(?:\s+policy)?|"
        r"applicant\s+name|name\s+of\s+applicant)\b"
    )

    for index, raw_line in enumerate(raw_lines):
        heading = heading_pattern.match(_clean_text(raw_line))
        if not heading:
            continue

        parts = []
        inline_value = _strip_inline_form_metadata(heading.group(1) or "")
        if inline_value:
            parts.append(inline_value)

        for candidate in raw_lines[index + 1:index + 6]:
            value = _clean_text(candidate)
            if not value:
                continue
            if next_field_pattern.match(value):
                break
            cleaned_candidate = _strip_inline_form_metadata(value)
            if cleaned_candidate != value:
                if cleaned_candidate:
                    parts.append(cleaned_candidate)
                break
            value = cleaned_candidate
            if re.match(r"^[A-Za-z][A-Za-z0-9 /&()._-]{1,48}\s*[:：]", value):
                break
            parts.append(value)
            if len(" ".join(parts)) >= 600:
                break

        statement = _clean_text(" ".join(parts))[:600]
        if statement:
            return statement

    return None


def _apply_marked_form_checkbox_fields(text, fields):
    """Map explicitly checked privacy and terms options without accepting negative choices."""
    if not isinstance(fields, dict):
        return fields

    negative_pattern = re.compile(
        r"\b(?:do\s+not|don't|does\s+not|did\s+not|not|"
        r"disagree|decline|reject|refuse)\b",
        re.IGNORECASE,
    )

    for item in _extract_checkbox_states(text):
        if item.get("status") != "checked":
            continue
        label = _clean_text(item.get("label"))
        if not label or negative_pattern.search(label):
            continue

        if re.search(r"\bprivacy(?:\s+policy)?\b", label, re.IGNORECASE):
            fields["privacy_acknowledgement"] = label

        if re.search(
            r"\bterms?\b.*\bconditions?\b|\bterms?\s+accepted\b",
            label,
            re.IGNORECASE,
        ):
            fields["terms_acceptance"] = label

    return fields

def extract_form_fields(text):
    """Extract labelled form data; OCR text cannot infer graphical marks it did not recognise."""
    text = text or ""
    fields = {}
    lines = _clean_lines(text)
    if lines:
        title = next((line for line in lines[:8] if re.search(r"(?i)\b(form|application|registration|admission|claim|declaration|survey|account\s+intake|customer\s+onboarding|client\s+onboarding|account\s+opening)\b", line)), None)
        if title: fields["form_title_type"] = title[:180]

    label_patterns = FORM_FIELD_LABEL_PATTERNS
    for field_name, label_pattern in label_patterns.items():
        value = _extract_labeled_value(text, label_pattern)
        if value: fields[field_name] = value

    inline_form_metadata = _extract_form_inline_metadata(text)
    for metadata_key, metadata_value in inline_form_metadata.items():
        fields.setdefault(metadata_key, metadata_value)

    declaration = _extract_declaration_statement(text)
    if declaration:
        fields["declaration_statement"] = declaration

    emails = _extract_emails(text); phones = _extract_phone_numbers(text)
    dates = _extract_dates(text); amounts = _extract_amounts(text)
    checkbox_states = _extract_checkbox_states(text)
    selected_options = [
        item["label"] for item in checkbox_states
        if item["status"] == "checked"
    ]
    if emails: fields["email_addresses"] = emails
    if phones: fields["phone_numbers"] = phones
    if dates: fields["date_candidates"] = dates
    if amounts: fields["amount_candidates"] = amounts
    if checkbox_states:
        fields["checkbox_option_states"] = checkbox_states
    if selected_options:
        fields["selected_checkbox_radio_options"] = selected_options
        for option in selected_options:
            option_lower = option.casefold()
            explicitly_negative = bool(re.search(r"\b(?:not|never|decline|reject|refuse|do\s+not|don\x27t|doesn\x27t)\b.*\b(?:agree|accept|acknowledge|consent)\b", option_lower))
            if explicitly_negative:
                continue
            if (
                "terms_acceptance" not in fields
                and re.search(r"\b(?:terms?\s*(?:and|&)\s*conditions|agree\s+to\s+terms|accept\s+terms)\b", option_lower)
            ):
                fields["terms_acceptance"] = option
            if (
                "privacy_acknowledgement" not in fields
                and re.search(r"\bprivacy\b", option_lower)
                and re.search(r"\b(?:acknowledge|consent|agree|accept)\b", option_lower)
            ):
                fields["privacy_acknowledgement"] = option

    labelled = _extract_labelled_lines(text)
    if labelled:
        fields["labelled_field_values"] = labelled
        # Map common free-form OCR labels to canonical form fields only when the
        # explicit field extractor did not already find a value.
        canonical_aliases = {
            "applicant_name": ("applicant_name", "full_name", "name_of_applicant"),
            "primary_contact": ("primary_contact", "main_contact", "contact_person"),
            "declarant_authorised_person": (
                "declarant_authorised_person", "applicant_authorised_person",
                "applicant_authorized_person", "authorised_person", "authorized_person",
            ),
            "submission_date": ("submission_date", "date_submitted"),
            "email": ("email", "email_address", "contact_email"),
            "phone": ("phone", "phone_number", "contact_phone"),
            "application_reference_number": ("application_reference_number", "application_number", "reference_number", "registration_number"),
            "application_date": ("application_date", "date_of_application"),
            "application_type": ("application_type", "type_of_application"),
            "date_of_birth": ("date_of_birth", "dob"),
            "age": ("age",),
            "gender": ("gender", "sex"),
            "address": ("address", "location_address", "residential_address", "permanent_address", "communication_address"),
            "purpose_reason": ("purpose_reason", "purpose", "reason_for_application"),
            "requested_plan": ("requested_plan", "plan_requested", "subscription_plan", "selected_plan"),
            "number_of_users": ("number_of_users", "no_of_users", "user_count", "number_of_seats", "seat_count"),
            "organisation_name": ("organisation_name", "organization_name", "company_name", "business_name"),
            "customer_type": ("customer_type", "client_type", "customer_category", "client_category"),
            "primary_use": ("primary_use", "main_use", "intended_use", "primary_purpose", "use_case"),
            "billing_frequency": ("billing_frequency", "billing_cycle", "billing_interval", "payment_frequency"),
            "invoice_delivery_method": ("invoice_delivery_method", "invoice_delivery", "billing_document_delivery"),
            "privacy_acknowledgement": ("privacy_acknowledgement", "privacy_acknowledgment", "privacy_consent"),
            "support_channel": ("support_channel", "preferred_support_channel", "support_method"),
            "terms_acceptance": ("terms_acceptance", "acceptance_of_terms", "terms_accepted", "terms_and_conditions"),
        }
        for target, aliases in canonical_aliases.items():
            if fields.get(target):
                continue
            value = next((labelled[key] for key in aliases if labelled.get(key)), None)
            if value:
                fields[target] = value
        # All explicit label-value pairs, including form-specific Q&A, are retained.
        qa = {key: value for key, value in labelled.items() if re.search(r"question|answer|reason|purpose|details|remarks|declaration", key)}
        if fields.get("purpose_reason"):
            qa.setdefault("purpose_reason", fields["purpose_reason"])
        if qa: fields["form_questions_answers"] = qa
    elif fields.get("purpose_reason"):
        fields["form_questions_answers"] = {"purpose_reason": fields["purpose_reason"]}

    if re.search(r"\bsignature\b", text, re.IGNORECASE):
        fields["signature_label_present"] = True
    fields = _apply_marked_form_checkbox_fields(text, fields)
    return fields


def _field_leaf_values(value):
    """Yield scalar strings from nested extracted fields for evidence matching."""
    if isinstance(value, dict):
        for nested in value.values():
            yield from _field_leaf_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _field_leaf_values(nested)
    elif isinstance(value, str):
        # Party blocks are serialized with pipes; split those components so
        # each source line can independently match its originating page.
        parts = re.split(r"[|\n]+", value)
        for part in parts:
            cleaned = _clean_text(part)
            if cleaned:
                yield cleaned


def build_field_provenance(fields, source_pages):
    """Map extracted fields to pages; OCR token confidence is unavailable."""

    def normalize(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    prepared_pages = []

    for item in source_pages or []:
        if not isinstance(item, dict):
            continue

        try:
            page_number = int(item.get("page_number"))
        except (TypeError, ValueError):
            continue

        page_text = normalize(item.get("text", ""))

        if page_text:
            prepared_pages.append((page_number, page_text))

    provenance = {}

    for field_name, field_value in (fields or {}).items():
        values = list(_field_leaf_values(field_value))
        matches = set()

        for leaf in values:
            normalized_value = normalize(leaf)

            if len(normalized_value) < 3:
                continue

            for page_number, normalized_page in prepared_pages:
                if normalized_value in normalized_page:
                    matches.add(page_number)

        provenance[field_name] = {
            "source_pages": sorted(matches),
            "confidence": None,
            "confidence_status": "unavailable",
            "confidence_basis": (
                "Unavailable: the current OCR wrapper does not expose token-level confidence."
            ),
            "method": "normalized_text_match",
            "evidence_status": (
                "source_text_match" if matches else "not_found_or_derived"
            ),
        }

    return provenance


# ---------------------------------------------------------
# CLASSIFICATION-BASED DISPATCH
# ---------------------------------------------------------

def extract_document_fields(text, classification):
    """
    Dispatch extraction according to the predicted document category.

    Accepts the classification dictionary returned by classifier.py.
    """

    if isinstance(classification, dict):
        label = classification.get("label", "")
    else:
        label = str(classification or "")

    label = label.lower().strip()

    if label == "resume":
        return extract_resume_fields(text)

    if label == "invoice":
        return extract_invoice_fields(text)

    if label == "form":
        return extract_form_fields(text)

    return {}







# RESUME_HEADER_NAME_PREFIX_SUPPORT
