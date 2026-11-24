import Icon from "../Common/Icon.jsx";

export default function PredictionResults({ prediction = null }) {
  const formatLabel = (value) => {
    const text = String(value || "Unclassified").replace(/[_-]+/g, " ");
    return text.replace(/\b\w/g, (letter) => letter.toUpperCase());
  };

  const finalClass = prediction?.prediction_rejected
    ? "Rejected ? Manual Review"
    : formatLabel(prediction?.predicted_class);

  const modelName =
    prediction?.model_name == null || prediction.model_name === ""
      ? "Unavailable"
      : String(prediction.model_name);

  const pageCount = Number(prediction?.pages_analyzed);
  const pagesAnalyzed =
    Number.isInteger(pageCount) && pageCount > 0
      ? String(pageCount)
      : "Unavailable";

  const isVerified = prediction?.verification_status === "verified";

  return (
    <article className="rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3">
      <h3 className="mb-3 text-sm font-semibold text-(color:--c-fg)">
        Prediction Results
      </h3>

      <dl className="grid grid-cols-2 overflow-hidden rounded-md border border-(color:--c-line) text-xs [&>div]:min-w-0 [&>div]:p-3">
        <div className="border-b border-r border-(color:--c-line)">
          <div className="flex items-center gap-2">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[#034a45] text-emerald-300">
              <Icon name="doc" className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <dt className="text-(color:--c-muted)">Final document class</dt>
              <dd className="mt-1 break-words font-bold text-(color:--c-fg)">
                {finalClass}
              </dd>
            </div>
          </div>
        </div>

        <div className="border-b border-(color:--c-line)">
          <dt className="text-(color:--c-muted)">Selected model</dt>
          <dd className="mt-2 break-words font-bold text-(color:--c-fg)">
            {modelName}
          </dd>
        </div>

        <div className="border-r border-(color:--c-line)">
          <dt className="text-(color:--c-muted)">Pages analyzed</dt>
          <dd className="mt-2 font-bold tabular-nums text-(color:--c-fg)">
            {pagesAnalyzed}
          </dd>
        </div>

        <div>
          <dt className="text-(color:--c-muted)">Verification status</dt>
          <dd className="mt-2">
            <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold ${
              isVerified
                ? "bg-emerald-500/15 text-emerald-300"
                : "bg-amber-500/15 text-amber-300"
            }`}>
              {isVerified ? "Verified" : "Pending confirmation"}
            </span>
          </dd>
        </div>
      </dl>
    </article>
  );
}
