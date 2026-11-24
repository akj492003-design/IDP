import { useEffect, useMemo, useState } from "react";
import {
  ChartNoAxesColumnIncreasing,
  Activity,
  Award,
  Crosshair,
} from "lucide-react";

import StatCard from "../Common/StatCard.jsx";
import { getModelMetrics } from "../../services/idpService.js";
import {
  ModelAccuracyChart,
  MetricsComparisonChart,
  ConfusionMatrixChart,
  PerClassPerformanceChart,
  ModelComparisonRadarChart,
  InferenceTimeChart,
} from "./PerformanceCharts.jsx";
import ClassificationReport from "./ClassificationReport.jsx";

const EMPTY_MODELS = Object.freeze([]);

const isNumeric = (value) =>
  value !== null &&
  value !== undefined &&
  value !== "" &&
  typeof value !== "boolean" &&
  Number.isFinite(Number(value));

const toPercent = (value) => {
  if (!isNumeric(value)) return null;
  const number = Number(value);
  const percentage = number <= 1 ? number * 100 : number;
  return percentage >= 0 && percentage <= 100 ? percentage : null;
};

const percent = (value) => {
  const number = toPercent(value);
  return number === null ? "—" : `${number.toFixed(2)}%`;
};

function toReportRows(model) {
  const report = model?.classification_report || {};
  return Object.entries(report)
    .filter(([, row]) =>
      row &&
      isNumeric(row.precision) &&
      isNumeric(row.recall) &&
      isNumeric(row.f1_score)
    )
    .map(([label, row]) => ({
      class: label,
      precision: percent(row.precision),
      recall: percent(row.recall),
      f1_score: percent(row.f1_score),
      support: row.support ?? "—",
    }));
}

function toPerClassRows(model) {
  return Object.entries(model?.classification_report || {})
    .filter(([, row]) =>
      row &&
      isNumeric(row.precision) &&
      isNumeric(row.recall) &&
      isNumeric(row.f1_score)
    )
    .map(([label, row]) => ({
      class: label,
      precision: toPercent(row.precision),
      recall: toPercent(row.recall),
      f1_score: toPercent(row.f1_score),
    }));
}

export default function ModelPerformanceAnalytics() {
  const [catalog, setCatalog] = useState(null);
  const [selectedName, setSelectedName] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let active = true;

    getModelMetrics()
      .then((payload) => {
        if (!active) return;
        setCatalog(payload);
        setLoadError("");
      })
      .catch((error) => {
        if (!active) return;
        setLoadError(
          error?.response?.data?.message ||
          "Saved model evaluation results could not be loaded."
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const development = catalog?.evaluations?.development_oof || null;
  const developmentModels = Array.isArray(development?.models)
    ? development.models
    : EMPTY_MODELS;

  const selectedModel = useMemo(
    () =>
      developmentModels.find((model) => model.model === selectedName) ||
      developmentModels.find((model) => model.selected_model) ||
      developmentModels[0] ||
      null,
    [developmentModels, selectedName]
  );

  const finalEvaluation = catalog?.evaluations?.final_test || null;
  const finalModels = Array.isArray(finalEvaluation?.models)
    ? finalEvaluation.models
    : EMPTY_MODELS;

  // Stage 10 must contain only genuine independent held-out results.
  const heldOutModel = useMemo(
    () => finalModels.find((model) => model.model === selectedModel?.model) || null,
    [finalModels, selectedModel]
  );
  const stage10IsHeldOut = Boolean(heldOutModel);
  const stage10Samples = stage10IsHeldOut
    ? Number(finalEvaluation?.samples || heldOutModel.test_samples || 0)
    : 0;

  const accuracyData = developmentModels.map((model) => ({
    model: model.model,
    accuracy: toPercent(model.accuracy),
  })).filter((model) => model.accuracy !== null);

  const radarData = selectedModel
    ? [
        { metric: "Accuracy", [selectedModel.model]: toPercent(selectedModel.accuracy) },
        { metric: "Precision", [selectedModel.model]: toPercent(selectedModel.precision) },
        { metric: "Recall", [selectedModel.model]: toPercent(selectedModel.recall) },
        { metric: "F1 Score", [selectedModel.model]: toPercent(selectedModel.f1_score) },
      ].filter((row) => Number.isFinite(row[selectedModel.model]))
    : [];

  const reportRows = toReportRows(heldOutModel);
  const perClassRows = toPerClassRows(heldOutModel);
  const matrix = heldOutModel?.confusion_matrix || null;
  const benchmarkRows = Array.isArray(catalog?.inference_benchmark?.records)
    ? catalog.inference_benchmark.records
    : [];
  const benchmarkNote = catalog?.inference_benchmark?.message || null;

  const traditionalModels = developmentModels.filter((model) =>
    String(model.family || "").toLowerCase().includes("traditional")
  );
  const deepModels = developmentModels.filter((model) =>
    String(model.family || "").toLowerCase().includes("deep")
  );

  return (
    <section
      id="analytics"
      className="scroll-mt-20 rounded-lg border border-(color:--c-line) bg-(color:--c-panel) p-3 sm:p-4"
      aria-labelledby="analyticsTitle"
    >
      <div className="mb-4 flex items-center gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-(color:--c-primary) text-white">
          <ChartNoAxesColumnIncreasing className="h-5 w-5" />
        </span>
        <div className="min-w-0">
          <h2 id="analyticsTitle" className="text-lg font-semibold leading-tight">
            Model Performance Analytics
          </h2>
          <p className="text-sm text-(color:--c-muted)">
            Stage 9 development comparison and Stage 10 independent final test results.
          </p>
        </div>
      </div>

      {loading && (
        <p className="mb-3 rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3 text-sm text-(color:--c-muted)" role="status">
          Loading Stage 9 and Stage 10 evaluation results…
        </p>
      )}

      {loadError && (
        <p className="mb-3 rounded-lg border border-rose-400/30 bg-rose-950/30 p-3 text-sm text-rose-200" role="alert">
          {loadError}
        </p>
      )}

      {!loading && !loadError && !developmentModels.length && (
        <p className="mb-3 rounded-lg border border-dashed border-(color:--c-line) p-3 text-sm text-(color:--c-muted)">
          Stage 9 development OOF model comparison data is unavailable.
        </p>
      )}

      {/* STAGE 9 — development-only, 5-fold OOF comparison */}
      <section className="rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/45 p-3 sm:p-4" aria-labelledby="stage9-title">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 id="stage9-title" className="text-base font-semibold text-(color:--c-fg)">
              Stage 9 — Development Out-of-Fold Comparison
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-(color:--c-muted)">
              {Number(development?.samples || 6387).toLocaleString()} development records · {development?.folds || 5}-fold OOF · test set not used
            </p>
          </div>

          <label className="flex min-w-0 flex-col gap-1.5 sm:w-[260px]">
            <span className="text-xs font-semibold text-(color:--c-muted)">Selected comparison model</span>
            <select
              value={selectedModel?.model || ""}
              onChange={(event) => setSelectedName(event.target.value)}
              disabled={loading || !developmentModels.length}
              className="h-10 w-full min-w-0 rounded-lg border border-(color:--c-line) bg-(color:--c-panel) px-3 text-sm text-(color:--c-fg) outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-60"
            >
              {traditionalModels.length > 0 && (
                <optgroup label="Traditional ML">
                  {traditionalModels.map((model) => (
                    <option key={model.model} value={model.model}>{model.model}</option>
                  ))}
                </optgroup>
              )}
              {deepModels.length > 0 && (
                <optgroup label="Deep Learning">
                  {deepModels.map((model) => (
                    <option key={model.model} value={model.model}>{model.model}</option>
                  ))}
                </optgroup>
              )}
            </select>
          </label>
        </div>

        {selectedModel ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard icon={Activity} label="OOF Accuracy" value={percent(selectedModel.accuracy)} tone="green" note="Stage 9 · development OOF" />
              <StatCard icon={Crosshair} label="Macro Precision" value={percent(selectedModel.precision)} tone="sky" note="Stage 9 · development OOF" />
              <StatCard icon={ChartNoAxesColumnIncreasing} label="Macro Recall" value={percent(selectedModel.recall)} tone="violet" note="Stage 9 · development OOF" />
              <StatCard icon={Award} label="Macro F1 Score" value={percent(selectedModel.f1_score)} tone="orange" note="Stage 9 · development OOF" />
            </div>

            <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-2">
              <ModelAccuracyChart data={accuracyData} selectedModel={selectedModel.model} />
              <MetricsComparisonChart data={developmentModels} />
              <ModelComparisonRadarChart data={radarData} models={[selectedModel.model]} />
              <ConfusionMatrixChart
                data={selectedModel.confusion_matrix || null}
                title={`${selectedModel.model} ? Stage 9 OOF Confusion Matrix`}
              />
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-(color:--c-muted)">
              Accuracy and macro metrics above are development OOF results. They are not final held-out test scores or live-document confidence values.
            </p>
          </>
        ) : (
          <p className="text-sm text-(color:--c-muted)">No selectable Stage 9 models were returned.</p>
        )}
      </section>

      {/* STAGE 10 — final independent held-out evaluation */}
      <section className="mt-4 rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/45 p-3 sm:p-4" aria-labelledby="stage10-title">
        <div className="mb-3">
          <h3 id="stage10-title" className="text-base font-semibold text-(color:--c-fg)">
            Stage 10 — Final Held-Out Test Evaluation
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-(color:--c-muted)">
            {selectedModel?.model || "No model selected"}
            {stage10IsHeldOut
              ? ` · ${stage10Samples.toLocaleString()} independent test records · ${Number(heldOutModel.correct_predictions || 0).toLocaleString()} correct · ${Number(heldOutModel.incorrect_predictions || 0).toLocaleString()} incorrect`
              : " · no independent held-out test evaluation is available for this model"}
          </p>
        </div>

        {stage10IsHeldOut ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard
                icon={Activity}
                label="Test Accuracy"
                value={percent(heldOutModel.accuracy)}
                tone="green"
                note="Stage 10 · independent held-out test"
              />
              <StatCard
                icon={Crosshair}
                label="Macro Precision"
                value={percent(heldOutModel.precision)}
                tone="sky"
                note="Stage 10 · independent held-out test"
              />
              <StatCard
                icon={ChartNoAxesColumnIncreasing}
                label="Macro Recall"
                value={percent(heldOutModel.recall)}
                tone="violet"
                note="Stage 10 · independent held-out test"
              />
              <StatCard
                icon={Award}
                label="Macro F1 Score"
                value={percent(heldOutModel.f1_score)}
                tone="orange"
                note="Stage 10 · independent held-out test"
              />
            </div>

            <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-2">
              <ConfusionMatrixChart data={matrix} />
              <PerClassPerformanceChart data={perClassRows} />
            </div>

            <ClassificationReport
              title="Stage 10 — EfficientNetB0 Classification Report"
              rows={reportRows.length ? reportRows : null}
            />

            <p className="mt-2 text-[11px] leading-relaxed text-(color:--c-muted)">
              Confusion-matrix order: Form, Invoice, Resume. Rows are actual classes; columns are predicted classes. These results belong only to the Stage 10 EfficientNetB0 held-out test set.
            </p>
          </>
        ) : (
          <p className="rounded-lg border border-dashed border-(color:--c-line) p-4 text-sm leading-relaxed text-(color:--c-muted)">
            {selectedModel?.model || "The selected model"} has no independent Stage 10 held-out test evaluation in the saved metrics. Stage 9 development OOF scores are shown only in the comparison section above; they are not reused as test results. The supplied Stage 10 confusion matrix and classification report are available only for EfficientNetB0.
          </p>
        )}

        <div className="mt-3">
          <InferenceTimeChart
            data={benchmarkRows}
            selectedModel={selectedModel?.model}
            note={benchmarkNote}
          />
        </div>
      </section>
    </section>
  );
}
