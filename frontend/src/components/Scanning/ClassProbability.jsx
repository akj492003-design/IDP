import Icon from "../Common/Icon.jsx";

export default function ClassProbability({
  prediction = null,
  onConfirm = null,
  confirming = false,
  verificationError = "",
}) {
  const finalClass = String(
    prediction?.predicted_class || "unclassified"
  )
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());

  const finalKey = finalClass.toLowerCase();

  const classificationSource = String(
    prediction?.classification_source || ""
  ).toLowerCase();

  const usesOcr =
    classificationSource.includes("ocr") ||
    classificationSource.includes("structural_validation");

  const methodLabel = usesOcr
    ? "OCR structural validation"
    : classificationSource === "efficientnetb0"
      ? "EfficientNetB0 visual classification"
      : "Document classification pipeline";

  const isVerified = prediction?.verification_status === "verified";
  const canConfirm = ["resume", "invoice", "form"].includes(finalKey);

  const evidence = Array.isArray(prediction?.text_evidence)
    ? prediction.text_evidence
    : [];

  const evidencePages = [
    ...new Set(
      evidence
        .filter(
          (item) =>
            item &&
            String(item.label || "").toLowerCase() === finalKey &&
            Number.isFinite(Number(item.page_number))
        )
        .map((item) => Number(item.page_number))
    ),
  ].sort((a, b) => a - b);



  return (
    <article className="rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-4">
      <h3 className="mb-4 text-sm font-semibold text-(color:--c-fg)">
        Document Type Verification
      </h3>

      <div className="flex min-h-[130px] flex-col items-center justify-center rounded-md border border-(color:--c-line) p-4 text-center">
        <Icon
          name="file-check"
          className="mb-3 h-9 w-9 text-(color:--c-accent)"
        />

        <p className="text-xs text-(color:--c-muted)">
          Final document type
        </p>

        <p className="mt-1 text-2xl font-bold capitalize text-(color:--c-accent)">
          {finalClass}
        </p>
      </div>

      <div className="mt-3 rounded-md border border-(color:--c-line) p-3">
        <p className="text-[10px] uppercase tracking-wide text-(color:--c-muted)">
          Classification method
        </p>

        <p className="mt-1 text-sm font-semibold text-(color:--c-fg)">
          {methodLabel}
        </p>

        {!isVerified && evidencePages.length > 0 && (
          <p className="mt-2 text-xs text-(color:--c-muted)">
            Supporting pages: {evidencePages.join(", ")}
          </p>
        )}

        {isVerified ? (
          <p className="mt-3 inline-flex items-center gap-2 text-xs font-semibold text-emerald-400">
            <Icon name="check" className="h-4 w-4" />
            Confirmed by user
          </p>
        ) : (
          <div className="mt-3">
            <button
              type="button"
              onClick={onConfirm}
              disabled={!canConfirm || !onConfirm || confirming}
              aria-busy={confirming}
              className="inline-flex min-h-9 items-center justify-center rounded-md bg-blue-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-blue-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {confirming ? "Saving confirmation..." : "Confirm document type"}
            </button>

            {verificationError && (
              <p
                className="mt-2 text-xs text-rose-400"
                role="alert"
              >
                {verificationError}
              </p>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
