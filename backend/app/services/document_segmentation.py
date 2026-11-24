"""Page-level grouping for mixed multi-document PDF uploads.

The segmentation output is heuristic and must be reviewed for adjacent
documents of the same class or documents sharing a page.
"""
import re

def _document_identifier(text, label):
    """Return a typed, text-supported identity hint for same-class boundaries.

    Identifiers are intentionally conservative. A type prefix prevents a weak
    fallback (for example, a name) from being compared with a stronger invoice
    number found on another page.
    """
    value = text or ""
    if label == "invoice":
        match = re.search(
            r"(?im)^\s*(?:invoice\s*(?:number|no\.?|#)|inv\s*(?:no\.?|#))\s*[:#-]?\s*([A-Z0-9][A-Z0-9/-]{2,})",
            value,
        )
        if match:
            return ("invoice_number", match.group(1).strip().upper())
        date_match = re.search(
            r"(?im)^\s*(?:invoice\s*date|date\s*of\s*issue)\s*[:#-]\s*([^\r\n]+)",
            value,
        )
        party_match = re.search(
            r"(?im)^\s*(?:bill\s*from|supplier(?:\s*name)?|vendor(?:\s*name)?|seller(?:\s*name)?)\s*[:#-]\s*([^\r\n]+)",
            value,
        )
        if date_match and party_match:
            identity = f"{party_match.group(1).strip().casefold()}|{date_match.group(1).strip().casefold()}"
            return ("invoice_party_date", identity)
        return None

    if label == "form":
        # Accept inline references such as:
        # Customer onboarding | Form reference: AIN-2026-001
        reference_patterns = (
            r"\bform\s+reference(?:\s+(?:number|no\.?|id))?\s*[:#-]\s*([A-Z0-9][A-Z0-9/_-]{2,})",
            r"\b(?:application|registration)\s+(?:reference\s*)?(?:number|no\.?|id)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
            r"(?im)^\s*reference\s*(?:number|no\.?|id)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{2,})",
        )

        for pattern in reference_patterns:
            reference = re.search(pattern, value, re.I | re.M)
            if reference:
                return ("form_reference", reference.group(1).strip().upper())

        def labelled_value(pattern):
            match = re.search(pattern, value, re.I | re.M)

            if not match:
                return None

            same_line = (match.group(1) or "").strip(" :|\t-")

            if same_line:
                return same_line

            # Support labels and values appearing on different lines.
            following_labels = re.compile(
                r"^(?:customer\s+type|primary\s+contact|applicant\s+name|"
                r"full\s+name|contact\s+email|email|contact\s+phone|phone|"
                r"requested\s+plan|number\s+of\s+users|primary\s+use|"
                r"billing\s+frequency|invoice\s+delivery(?:\s+method)?|"
                r"support\s+channel|privacy\s+acknowledg(?:e)?ment|"
                r"terms\s+acceptance|declaration|submission\s+date)\b",
                re.I,
            )

            for line in value[match.end():].splitlines()[:5]:
                candidate = line.strip(" :|\t-")

                if not candidate:
                    continue

                if following_labels.match(candidate):
                    return None

                return candidate

            return None

        organisation = labelled_value(
            r"(?im)^\s*(?:organisation|organization|company|business)\s*name\s*[:#-]?\s*([^\r\n]*)"
        )

        customer = labelled_value(
            r"(?im)^\s*(?:customer|client)\s*name\s*[:#-]?\s*([^\r\n]*)"
        )

        if organisation or customer:
            identity_value = (organisation or customer).strip().casefold()
            return ("form_organisation", identity_value)

        applicant = labelled_value(
            r"(?im)^\s*(?:applicant\s*name|full\s*name|name\s*of\s*applicant|primary\s*contact)\s*[:#-]?\s*([^\r\n]*)"
        )

        if applicant:
            return ("form_applicant", applicant.strip().casefold())

        email = re.search(
            r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
            value,
            re.I,
        )

        if email:
            return ("form_email", email.group(0).strip().lower())

        return None

    if label == "resume":
        email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value, re.I)
        if email:
            return ("resume_email", email.group(0).strip().lower())
        excluded = {
            "resume", "curriculum vitae", "professional summary", "summary",
            "education", "experience", "work experience", "technical skills",
            "skills", "projects", "certifications", "profile", "personal details",
        }
        for line in value.splitlines()[:12]:
            candidate = re.sub(r"^\s*[•●▪■\-*]+\s*", "", line).strip(" :|-\t")
            if not candidate or candidate.lower() in excluded or re.search(r"\d|@|://", candidate):
                continue
            if candidate.isupper() and len(candidate.split()) >= 3:
                continue
            if 2 <= len(candidate.split()) <= 5 and re.fullmatch(r"[A-Za-z][A-Za-z.'’-]*(?:\s+[A-Za-z][A-Za-z.'’-]*){1,4}", candidate):
                return ("resume_name", candidate.casefold())
        return None
    return None


def _identity_changed(current, previous):
    if not current or not previous:
        return False
    return current[0] == previous[0] and current[1] != previous[1]


def _safe_confidence(prediction):
    if not isinstance(prediction, dict):
        return 0.0
    raw_value = prediction.get("confidence", 0.0)
    if isinstance(raw_value, bool):
        return 0.0
    try:
        value = float(raw_value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return value if 0.0 <= value <= 1.0 else 0.0


def _safe_score(value):
    if isinstance(value, bool):
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return score if 0.0 <= score <= 1.0 else 0.0


def _is_new_document_start(text, label, previous_text=""):
    """Require a document-start marker and a changed, comparable identity hint."""
    value = (text or "")[:5000].lower()
    if label == "invoice":
        marker = bool(
            re.search(r"\b(tax invoice|invoice)\b", value)
            and re.search(r"\b(invoice (?:no|number)|invoice date|bill to|ship to|supplier details|bill from)\b", value)
        )
    elif label == "form":
        marker = bool(
            re.search(r"\b(application form|registration form|admission form|application for|account intake(?: form)?|customer onboarding(?: form)?|client onboarding(?: form)?|account opening(?: form)?)\b", value)
            and re.search(r"\b(name|date of birth|applicant|signature|requested plan|number of users|organisation name|organization name|company name|customer type|billing frequency)\b", value)
        )
    elif label == "resume":
        # Recognise equivalent headings used by different resume templates.
        resume_heading_aliases = {
            "profile summary",
            "professional summary",
            "career objective",
            "core competencies",
            "technical skills",
            "key skills",
            "skills and competencies",
            "skills & competencies",
            "work experience",
            "professional experience",
            "employment history",
            "academic qualifications",
            "educational qualifications",
            "education",
            "academic background",
            "projects",
            "personal projects",
            "academic projects",
            "certifications",
            "professional certifications",
            "volunteer experience",
            "achievements",
            "languages",
        }

        detected_headings = set()

        for line in value.splitlines():
            heading = re.sub(
                r"^\s*(?:\d+[.) :\-]*|[•●▪■\-*]+)",
                "",
                line,
            )
            heading = heading.strip(" :|\t").casefold()

            if heading in resume_heading_aliases:
                detected_headings.add(heading)

        section_hits = len(detected_headings)
        marker = section_hits >= 2
    else:
        return False

    if not marker:
        return False
    return _identity_changed(
        _document_identifier(text, label),
        _document_identifier(previous_text, label),
    )



_TEXT_EVIDENCE_PATTERNS = {
    "form": [
        ("form_title", r"\b(?:(?:general\s+)?(?:application|registration|admission|claim|declaration|survey)\s+form|account\s+intake(?:\s+form)?|customer\s+onboarding(?:\s+form)?|client\s+onboarding(?:\s+form)?|account\s+opening(?:\s+form)?)\b"),
        ("application_reference", r"\bapplication\s+(?:reference\s+)?(?:number|no\.?|id)\b|\bregistration\s+(?:number|no\.?|id)\b"),
        ("application_date", r"\bapplication\s+date\b|\bdate\s+of\s+application\b|\bsubmission\s+date\b"),
        ("application_type", r"\bapplication\s+type\b|\btype\s+of\s+application\b"),
        ("applicant_identity", r"\bapplicant\s+(?:name|details|personal\s+information)\b|\bname\s+of\s+applicant\b"),
        ("personal_identity_fields", r"\bdate\s+of\s+birth\b|\bdob\b|\bgender\b|\bmarital\s+status\b"),
        ("form_declaration", r"\bdeclaration\b|\bI\s+hereby\s+declare\b"),
        ("form_signature", r"\bapplicant'?s?\s+signature\b|\bsignature\s+of\s+applicant\b"),
        ("form_selection", r"\bcheckbox(?:es)?\b|\bselect\s+one\b|\bselect\s+all\s+that\s+apply\b"),
        ("form_question", r"\bpurpose\s*/?\s*reason\b|\breason\s+for\s+application\b|\bquestion\s+\d+\b"),
        ("requested_plan", r"\brequested\s+plan\b|\bsubscription\s+plan\b|\bselected\s+plan\b"),
        ("user_count", r"\bnumber\s+of\s+users\b|\buser\s+count\b|\bnumber\s+of\s+seats\b"),
        ("organisation_identity", r"\borganisation\s+name\b|\borganization\s+name\b|\bcompany\s+name\b|\bbusiness\s+name\b"),
        ("customer_type", r"\bcustomer\s+type\b|\bclient\s+type\b"),
        ("primary_use", r"\bprimary\s+use\b|\bintended\s+use\b|\buse\s+case\b"),
        ("billing_frequency", r"\bbilling\s+(?:frequency|cycle|interval)\b|\bpayment\s+frequency\b"),
        ("invoice_delivery", r"\binvoice\s+delivery(?:\s+method)?\b"),
        ("privacy_acknowledgement", r"\bprivacy\s+acknowledg(?:e)?ment\b|\bprivacy\s+consent\b"),
        ("support_channel", r"\b(?:preferred\s+)?support\s+channel\b"),
        ("terms_acceptance", r"\bterms\s+acceptance\b|\bterms\s+(?:and|&)\s+conditions\b|\bagree\s+to\s+terms\b"),
    ],
    "invoice": [
        ("invoice_title", r"\b(?:tax\s+)?invoice\b"),
        ("invoice_identifier", r"\binvoice\s*(?:number|no\.?|#)\b|\binv\s*(?:no\.?|#)\b"),
        ("invoice_date", r"\binvoice\s+date\b|\bdate\s+of\s+issue\b"),
        ("invoice_parties", r"\bbill\s+to\b|\bbill\s+from\b|\bsupplier\s+details\b|\bbuyer\s+details\b"),
        ("invoice_tax", r"\b(?:cgst|sgst|igst|gstin|taxable\s+amount|tax\s+amount)\b"),
        ("invoice_totals", r"\b(?:subtotal|sub\s+total|grand\s+total|total\s+payable|amount\s+payable)\b"),
        ("invoice_lines", r"\b(?:hsn\s*/?\s*sac|quantity|qty|unit\s+price|line\s+total)\b"),
        ("invoice_payment", r"\bpayment\s+terms\b|\bbalance\s+due\b|\bamount\s+paid\b"),
    ],
    "resume": [
        ("resume_title", r"\b(?:resume|curriculum\s+vitae|cv)\b"),
        ("resume_summary", r"\b(?:professional|career|profile)\s+summary\b|\bcareer\s+objective\b"),
        ("resume_experience", r"\b(?:work|professional|employment)\s+experience\b|\bemployment\s+history\b"),
        ("resume_education", r"\beducation(?:al)?\s+(?:qualification|details|background)?\b|\bacademic\s+qualification"),
        ("resume_skills", r"\b(?:technical|core|key)\s+skills\b|\bskills\s*&?\s*competenc"),
        ("resume_projects", r"\b(?:academic|personal)?\s*projects\b"),
        ("resume_certifications", r"\bcertifications?\b|\blicenses?\b"),
        ("resume_achievements", r"\bachievements?\b|\bawards?\b"),
        ("resume_languages", r"\blanguages?\b|\bpublications?\b"),
    ],
}


def classify_text_structure(text):
    """Return a conservative document class only when multiple structural cues agree.

    A single keyword is never enough to override a visual model. Application forms
    require a form heading plus at least two form-field anchors, or four independent
    form-field anchors. Similar safeguards are used for invoices and resumes.
    """
    value = (text or "").casefold()
    evidence = {}
    scores = {}
    for label, patterns in _TEXT_EVIDENCE_PATTERNS.items():
        hits = [name for name, pattern in patterns if re.search(pattern, value, re.I)]
        evidence[label] = hits
        scores[label] = len(hits)

    strong = {}
    if "form_title" in evidence["form"] and scores["form"] >= 3:
        strong["form"] = scores["form"]
    elif scores["form"] >= 4 and (
        "application_reference" in evidence["form"]
        or "applicant_identity" in evidence["form"]
        or "organisation_identity" in evidence["form"]
        or "requested_plan" in evidence["form"]
    ):
        strong["form"] = scores["form"]

    if "invoice_title" in evidence["invoice"] and scores["invoice"] >= 3:
        strong["invoice"] = scores["invoice"]
    elif scores["invoice"] >= 4 and (
        "invoice_identifier" in evidence["invoice"]
        or "invoice_totals" in evidence["invoice"]
    ):
        strong["invoice"] = scores["invoice"]

    resume_section_hits = scores["resume"] - int("resume_title" in evidence["resume"])
    if "resume_title" in evidence["resume"] and resume_section_hits >= 2:
        strong["resume"] = scores["resume"]
    elif resume_section_hits >= 4:
        strong["resume"] = scores["resume"]

    if not strong:
        return None, {"scores": scores, "evidence": evidence, "strength": "insufficient"}

    ranked = sorted(strong.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 2:
        return None, {"scores": scores, "evidence": evidence, "strength": "ambiguous"}

    return ranked[0][0], {
        "scores": scores,
        "evidence": evidence,
        "strength": "strong",
        "matched_cues": evidence[ranked[0][0]],
    }


def reconcile_page_predictions(page_texts, classification):
    """Reconcile page-level visual labels with strong OCR structural evidence.

    The original model prediction and probabilities are retained for audit.
    The final page label can change only when multi-cue OCR structure is strong.
    """
    result = dict(classification) if isinstance(classification, dict) else {}
    raw_predictions = result.get("page_predictions")
    if not isinstance(raw_predictions, (list, tuple)) or len(raw_predictions) != len(page_texts):
        return result

    reconciled = []
    override_count = 0
    final_labels = []
    for index, (page_text, raw_prediction) in enumerate(zip(page_texts, raw_predictions)):
        prediction = dict(raw_prediction) if isinstance(raw_prediction, dict) else {}
        visual_label = str(prediction.get("label", "unclassified")).casefold()
        visual_confidence = _safe_confidence(prediction)
        text_label, text_evidence = classify_text_structure(page_text)

        final_label = visual_label
        overridden = bool(text_label and text_label != visual_label)
        if text_label and (visual_label in {"", "unknown", "unclassified"} or overridden):
            final_label = text_label

        prediction.update({
            "page_number": prediction.get("page_number", index + 1),
            "visual_label": visual_label,
            "visual_confidence": visual_confidence,
            "text_label": text_label,
            "text_evidence": text_evidence,
            "label": final_label,
            "classification_source": (
                "ocr_structural_evidence_override" if overridden
                else "ocr_structural_evidence" if text_label and visual_label in {"", "unknown", "unclassified"}
                else prediction.get("classification_source", "efficientnetb0")
            ),
            "classification_conflict": overridden,
            "classification_review_required": overridden or text_evidence.get("strength") == "ambiguous",
        })
        if overridden:
            override_count += 1
            prediction["classification_review_reason"] = (
                f"Visual model predicted '{visual_label}' but multiple OCR structural cues support '{text_label}'. "
                "The final routing label uses the OCR structure; manually verify the page."
            )
        final_labels.append(final_label)
        reconciled.append(prediction)

    supported_labels = [label for label in final_labels if label in {"resume", "invoice", "form"}]
    if supported_labels:
        counts = {label: supported_labels.count(label) for label in set(supported_labels)}
        if len(counts) == 1:
            result["label"] = supported_labels[0]
        else:
            result["label"] = max(counts, key=counts.get)
    result["visual_label"] = result.get("label") if not override_count else classification.get("label")
    result["page_predictions"] = reconciled
    result["classification_method"] = "visual_model_with_ocr_structural_validation"
    result["ocr_override_count"] = override_count
    result["classification_review_required"] = override_count > 0
    if override_count:
        result["classification_review_reasons"] = [
            item["classification_review_reason"]
            for item in reconciled
            if item.get("classification_review_reason")
        ]
    if override_count:
        result["score_type"] = "hybrid_decision"
    return result


def group_page_predictions(page_texts, classification):
    """Group consecutive pages and retain uncertain transitions for review."""
    predictions = classification.get("page_predictions", []) if isinstance(classification, dict) else []
    if not isinstance(predictions, (list, tuple)):
        predictions = []
    if len(predictions) != len(page_texts):
        label = classification.get("label", "unclassified") if isinstance(classification, dict) else "unclassified"
        return [{
            "start_page": 1,
            "end_page": max(1, len(page_texts)),
            "label": label,
            "page_indexes": list(range(len(page_texts))),
            "boundary_method": "single_document_fallback",
            "boundary_review_required": True,
            "boundary_warnings": ["Page-level predictions were unavailable or incomplete; automatic segmentation was not performed."],
        }]

    groups = []
    supported = {"resume", "invoice", "form"}
    for index, (page_text, prediction) in enumerate(zip(page_texts, predictions)):
        prediction = prediction if isinstance(prediction, dict) else {}
        label = str(prediction.get("label", "unclassified")).lower()
        confidence = _safe_confidence(prediction)
        if not groups:
            groups.append({
                "start_page": index + 1,
                "end_page": index + 1,
                "label": label,
                "page_indexes": [index],
                "boundary_method": "first_page",
                "boundary_review_required": label not in supported or confidence < 0.60,
                "boundary_warnings": [],
            })
            continue

        current = groups[-1]
        previous_label = current["label"]
        previous_prediction = predictions[index - 1] if isinstance(predictions[index - 1], dict) else {}
        previous_confidence = _safe_confidence(previous_prediction)
        previous_text = "\n".join(page_texts[i] for i in current["page_indexes"])
        # Strong OCR structure can identify a new document even when
        # the visual classifier confidence is below the normal threshold.
        # Such boundaries are split, but explicitly marked for review.
        strong_text_boundary = (
            prediction.get("classification_conflict") is True
            and isinstance(prediction.get("text_evidence"), dict)
            and prediction["text_evidence"].get("strength") == "strong"
        )

        changed_class = (
            label != previous_label
            and label in supported
            and previous_label in supported
            and (
                (
                    confidence >= 0.60
                    and previous_confidence >= 0.60
                )
                or strong_text_boundary
            )
        )
        repeated_start = (
            label == previous_label
            and label in supported
            and confidence >= 0.60
            and _is_new_document_start(page_text, label, previous_text)
        )

        if changed_class or repeated_start:
            groups.append({
                "start_page": index + 1,
                "end_page": index + 1,
                "label": label,
                "page_indexes": [index],
                "boundary_method": "class_transition" if changed_class else "same_class_identifier_change",
                "boundary_review_required": bool(
                    strong_text_boundary
                    and not (
                        confidence >= 0.60
                        and previous_confidence >= 0.60
                    )
                ),
                "boundary_warnings": (
                    [
                        (
                            f"Page {index + 1} was split using strong OCR "
                            f"structural evidence despite visual confidence "
                            f"{confidence:.2f}. Manually verify this boundary."
                        )
                    ]
                    if strong_text_boundary
                    and not (
                        confidence >= 0.60
                        and previous_confidence >= 0.60
                    )
                    else []
                ),
            })
        else:
            current["end_page"] = index + 1
            current["page_indexes"].append(index)
            if label != previous_label:
                current["boundary_review_required"] = True
                current["boundary_warnings"].append(
                    f"Page {index + 1} predicted {label} ({confidence:.2f}) after {previous_label} ({previous_confidence:.2f}); confidence was insufficient to split automatically."
                )
            if previous_label not in supported and label in supported:
                current["label"] = label

    return groups


def classification_for_group(group, page_classification):
    page_classification = page_classification if isinstance(page_classification, dict) else {}
    page_predictions = page_classification.get("page_predictions", [])
    if not isinstance(page_predictions, (list, tuple)):
        page_predictions = []
    selected = [
        page_predictions[i] if isinstance(page_predictions[i], dict) else {}
        for i in group["page_indexes"]
        if isinstance(i, int) and 0 <= i < len(page_predictions)
    ]
    if not selected:
        return {
            "label": group["label"],
            "model": page_classification.get("model", "unknown"),
            "scores": page_classification.get("scores", []),
            "boundary_review_required": bool(group.get("boundary_review_required")),
            "boundary_warnings": list(group.get("boundary_warnings", [])),
            "classification_source": page_classification.get("classification_source"),
            "classification_review_required": bool(page_classification.get("classification_review_required")),
            "classification_review_reasons": list(page_classification.get("classification_review_reasons", [])),
        }
    labels = ["resume", "invoice", "form"]
    scores = {}
    for label in labels:
        page_scores = []
        for item in selected:
            item_scores = item.get("scores", [])
            if not isinstance(item_scores, (list, tuple)):
                item_scores = []
            score_item = next(
                (entry for entry in item_scores if isinstance(entry, dict) and entry.get("label") == label),
                None,
            )
            page_scores.append(_safe_score(score_item.get("score", 0.0)) if score_item else 0.0)
        scores[label] = sum(page_scores) / len(page_scores)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    chosen = group["label"] if group["label"] in labels else (ranked[0][0] if ranked and ranked[0][1] > 0 else "unclassified")
    source_score_type = page_classification.get("score_type", "softmax_probability")
    has_override = any(item.get("classification_conflict") for item in selected)
    grouped_score_type = (
        "hybrid_decision" if has_override
        else "mean_page_softmax_probability" if source_score_type == "softmax_probability"
        else source_score_type
    )
    visual_labels = [
        str(item.get("visual_label")).lower()
        for item in selected
        if item.get("visual_label") in labels
    ]
    visual_label = max(set(visual_labels), key=visual_labels.count) if visual_labels else None
    visual_confidences = [
        _safe_confidence({"confidence": item.get("visual_confidence")})
        for item in selected
        if item.get("visual_confidence") is not None
    ]
    visual_confidence = (
        round(sum(visual_confidences) / len(visual_confidences), 6)
        if visual_confidences else None
    )
    text_evidence = [
        {
            "page_number": item.get("page_number"),
            "label": item.get("text_label"),
            "matched_cues": (item.get("text_evidence") or {}).get("matched_cues", []),
            "scores": (item.get("text_evidence") or {}).get("scores", {}),
            "overrode_visual_prediction": bool(item.get("classification_conflict")),
        }
        for item in selected
        if item.get("text_label") or item.get("classification_conflict")
    ]
    review_reasons = [
        item.get("classification_review_reason")
        for item in selected
        if item.get("classification_review_reason")
    ]
    return {
        "label": chosen,
        "model": page_classification.get("model", "EfficientNetB0"),
        "score_type": grouped_score_type,
        "scores": [{"label": label, "score": round(score, 6)} for label, score in ranked],
        "visual_label": visual_label,
        "visual_confidence": visual_confidence,
        "classification_source": "ocr_structural_evidence_override" if has_override else "efficientnetb0",
        "classification_conflict": has_override,
        "classification_review_required": has_override or any(item.get("classification_review_required") for item in selected),
        "classification_review_reasons": review_reasons,
        "text_evidence": text_evidence,
        "page_count": len(selected),
        "page_range": {"start": group["start_page"], "end": group["end_page"]},
        "boundary_method": group["boundary_method"],
        "boundary_review_required": bool(group.get("boundary_review_required")),
        "boundary_warnings": list(group.get("boundary_warnings", [])),
        "page_predictions": selected,
    }
