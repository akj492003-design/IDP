import { useEffect, useRef, useState } from "react";
import Icon from "../Common/Icon.jsx";
import { exportExtractedDocument, updateDocument } from "../../services/idpService.js";
import { getApiErrorMessage } from "../../services/api.js";

const EXTRACTION_FIELD_ORDER = {
  resume: [
    "candidate_name", "name", "full_name",
    "emails", "email", "email_addresses",
    "phone_numbers", "phone",
    "location_address", "address",
    "professional_summary", "technical_skills",
    "work_experience", "experience_duration", "total_experience",
    "educational_qualifications", "projects",
    "certifications", "academic_achievements",
    "internships", "volunteer_experience",
    "extracurricular_activities", "languages",
    "publications", "interests", "references",
    "linkedin", "github", "portfolio",
  ],
  invoice: [
    "supplier_name", "vendor_name", "seller_name",
    "supplier_address", "supplier_state", "supplier_gstin",
    "invoice_number", "invoice_date", "due_date", "place_of_supply",
    "customer_name", "buyer_name", "buyer_customer_details",
    "buyer_address", "supplier_gstin", "buyer_gstin", "gstin",
    "supplier_email_addresses", "supplier_phone_numbers",
    "buyer_email_addresses", "buyer_phone_numbers",
    "line_items", "subtotal", "taxable_amount",
    "tax_breakdown", "tax_type", "gst_rate", "tax_rate", "tax_amount", "total_discount",
    "other_charges", "round_off", "grand_total",
    "amount_paid", "balance_due", "payment_terms", "bank_transfer_reference",
    "amount_in_words", "currency",
    "email_addresses", "phone_numbers",
    "date_candidates", "amount_candidates",
    "calculation_validation",
  ],
  form: [
    "applicant_name", "full_name", "name", "primary_contact",
    "form_title_type", "requested_plan", "number_of_users", "organisation_name",
    "customer_type", "primary_use", "billing_frequency", "invoice_delivery_method",
    "privacy_acknowledgement", "support_channel", "terms_acceptance",
    "declaration_statement", "application_reference_number",
    "application_type", "application_date",
    "email", "email_addresses", "phone", "phone_numbers",
    "date_of_birth", "age", "gender", "address",
    "father_name", "mother_name", "guardian_name",
    "occupation", "nationality", "marital_status",
    "qualification", "category", "religion",
    "annual_income", "state", "district", "pin_code",
    "purpose_reason", "form_questions_answers",
    "selected_checkbox_radio_options",
    "signature_label_present", "labelled_field_values",
    "date_candidates", "amount_candidates",
  ],
};

function orderExtractionEntries(entries, documentType) {
  const priorities = EXTRACTION_FIELD_ORDER[
    String(documentType || "").toLowerCase()
  ] || [];

  const rank = new Map(priorities.map((key, index) => [key, index]));

  return entries
    .map((entry, originalIndex) => ({ entry, originalIndex }))
    .sort((a, b) => {
      const rankA = rank.get(String(a.entry[0]).toLowerCase()) ?? priorities.length;
      const rankB = rank.get(String(b.entry[0]).toLowerCase()) ?? priorities.length;
      return rankA - rankB || a.originalIndex - b.originalIndex;
    })
    .map(({ entry }) => entry);
}

const labelFor = (key) =>
  String(key || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());

function flattenRows(data) {
  return Object.entries(data || {}).map(([field, value]) => ({
    field: labelFor(field),
    value: Array.isArray(value) || (value && typeof value === "object")
      ? JSON.stringify(value, null, 2)
      : value == null ? "" : String(value),
  }));
}

function downloadLocal(data, format) {
  const rows = flattenRows(data);
  let content;
  let mime = "text/plain;charset=utf-8";

  if (format === "json") {
    content = JSON.stringify(data || {}, null, 2);
    mime = "application/json;charset=utf-8";
  } else if (format === "csv") {
    const escape = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
    content = [
      ["Field", "Extracted Value"].map(escape).join(","),
      ...rows.map((row) =>
        [row.field, row.value].map(escape).join(",")
      ),
    ].join("\r\n");
    mime = "text/csv;charset=utf-8";
  } else {
    content = rows.length
      ? rows.map((row, index) => `${index + 1}. ${row.field}\n${row.value}`).join("\n\n")
      : "No extracted fields.";
  }

  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const anchor = window.document.createElement("a");
  anchor.href = url;
  anchor.download = `idp-extracted-information.${format}`;
  window.document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1200);
}

function renderNested(value, keyPrefix) {
  if (value == null || value === "") {
    return <span className="text-(color:--c-muted)">—</span>;
  }

  if (Array.isArray(value)) {
    return (
      <ul className="list-disc space-y-1 pl-5">
        {value.map((item, index) => (
          <li key={`${keyPrefix}-${index}`} className="break-words">
            {item && typeof item === "object"
              ? renderNested(item, `${keyPrefix}-${index}`)
              : String(item ?? "—")}
          </li>
        ))}
      </ul>
    );
  }

  if (typeof value === "object") {
    return (
      <dl className="mt-1 grid gap-1.5 rounded-md border border-(color:--c-line) bg-(color:--c-panel)/45 p-2 sm:grid-cols-2">
        {Object.entries(value).map(([nestedKey, nestedValue]) => (
          <div key={`${keyPrefix}-${nestedKey}`} className="min-w-0">
            <dt className="text-[10px] font-medium text-(color:--c-muted)">{labelFor(nestedKey)}</dt>
            <dd className="mt-0.5 break-words text-xs text-(color:--c-fg)">
              {renderNested(nestedValue, `${keyPrefix}-${nestedKey}`)}
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return <span className="whitespace-pre-wrap break-words">{String(value)}</span>;
}


function hasRenderableValue(value) {
  if (value === null || value === undefined || value === "") return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

// Do not display a section heading or nearby OCR text as an email address.
function normalizeFormTitleForDisplay(value) {
  const title = String(value ?? "").trim();
  if (/^account\s+intake\s+and\s+service$/i.test(title)) {
    return `${title} REQUEST FORM`;
  }
  return title;
}

function getValidatedEmailValue(...values) {
  const pattern = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
  const found = [];

  const collect = (value) => {
    if (Array.isArray(value)) {
      value.forEach(collect);
      return;
    }
    if (typeof value === "string") {
      const matches = value.match(pattern) || [];
      matches.forEach((email) => {
        if (!found.some((item) => item.toLowerCase() === email.toLowerCase())) {
          found.push(email);
        }
      });
    } else if (value && typeof value === "object") {
      Object.values(value).forEach(collect);
    }
  };

  values.forEach(collect);
  return found.length ? found.join(", ") : null;
}

function SourceDetails({ title, value }) {
  if (!hasRenderableValue(value)) return null;
  return (
    <details className="rounded-md border border-(color:--c-line) bg-(color:--c-panel)/35 px-3 py-2">
      <summary className="cursor-pointer text-[10px] font-semibold text-(color:--c-muted)">
        {title} (full OCR party block)
      </summary>
      <div className="mt-2 text-xs leading-relaxed text-(color:--c-fg)">
        {renderNested(value, `source-${title}`)}
      </div>
    </details>
  );
}

function getRecordValue(record, names) {
  if (!record || typeof record !== "object") return null;

  const entries = Object.entries(record);

  for (const name of names) {
    const entry = entries.find(
      ([key]) => key.toLowerCase() === name.toLowerCase()
    );

    if (entry && hasRenderableValue(entry[1])) {
      return entry[1];
    }
  }

  return null;
}

function FieldGroup({ title, items, keyPrefix }) {
  const visible = items.filter(([, value]) => hasRenderableValue(value));

  if (!visible.length) return null;

  return (
    <section className="rounded-md border border-(color:--c-line) bg-(color:--c-panel)/45 p-3">
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-(color:--c-muted)">
        {title}
      </h4>

      <dl className="grid gap-3 sm:grid-cols-2">
        {visible.map(([label, value], index) => (
          <div
            key={`${keyPrefix || title}-${index}-${label}`}
            className="min-w-0"
          >
            <dt className="text-[10px] font-medium text-(color:--c-muted)">
              {label}
            </dt>
            <dd className="mt-1 break-words text-xs leading-relaxed text-(color:--c-fg)">
              {renderNested(value, `${keyPrefix || title}-${index}`)}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function RecordGroup({ title, value }) {
  if (!hasRenderableValue(value)) return null;

  const records = Array.isArray(value) ? value : [value];

  return (
    <section className="rounded-md border border-(color:--c-line) bg-(color:--c-panel)/45 p-3">
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-(color:--c-muted)">
        {title}
        <span className="ml-2 font-normal normal-case">
          ({records.length})
        </span>
      </h4>

      <div className="space-y-2">
        {records.map((record, index) => {
          const heading = getRecordValue(record, [
            "job_title", "title", "project_name", "degree",
            "institution", "language", "name", "company",
            "description",
          ]);

          return (
            <article
              key={`${title}-${index}`}
              className="rounded-md border border-(color:--c-line) bg-(color:--c-inner)/60 p-2.5"
            >
              <p className="mb-1 text-xs font-semibold text-(color:--c-fg)">
                {heading ? String(heading) : `${title} ${index + 1}`}
              </p>
              <div className="text-xs leading-relaxed text-(color:--c-fg)">
                {renderNested(record, `${title}-${index}`)}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

const LEGACY_PROJECT_TECHNOLOGY_ALIASES = new Map([
  ["python", "Python"],
  ["pandas", "Pandas"],
  ["scikit-learn", "Scikit-learn"],
  ["tf-idf", "TF-IDF"],
  ["r", "R"],
  ["statistics", "Statistics"],
  ["google forms", "Google Forms"],
]);

function splitLegacyProjectTechnologyPrefix(value) {
  if (typeof value !== "string") return null;

  const match = value.match(/^\s*(.*?)\s+(?:[??|?]|[-\u2010-\u2015\u2212])\s+(\S[\s\S]*)$/);
  if (!match) return null;

  const parts = match[1]
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);

  if (
    parts.length < 2 ||
    parts.some((part) => !LEGACY_PROJECT_TECHNOLOGY_ALIASES.has(part.toLowerCase()))
  ) {
    return null;
  }

  return {
    technologies: [...new Set(
      parts.map((part) => LEGACY_PROJECT_TECHNOLOGY_ALIASES.get(part.toLowerCase()))
    )],
    description: match[2].trim(),
  };
}

function ProjectGroup({ value }) {
  if (!hasRenderableValue(value)) return null;

  const records = Array.isArray(value) ? value : [value];

  const knownKeys = new Set([
    "title", "project_name", "name",
    "description", "summary", "details",
    "technologies", "tech_stack", "tools",
    "url", "link",
  ]);

  return (
    <section className="rounded-md border border-(color:--c-line) bg-(color:--c-panel)/45 p-3">
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-(color:--c-muted)">
        Projects <span className="ml-2 font-normal normal-case">({records.length})</span>
      </h4>

      <div className="space-y-2">
        {records.map((record, index) => {
          const item = record && typeof record === "object" && !Array.isArray(record)
            ? record
            : { description: record };

          const title = getRecordValue(item, [
            "title", "project_name", "name",
          ]);

          const storedTechnologies = getRecordValue(item, [
            "technologies", "tech_stack", "tools",
          ]);

          const rawDescription = getRecordValue(item, [
            "description", "summary", "details",
          ]);

          // Older records may have the technology stack fused into the
          // beginning of Description without a separate technologies field.
          // Always inspect the description. Older records can contain an
          // empty or incomplete technologies property alongside a fused stack.
          const legacyStack = splitLegacyProjectTechnologyPrefix(rawDescription);

          const storedTechnologyItems = Array.isArray(storedTechnologies)
            ? storedTechnologies.filter((item) => typeof item === "string" && item.trim())
            : typeof storedTechnologies === "string"
              ? storedTechnologies.split(/[,;|]/).map((item) => item.trim()).filter(Boolean)
              : [];

          const technologies = legacyStack
            ? [...new Map(
                [...storedTechnologyItems, ...legacyStack.technologies]
                  .map((item) => [String(item).toLowerCase(), item])
              ).values()]
            : storedTechnologies;

          const url = getRecordValue(item, ["url", "link"]);
          let description = legacyStack?.description ?? rawDescription;

          // Some older records contain both a technologies field and the
          // same stack duplicated at the beginning of Description.
          const technologyLabel = Array.isArray(technologies)
            ? technologies.join(", ")
            : String(technologies ?? "");

          if (
            hasRenderableValue(storedTechnologies) &&
            technologyLabel &&
            typeof rawDescription === "string" &&
            rawDescription.trimStart().slice(0, technologyLabel.length).toLowerCase()
              === technologyLabel.toLowerCase()
          ) {
            description = rawDescription.trimStart()
              .slice(technologyLabel.length)
              .replace(/^\s*(?:[??|??]\s*)?/, "")
              .trim();
          }

          const additional = Object.fromEntries(
            Object.entries(item).filter(
              ([key]) => !knownKeys.has(key.toLowerCase())
            )
          );

          return (
            <article
              key={`project-${index}`}
              className="rounded-md border border-(color:--c-line) bg-(color:--c-inner)/60 p-2.5"
            >
              <p className="mb-2 text-xs font-semibold text-(color:--c-fg)">
                {title ? String(title) : `Project ${index + 1}`}
              </p>

              {hasRenderableValue(technologies) && (
                <div className="mb-2">
                  <p className="text-[10px] font-medium text-(color:--c-muted)">
                    Technologies
                  </p>
                  <div className="mt-1 text-xs leading-relaxed text-(color:--c-fg)">
                    {renderNested(technologies, `project-${index}-technologies`)}
                  </div>
                </div>
              )}

              {hasRenderableValue(description) && (
                <div className="mb-2">
                  <p className="text-[10px] font-medium text-(color:--c-muted)">
                    Description
                  </p>
                  <div className="mt-1 break-words text-xs leading-relaxed text-(color:--c-fg)">
                    {renderNested(description, `project-${index}-description`)}
                  </div>
                </div>
              )}

              {hasRenderableValue(url) && (
                <div className="mb-2">
                  <p className="text-[10px] font-medium text-(color:--c-muted)">
                    URL
                  </p>
                  <div className="mt-1 break-all text-xs text-(color:--c-fg)">
                    {renderNested(url, `project-${index}-url`)}
                  </div>
                </div>
              )}

              {hasRenderableValue(additional) && (
                <div className="mt-2 border-t border-(color:--c-line) pt-2">
                  <p className="text-[10px] font-medium text-(color:--c-muted)">
                    Additional project details
                  </p>
                  <div className="mt-1 text-xs text-(color:--c-fg)">
                    {renderNested(additional, `project-${index}-additional`)}
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function InvoiceLineItems({ value }) {
  if (!hasRenderableValue(value)) return null;

  const items = Array.isArray(value) ? value : [value];

  const columns = [
    ["Description", ["description", "item_description", "name", "product"]],
    ["HSN/SAC", ["hsn_sac_code", "hsn", "sac"]],
    ["Quantity", ["quantity", "qty"]],
    ["Unit Price", ["unit_price", "rate", "unit_rate"]],
    ["Taxable Value", ["taxable_value", "line_total", "amount"]],
  ];

  return (
    <section className="rounded-md border border-(color:--c-line) bg-(color:--c-panel)/45 p-3">
      <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-(color:--c-muted)">
        Invoice line items ({items.length})
      </h4>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-(color:--c-line)">
              {columns.map(([label]) => (
                <th
                  key={label}
                  className="px-2 py-2 font-semibold text-(color:--c-muted)"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {items.map((item, index) => (
              <tr
                key={`invoice-item-${index}`}
                className="border-b border-(color:--c-line) last:border-0"
              >
                {columns.map(([label, aliases]) => (
                  <td
                    key={label}
                    className="max-w-[220px] break-words px-2 py-2 align-top text-(color:--c-fg)"
                  >
                    {renderNested(
                      getRecordValue(item, aliases),
                      `invoice-${index}-${label}`
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// INVOICE_FORM_UI_DEDUP_EMAIL_FIX_20261116
function StructuredExtractionView({ data, documentType, fieldProvenance }) {
  const source = data && typeof data === "object" ? data : {};
  const type = String(documentType || "").toLowerCase();

  const used = new Set();

  const take = (...names) => {
    const keys = Object.keys(source);

    const actualKeys = [
      ...new Set(
        names
          .map((name) =>
            keys.find(
              (key) => key.toLowerCase() === name.toLowerCase()
            )
          )
          .filter((key) => key !== undefined)
      ),
    ];

    // Mark every alias as consumed so equivalent keys do not
    // reappear in Additional Extracted Information.
    actualKeys.forEach((key) => used.add(key));

    for (const actual of actualKeys) {
      if (hasRenderableValue(source[actual])) {
        return source[actual];
      }
    }

    return null;
  };

  const diagnosticsPattern =
    /candidate|raw_ocr|ocr_candidates|date_candidates|amount_candidates|email_candidates|phone_candidates|field_provenance|confidence|debug|labelled_field_values/i;
  const diagnosticExactKeys = new Set([
    "dates", "amounts", "invoice_numbers", "gstin",
    "email_addresses", "phone_numbers",
  ]);

  const diagnostics = Object.entries(source).filter(([key]) =>
    diagnosticsPattern.test(key) || diagnosticExactKeys.has(key.toLowerCase())
  );
  const provenance = fieldProvenance && typeof fieldProvenance === "object"
    ? fieldProvenance
    : {};
  const provenanceItems = Object.entries(provenance).filter(([, value]) =>
    hasRenderableValue(value)
  );

  let sections;

  if (type === "resume") {
    const identity = [
      ["Candidate Name", take("candidate_name", "full_name", "name")],
      ["Email", take("emails", "email", "email_addresses")],
      ["Phone", take("phone_numbers", "phones", "phone")],
      ["Location", take("location_address", "address", "location")],
      ["LinkedIn", take("linkedin")],
      ["GitHub", take("github")],
      ["Portfolio", take("portfolio")],
      ["Experience Duration", take("experience_duration", "total_experience")],
    ];

    const summary = take("professional_summary", "summary");
    const skills = take("technical_skills", "skills", "key_skills");
    const work = take("work_experience", "employment_history", "experience");
    const education = take(
      "educational_qualifications", "education", "educational_details"
    );
    const projects = take("projects", "project_experience");
    const certifications = take("certifications");
    const achievements = take("academic_achievements", "achievements");
    const coursework = take("relevant_coursework", "coursework");
    const languages = take("languages", "language_proficiency");

    sections = (
      <>
        <FieldGroup title="Candidate profile" items={identity} keyPrefix="resume-profile" />
        <FieldGroup title="Professional summary" items={[["Summary", summary]]} keyPrefix="resume-summary" />
        <RecordGroup title="Technical skills" value={skills} />
        <RecordGroup title="Work experience" value={work} />
        <RecordGroup title="Education" value={education} />
        <ProjectGroup value={projects} />
        <RecordGroup title="Certifications" value={certifications} />
        <RecordGroup title="Academic achievements" value={achievements} />
        <FieldGroup title="Relevant coursework" items={[["Courses", coursework]]} keyPrefix="resume-coursework" />
        <RecordGroup title="Languages" value={languages} />
      </>
    );
  } else if (type === "invoice") {
    const invoice = [
      ["Invoice Number", take("invoice_number", "invoice_no")],
      ["Invoice Date", take("invoice_date")],
      ["Due Date", take("due_date")],
      ["Place of Supply", take("place_of_supply")],
      ["Currency", take("currency")],
      ["Payment Terms", take("payment_terms")],
      ["Bank Transfer Reference", take("bank_transfer_reference", "bank_reference", "utr_number", "transaction_reference")],
      ["Amount in Words", take("amount_in_words")],
    ];

    const sellerSourceBlock = take("seller_company_details");
    const seller = [
      ["Supplier", take("supplier_name", "vendor_name", "seller_name")],
      ["Supplier State", take("supplier_state")],
      ["Supplier GSTIN", take("supplier_gstin")],
      ["Supplier Address", take("supplier_address")],
      ["Supplier Email", take("supplier_email_addresses", "supplier_email")],
      ["Supplier Phone", take("supplier_phone_numbers", "supplier_phone")],
    ];

    const buyerSourceBlock = take("buyer_customer_details");
    const buyer = [
      ["Customer", take("customer_name", "buyer_name")],
      ["Customer GSTIN", take("buyer_gstin")],
      ["Customer Address", take("buyer_address")],
      ["Customer Email", take("buyer_email_addresses", "buyer_email")],
      ["Customer Phone", take("buyer_phone_numbers", "buyer_phone")],
    ];

    const lineItems = take("line_items", "items", "invoice_line_items");
    const taxes = take("tax_breakdown");
    const calculationValidation = take("calculation_validation");
    const taxDetails = [
      ["Tax Type", take("tax_type")],
      ["GST Rate", take("gst_rate", "tax_rate")],
    ];
    const totals = [
      ["Subtotal", take("subtotal")],
      ["Taxable Amount", take("taxable_amount")],
      ["Tax Amount", take("tax_amount", "total_tax")],
      ["CGST", take("cgst")],
      ["SGST", take("sgst")],
      ["IGST", take("igst")],
      ["Discount", take("total_discount", "discount")],
      ["Other Charges", take("other_charges")],
      ["Round Off", take("round_off")],
      ["Grand Total", take("grand_total", "total_amount", "total")],
      ["Amount Paid", take("amount_paid")],
      ["Balance Due", take("balance_due")],
    ];

    sections = (
      <>
        <FieldGroup title="Invoice details" items={invoice} keyPrefix="invoice-meta" />
        <FieldGroup title="Seller / supplier" items={seller} keyPrefix="invoice-seller" />
        <SourceDetails title="Seller source details" value={sellerSourceBlock} />
        <FieldGroup title="Buyer / customer" items={buyer} keyPrefix="invoice-buyer" />
        <SourceDetails title="Buyer source details" value={buyerSourceBlock} />
        <InvoiceLineItems value={lineItems} />
        <FieldGroup title="Tax details" items={taxDetails} keyPrefix="invoice-tax-meta" />
        <FieldGroup title="Tax breakdown" items={[["Tax Breakdown", taxes]]} keyPrefix="invoice-tax" />
        <FieldGroup title="Invoice totals" items={totals} keyPrefix="invoice-total" />
        <FieldGroup
          title="Financial calculation validation"
          items={[["Reconciliation checks", calculationValidation]]}
          keyPrefix="invoice-validation"
        />
      </>
    );
  } else if (type === "form") {
    const applicant = [
      ["Form Title", normalizeFormTitleForDisplay(take("form_title_type", "form_title", "title"))],
      ["Application Reference", take("application_reference_number", "reference_number", "registration_number")],
      ["Applicant Name", take("applicant_name", "full_name", "name")],
      ["Application Type", take("application_type")],
      ["Application Date", take("application_date")],
      ["Submission Date", take("submission_date")],
      ["Date of Birth", take("date_of_birth", "dob")],
      ["Age", take("age")],
    ];

    const emailAddresses = take("email_addresses");
    const emailField = take("email");
    const validatedEmail = getValidatedEmailValue(emailAddresses, emailField);
    const contact = [
      ["Primary Contact", take("primary_contact")],
      ["Email", validatedEmail],
      ["Phone", take("phone", "phone_numbers")],
      ["Address", take("address", "residential_address")],
      ["State", take("state")],
      ["District", take("district")],
      ["PIN Code", take("pin_code", "pincode", "postal_code")],
    ];

    const organisation = [
      ["Organisation", take("organisation_name", "organization_name")],
      ["Customer Type", take("customer_type")],
      ["Requested Plan", take("requested_plan", "subscription_plan")],
      ["Number of Users", take("number_of_users", "user_count")],
      ["Primary Use", take("primary_use")],
      ["Billing Frequency", take("billing_frequency")],
      ["Invoice Delivery Method", take("invoice_delivery_method")],
      ["Support Channel", take("support_channel")],
    ];

    const personal = [
      ["Father Name", take("father_name")],
      ["Mother Name", take("mother_name")],
      ["Guardian Name", take("guardian_name")],
      ["Gender", take("gender")],
      ["Marital Status", take("marital_status")],
      ["Occupation", take("occupation")],
      ["Nationality", take("nationality")],
    ];

    const declarations = [
      ["Declaration Statement", take("declaration_statement", "declarations", "declaration")],
      ["Consent", take("privacy_acknowledgement", "applicant_consent", "consent")],
      ["Terms Accepted", take("terms_acceptance", "terms_accepted")],
      ["Declarant / Authorised Person", take("declarant_authorised_person")],
      ["Selected Options", take("selected_checkbox_radio_options")],
      ["Form Questions and Answers", take("form_questions_answers")],
      ["Submission Status", take("submission_status")],
      ["Record Status", take("record_status")],
    ];

    sections = (
      <>
        <FieldGroup title="Application details" items={applicant} keyPrefix="form-applicant" />
        <FieldGroup title="Contact details" items={contact} keyPrefix="form-contact" />
        <FieldGroup title="Organisation and service details" items={organisation} keyPrefix="form-org" />
        <FieldGroup title="Personal information" items={personal} keyPrefix="form-personal" />
        <FieldGroup title="Declarations and selections" items={declarations} keyPrefix="form-declarations" />
      </>
    );
  } else {
    Object.keys(source).forEach((key) => used.add(key));
    sections = (
      <FieldGroup
        title="Extracted fields"
        items={Object.entries(source).map(([key, value]) => [labelFor(key), value])}
        keyPrefix="generic"
      />
    );
  }

  const additional = Object.entries(source).filter(
    ([key, value]) =>
      !used.has(key) &&
      !diagnosticsPattern.test(key) &&
      !diagnosticExactKeys.has(key.toLowerCase()) &&
      hasRenderableValue(value)
  );

  // Do not repeat fields already displayed in structured sections.
  // The original extraction payload remains unchanged.
  const visibleDiagnostics = diagnostics.filter(
    ([key]) => !used.has(key)
  );
  return (
    <div className="space-y-3">
      {sections}

      {additional.length > 0 && (
        <FieldGroup
          title="Additional extracted information"
          items={additional.map(([key, value]) => [labelFor(key), value])}
          keyPrefix="additional"
        />
      )}

      {visibleDiagnostics.length > 0 && (
        <details className="rounded-md border border-(color:--c-line) bg-(color:--c-panel)/40 p-3">
          <summary className="cursor-pointer text-xs font-semibold text-(color:--c-muted)">
            OCR diagnostics and candidate values ({visibleDiagnostics.length})
          </summary>

          <div className="mt-3 space-y-3">
            {visibleDiagnostics.map(([key, value]) => (
              <FieldGroup
                key={`diagnostic-${key}`}
                title={labelFor(key)}
                items={[[labelFor(key), value]]}
                keyPrefix={`diagnostic-${key}`}
              />
            ))}
          </div>
        </details>
      )}

      {provenanceItems.length > 0 && (
        <details className="rounded-md border border-(color:--c-line) bg-(color:--c-panel)/40 p-3">
          <summary className="cursor-pointer text-xs font-semibold text-(color:--c-muted)">
            Field-level source evidence ({provenanceItems.length})
          </summary>
          <div className="mt-3 space-y-3">
            {provenanceItems.map(([key, value]) => (
              <FieldGroup
                key={`provenance-${key}`}
                title={labelFor(key)}
                items={[[labelFor(key), value]]}
                keyPrefix={`provenance-${key}`}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}


export default function ExtractedInformation({
  data = null,
  documentId = null,
  documentIndex = null,
  documents = [],
  selectedDocumentIndex = null,
  onSelectDocument = null,
  status = "idle",
  fileName = "document",
  fieldProvenance = {},
  reviewDecision = null,
  onReviewSaved = null,
}) {
  const [format, setFormat] = useState("pdf");
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [exportScope, setExportScope] = useState("current");
  const [printingScope, setPrintingScope] = useState("");
  const listRef = useRef(null);

  const [editingFields, setEditingFields] = useState(false);
  const [draftFields, setDraftFields] = useState(() =>
    JSON.stringify(data || {}, null, 2)
  );
  const [savingFields, setSavingFields] = useState(false);
  const [editError, setEditError] = useState("");

  const [reviewComment, setReviewComment] = useState("");
  const [savingReview, setSavingReview] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [reviewMessage, setReviewMessage] = useState("");

  const currentReviewDecision = String(
    reviewDecision?.decision || reviewDecision?.status || "pending"
  ).toLowerCase();


  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = 0;
    }
  }, [documentId, documentIndex]);

  const activeDocumentIndex = selectedDocumentIndex ?? documentIndex;
  const selectedSegment = (Array.isArray(documents) ? documents : []).find(
    (segment) => Number(segment.document_index) === Number(activeDocumentIndex)
  ) || (
    Array.isArray(documents) && documents.length === 1
      ? documents[0]
      : null
  );

  const documentType = selectedSegment?.classification?.label || "";
  const entries = orderExtractionEntries(
    Object.entries(data || {}).filter(([, value]) =>
      value !== null && value !== undefined && value !== ""
    ),
    documentType
  );
  const hasData = entries.length > 0;
  const canExportAll = status === "completed" && Boolean(documentId);

  const handleBeginEditing = () => {
    setDraftFields(JSON.stringify(data || {}, null, 2));
    setEditError("");
    setReviewMessage("");
    setEditingFields(true);
  };

  const handleCancelEditing = () => {
    setEditingFields(false);
    setEditError("");
    setDraftFields(JSON.stringify(data || {}, null, 2));
  };

  const handleSaveFields = async () => {
    if (!documentId) {
      setEditError("The document ID is unavailable.");
      return;
    }

    let parsedFields;

    try {
      parsedFields = JSON.parse(draftFields);
    } catch {
      setEditError("Invalid JSON. Correct the syntax before saving.");
      return;
    }

    if (
      !parsedFields ||
      typeof parsedFields !== "object" ||
      Array.isArray(parsedFields)
    ) {
      setEditError("The extracted fields must be a JSON object.");
      return;
    }

    setSavingFields(true);
    setEditError("");
    setReviewMessage("");

    try {
      await updateDocument(documentId, {
        extracted_fields_update: {
          document_index: documentIndex ?? null,
          fields: parsedFields,
        },
      });

      setEditingFields(false);
      setReviewMessage("Extracted-field corrections saved.");

      try {
        await onReviewSaved?.();
      } catch {
        setReviewMessage(
          "Corrections were saved, but the latest document could not be reloaded."
        );
      }
    } catch (error) {
      setEditError(
        error?.response?.data?.message ||
        error?.message ||
        "Unable to save extracted-field corrections."
      );
    } finally {
      setSavingFields(false);
    }
  };

  const handleReviewDecision = async (decision) => {
    if (!documentId) {
      setReviewError("The document ID is unavailable.");
      return;
    }

    setSavingReview(true);
    setReviewError("");
    setReviewMessage("");

    try {
      await updateDocument(documentId, {
        review_decision: {
          decision,
          document_index: documentIndex ?? null,
          comment: reviewComment.trim(),
        },
      });

      setReviewComment("");
      setReviewMessage(
        decision === "approved"
          ? "Document approval saved."
          : "Document rejection saved."
      );

      try {
        await onReviewSaved?.();
      } catch {
        setReviewMessage(
          "Review decision was saved, but the latest document could not be reloaded."
        );
      }
    } catch (error) {
      setReviewError(
        error?.response?.data?.message ||
        error?.message ||
        "Unable to save the review decision."
      );
    } finally {
      setSavingReview(false);
    }
  };

  const canReview = status === "completed" && Boolean(documentId);
  const canEditFields = canReview && hasData;

  const handleDownload = async () => {
    setDownloadError("");

    if (!canExportAll) {
      if (exportScope === "all") {
        setDownloadError("Complete document processing before exporting all detected documents.");
        return;
      }
      if (["json", "csv", "txt"].includes(format)) {
        downloadLocal(data, format);
      } else {
        setDownloadError(
          "Complete document processing before generating PDF, DOCX, XLSX or XML reports."
        );
      }
      return;
    }

    setDownloading(true);
    try {
      const blob = await exportExtractedDocument(documentId, format, documentIndex, exportScope);
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      const safeBase = String(fileName || "document").replace(/\.[^.]+$/, "").replace(/[^\w-]+/g, "_");
      anchor.href = url;
      const segmentSuffix = exportScope === "all"
        ? "_all_documents"
        : documentIndex != null
          ? `_document_${String(documentIndex).padStart(2, "0")}`
          : "";
      const downloadExtension = format === "csv" ? "csv" : format;
      anchor.download = `${safeBase || "document"}${segmentSuffix}_extracted.${downloadExtension}`;
      window.document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch (error) {
      setDownloadError(
        getApiErrorMessage(error, "Unable to export extracted information.")
      );
    } finally {
      setDownloading(false);
    }
  };

  const handlePrint = async (scope) => {
    setDownloadError("");

    if (!canExportAll) {
      setDownloadError("Complete document processing before printing its PDF report.");
      return;
    }

    // Open synchronously so browser popup protection does not block the tab.
    const printWindow = window.open("about:blank", "_blank");

    if (!printWindow) {
      setDownloadError("Allow pop-ups for this website to open the print preview.");
      return;
    }

    setPrintingScope(scope);

    try {
      const blob = await exportExtractedDocument(
        documentId,
        "pdf",
        documentIndex,
        scope
      );

      const url = URL.createObjectURL(blob);
      printWindow.location.replace(url);

      // Keep the PDF URL alive while the browser viewer is being used.
      window.setTimeout(() => URL.revokeObjectURL(url), 10 * 60 * 1000);
    } catch (error) {
      printWindow.close();
      setDownloadError(
        getApiErrorMessage(error, "Unable to prepare the PDF for printing.")
      );
    } finally {
      setPrintingScope("");
    }
  };

  return (
    <article className="flex h-full min-h-0 min-w-0 flex-col rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3 shadow-[0_2px_10px_rgba(0,0,0,.04)] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:shadow-[0_6px_18px_rgba(15,35,70,.06)] w-full max-w-full">
      <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-(color:--c-fg)">Extracted Information</h3>
        {hasData && (
          <div className="flex flex-wrap items-center gap-1.5">
            {Array.isArray(documents) && documents.length > 1 && (
              <>
                <label htmlFor="extracted-export-scope" className="sr-only">
                  Export scope
                </label>
                <select
                  id="extracted-export-scope"
                  value={exportScope}
                  onChange={(event) => setExportScope(event.target.value)}
                  disabled={downloading}
                  className="h-8 max-w-[155px] rounded-md border border-(color:--c-line) bg-(color:--c-panel) px-2 text-[11px] text-(color:--c-fg) focus-visible:outline-2 focus-visible:outline-blue-500 disabled:opacity-60"
                >
                  <option value="current">Current document</option>
                  <option value="all">All {documents.length} documents</option>
                </select>
              </>
            )}
            <label htmlFor="extracted-export-format" className="sr-only">Export format</label>
            <select
              id="extracted-export-format"
              value={format}
              onChange={(event) => setFormat(event.target.value)}
              className="h-8 rounded-md border border-(color:--c-line) bg-(color:--c-panel) px-2 text-[11px] text-(color:--c-fg) focus-visible:outline-2 focus-visible:outline-blue-500"
            >
              <option value="pdf">PDF report</option>
              <option value="txt">TXT report</option>
              <option value="docx">DOCX report</option>
              <option value="csv">CSV report</option>
              <option value="xlsx">Excel workbook (XLSX)</option>
              <option value="json">JSON report</option>
              <option value="xml">XML report</option>
            </select>
            <button
              type="button"
              onClick={handleDownload}
              disabled={downloading}
              className="inline-flex h-8 items-center gap-1.5 rounded-md bg-blue-600 px-2.5 text-[11px] font-semibold text-white transition hover:bg-blue-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:cursor-wait disabled:opacity-60"
            >
              <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M12 3v12m0 0 4-4m-4 4-4-4M4 17v4h16v-4" />
              </svg>
              {downloading ? "Preparing…" : "Export"}
            </button>

            <button
              type="button"
              onClick={() => handlePrint("current")}
              disabled={downloading || Boolean(printingScope)}
              className="inline-flex h-8 items-center rounded-md border border-blue-400/40 px-2.5 text-[11px] font-semibold text-(color:--c-fg) transition hover:bg-blue-500/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:cursor-wait disabled:opacity-60"
            >
              {printingScope === "current" ? "Preparing..." : "Print Current"}
            </button>

            <button
              type="button"
              onClick={() => handlePrint("all")}
              disabled={downloading || Boolean(printingScope)}
              className="inline-flex h-8 items-center rounded-md border border-blue-400/40 px-2.5 text-[11px] font-semibold text-(color:--c-fg) transition hover:bg-blue-500/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:cursor-wait disabled:opacity-60"
            >
              {printingScope === "all" ? "Preparing..." : "Print All"}
            </button>
          </div>
        )}
      </div>

      {canReview && (
        <section className="mb-3 space-y-2 rounded-md border border-(color:--c-line) bg-(color:--c-panel)/45 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs text-(color:--c-muted)">
              Human review decision:
              <strong className="ml-1 capitalize text-(color:--c-fg)">
                {currentReviewDecision.replaceAll("_", " ")}
              </strong>
            </div>

            <div className="flex flex-wrap gap-2">
              {canEditFields && (
                editingFields ? (
                  <>
                    <button
                      type="button"
                      onClick={handleSaveFields}
                      disabled={savingFields}
                      className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-60"
                    >
                      {savingFields ? "Saving..." : "Save Corrections"}
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelEditing}
                      disabled={savingFields}
                      className="rounded-md border border-(color:--c-line) px-3 py-1.5 text-xs font-semibold text-(color:--c-fg) disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={handleBeginEditing}
                    className="rounded-md border border-blue-400/50 px-3 py-1.5 text-xs font-semibold text-(color:--c-fg) hover:bg-blue-500/10"
                  >
                    Edit Extracted Fields
                  </button>
                )
              )}

              <button
                type="button"
                onClick={() => handleReviewDecision("approved")}
                disabled={savingReview}
                className="rounded-md bg-emerald-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-600 disabled:opacity-60"
              >
                Approve
              </button>

              <button
                type="button"
                onClick={() => handleReviewDecision("rejected")}
                disabled={savingReview}
                className="rounded-md bg-rose-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-600 disabled:opacity-60"
              >
                Reject
              </button>
            </div>
          </div>

          <label className="block">
            <span className="mb-1 block text-[11px] font-medium text-(color:--c-muted)">
              Reviewer comment (optional)
            </span>
            <textarea
              value={reviewComment}
              onChange={(event) => setReviewComment(event.target.value)}
              maxLength={1000}
              rows={2}
              placeholder="Add a correction note or reason for rejection..."
              className="w-full rounded-md border border-(color:--c-line) bg-(color:--c-inner) px-2.5 py-2 text-xs text-(color:--c-fg) outline-none focus:border-blue-500"
            />
          </label>

          {editError && (
            <p role="alert" className="text-xs text-rose-600 [.dark_&]:text-rose-300">
              {editError}
            </p>
          )}

          {reviewError && (
            <p role="alert" className="text-xs text-rose-600 [.dark_&]:text-rose-300">
              {reviewError}
            </p>
          )}

          {reviewMessage && (
            <p role="status" className="text-xs text-emerald-700 [.dark_&]:text-emerald-300">
              {reviewMessage}
            </p>
          )}
        </section>
      )}

      {Array.isArray(documents) && documents.length > 1 && (
        <nav className="mb-3 rounded-md border border-(color:--c-line) bg-(color:--c-panel)/55 p-2" aria-label="Detected documents">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-(color:--c-muted)">
            {documents.length} detected documents · Select one to view its extraction
          </p>
          <div className="flex flex-wrap gap-1.5">
            {documents.map((segment) => {
              const index = Number(segment.document_index);
              const selected = index === Number(selectedDocumentIndex);
              return (
                <button
                  key={`segment-${index}`}
                  type="button"
                  onClick={() => onSelectDocument?.(index)}
                  aria-pressed={selected}
                  className={`rounded-md border px-2.5 py-1.5 text-[11px] font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 ${selected
                    ? "border-blue-400 bg-blue-600 text-white shadow-sm"
                    : "border-(color:--c-line) bg-(color:--c-inner) text-(color:--c-fg) hover:border-blue-400 hover:bg-blue-500/10"
                  }`}
                >
                  {segment.label || `Document ${index}`}
                </button>
              );
            })}
          </div>
        </nav>
      )}


      {downloadError && (
        <p className="mb-2 rounded-md border border-rose-300 bg-rose-50 px-2 py-1.5 text-xs text-rose-800" role="alert">
          {downloadError}
        </p>
      )}

      {hasData ? (
        <div
          ref={listRef}
          className="min-h-0 flex-1 min-w-0 w-full max-w-full overflow-x-hidden overflow-y-auto rounded-md border border-(color:--c-line) p-3 [.light_&]:border-[#d8e1ee]"
        >
          {editingFields ? (
            <div className="space-y-2">
              <p className="text-xs text-(color:--c-muted)">
                Edit the complete extracted-fields JSON object. Preserve field names and nested structures, including invoice line items.
              </p>
              <textarea
                aria-label="Edit extracted fields as JSON"
                spellCheck={false}
                value={draftFields}
                onChange={(event) => setDraftFields(event.target.value)}
                className="min-h-[360px] w-full resize-y rounded-md border border-(color:--c-line) bg-(color:--c-panel) p-3 font-mono text-xs leading-5 text-(color:--c-fg) outline-none focus:border-blue-500"
              />
            </div>
          ) : (
            <StructuredExtractionView
              data={data}
              documentType={documentType}
              fieldProvenance={fieldProvenance}
            />
          )}
        </div>
      ) : (
        <div className="flex min-h-[140px] flex-col items-center justify-center rounded-md border border-dashed border-(color:--c-dash)/70 p-4 text-center text-xs leading-relaxed text-(color:--c-muted)">
          <Icon name="list" className="mb-3 h-9 w-9 text-(color:--c-accent)" />
          <p className="max-w-[180px]">Extracted information will appear here after processing the document.</p>
        </div>
      )}
    </article>
  );
}
