import React from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Cell,
} from "recharts";

import ChartCard from "../Common/ChartCard.jsx";

const COLORS = {
  blue: "#4da3ff",
  violet: "#a78bfa",
  green: "#34d399",
  orange: "#fb923c",
};

const GRID = "#0a2f57";
const TICK = { fontSize: 10, fill: "#8fb0d8" };

const legendFormatter = (value) => (
  <span className="text-[11px] text-[#b8cce5]">{value}</span>
);

function percent(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") {
    return null;
  }
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  const percentage = number <= 1 ? number * 100 : number;
  return percentage >= 0 && percentage <= 100 ? percentage : null;
}

function shortModel(name) {
  const labels = {
    EfficientNetB0: "EffNetB0",
    MobileNetV2: "MobileNetV2",
    ResNet18: "ResNet18",
    "Logistic Regression": "Logistic Reg.",
    "Gaussian NB": "Gaussian NB",
    "Linear SVM": "Linear SVM",
  };
  return labels[name] || name;
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="max-w-[250px] rounded-lg border border-[#14518a] bg-[#00203e] px-3 py-2 text-[#e6f0ff] shadow-xl shadow-black/30">
      {label !== undefined ? (
        <p className="mb-2 break-words text-xs font-semibold text-[#b8cce5]">{label}</p>
      ) : null}
      <div className="grid gap-1.5">
        {payload.map((item, index) => (
          <div key={`${item.dataKey || item.name || "metric"}-${index}`} className="flex items-center justify-between gap-4 text-xs">
            <span className="text-[#a9c2df]">{item.name || item.dataKey}</span>
            <span className="font-semibold tabular-nums text-white">
              {Number.isFinite(Number(item.value))
                ? `${Number(item.value).toFixed(2)}${String(item.dataKey || "").toLowerCase().includes("time") || item.dataKey === "measuredTime" ? " ms" : "%"}`
                : "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Frame({ children }) {
  return (
    <div className="h-full min-h-0 min-w-0 w-full">
      <ResponsiveContainer width="100%" height="100%">
        {children}
      </ResponsiveContainer>
    </div>
  );
}

export function ModelAccuracyChart({ data = [], selectedModel = null }) {
  const rows = (Array.isArray(data) ? data : [])
    .map((item) => ({ ...item, accuracy: percent(item.accuracy) }))
    .filter((item) => item.accuracy !== null);

  return (
    <ChartCard
      title="1. Stage 9 — Accuracy Comparison (7 Models)"
      empty={!rows.length}
      message="Development OOF model results unavailable."
      description="The Stage 9 out-of-fold comparison has not been loaded."
    >
      <Frame>
        <BarChart data={rows} margin={{ top: 12, right: 10, bottom: 54, left: 2 }} barCategoryGap="20%">
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis
            dataKey="model"
            interval={0}
            tick={TICK}
            tickFormatter={shortModel}
            angle={-25}
            textAnchor="end"
            height={62}
            tickMargin={10}
            axisLine={{ stroke: "#315474" }}
            tickLine={false}
          />
          <YAxis domain={[0, 100]} ticks={[0, 20, 40, 60, 80, 100]} tick={TICK} width={36} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(77,163,255,0.08)" }} />
          <Bar dataKey="accuracy" name="OOF Accuracy" radius={[4, 4, 0, 0]} maxBarSize={38}>
            {rows.map((row) => (
              <Cell key={row.model} fill={row.model === selectedModel ? COLORS.green : COLORS.blue} />
            ))}
          </Bar>
        </BarChart>
      </Frame>
    </ChartCard>
  );
}

export function MetricsComparisonChart({ data = [] }) {
  const rows = (Array.isArray(data) ? data : []).map((item) => ({
    ...item,
    precision: percent(item.precision),
    recall: percent(item.recall),
    f1_score: percent(item.f1_score),
  }));

  return (
    <ChartCard
      title="2. Stage 9 — Precision, Recall & F1"
      empty={!rows.length}
      message="Development OOF metrics unavailable."
      description="Macro-average precision, recall and F1 for each Stage 9 model."
    >
      <Frame>
        <BarChart data={rows} margin={{ top: 12, right: 8, bottom: 52, left: 0 }} barCategoryGap="18%">
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis
            dataKey="model"
            interval={0}
            tick={TICK}
            tickFormatter={shortModel}
            angle={-25}
            textAnchor="end"
            height={62}
            tickMargin={10}
            axisLine={{ stroke: "#315474" }}
            tickLine={false}
          />
          <YAxis domain={[0, 100]} ticks={[0, 20, 40, 60, 80, 100]} tick={TICK} width={36} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} />
          <Legend verticalAlign="top" height={26} formatter={legendFormatter} />
          <Bar dataKey="precision" name="Macro Precision" fill={COLORS.blue} maxBarSize={18} />
          <Bar dataKey="recall" name="Macro Recall" fill={COLORS.violet} maxBarSize={18} />
          <Bar dataKey="f1_score" name="Macro F1" fill={COLORS.green} maxBarSize={18} />
        </BarChart>
      </Frame>
    </ChartCard>
  );
}

export function ConfusionMatrixChart({ data = null, title = "3. Stage 10 — Confusion Matrix (Held-Out Test)" }) {
  const labels = data?.labels || [];
  const matrix = data?.rows || data?.matrix || [];
  const valid = Array.isArray(labels) && labels.length > 0 &&
    Array.isArray(matrix) && matrix.length === labels.length &&
    matrix.every((row) => Array.isArray(row) && row.length === labels.length);
  const peak = valid ? Math.max(1, ...matrix.flat().map((v) => Number(v) || 0)) : 1;

  return (
    <ChartCard
      title={title}
      empty={!valid}
      icon="grid"
      message="Confusion matrix unavailable."
        description={data?.sample_count
          ? `${Number(data.sample_count).toLocaleString()} records — ${data.evaluation || "evaluation split"} — rows: actual; columns: predicted.`
          : "No validated confusion matrix was provided."}
    >
      <div className="flex h-full min-h-0 w-full items-center justify-center overflow-auto">
        <div className="grid w-full max-w-[430px] gap-1.5" style={{ gridTemplateColumns: `minmax(58px,.85fr) repeat(${labels.length},minmax(0,1fr))` }}>
          <span className="flex items-center justify-center text-center text-[9px] leading-tight text-[#8fb0d8]">Actual (rows) / Predicted (columns)</span>
          {labels.map((label) => (
            <span key={`column-${label}`} className="flex min-h-8 items-center justify-center break-words px-1 text-center text-[10px] font-medium capitalize text-[#a9c2df]">
              {label}
            </span>
          ))}
          {matrix.map((row, rowIndex) => (
            <React.Fragment key={`row-${labels[rowIndex] || rowIndex}`}>
              <span className="flex items-center break-words pr-1 text-[10px] font-medium capitalize text-[#a9c2df]">{labels[rowIndex]}</span>
              {row.map((raw, colIndex) => {
                const value = Number(raw);
                const alpha = Math.min(0.88, 0.12 + ((Number.isFinite(value) ? value : 0) / peak) * 0.72);
                return (
                  <span
                    key={`${rowIndex}-${colIndex}`}
                    title={`${labels[rowIndex]} predicted as ${labels[colIndex]}: ${value}`}
                    className="grid min-h-12 min-w-0 place-items-center rounded-md border border-[#164b7b]/50 text-xs font-semibold tabular-nums text-white"
                    style={{ backgroundColor: `rgba(77,163,255,${alpha})` }}
                  >
                    {Number.isFinite(value) ? value : "—"}
                  </span>
                );
              })}
            </React.Fragment>
          ))}
        </div>
      </div>
    </ChartCard>
  );
}

export function PerClassPerformanceChart({ data = [] }) {
  const rows = (Array.isArray(data) ? data : []).map((item) => ({
    ...item,
    precision: percent(item.precision),
    recall: percent(item.recall),
    f1_score: percent(item.f1_score),
  }));

  return (
    <ChartCard
      title="4. Stage 10 — Per-Class Performance"
      empty={!rows.length}
      message="Per-class metrics unavailable."
      description="No per-class results were included in the held-out report."
    >
      <Frame>
        <BarChart data={rows} margin={{ top: 10, right: 8, bottom: 12, left: 0 }} barCategoryGap="25%">
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="class" tick={{ fontSize: 10, fill: "#8fb0d8" }} tickFormatter={(v) => String(v).charAt(0).toUpperCase() + String(v).slice(1)} />
          <YAxis domain={[0, 100]} ticks={[0, 20, 40, 60, 80, 100]} tick={TICK} width={36} axisLine={false} tickLine={false} />
          <Tooltip content={<ChartTooltip />} />
          <Legend verticalAlign="top" height={26} formatter={legendFormatter} />
          <Bar dataKey="precision" name="Precision" fill={COLORS.blue} maxBarSize={24} />
          <Bar dataKey="recall" name="Recall" fill={COLORS.violet} maxBarSize={24} />
          <Bar dataKey="f1_score" name="F1 Score" fill={COLORS.green} maxBarSize={24} />
        </BarChart>
      </Frame>
    </ChartCard>
  );
}

export function ModelComparisonRadarChart({ data = [], models = [] }) {
  const rows = Array.isArray(data) ? data : [];
  const keys = models.length ? models : Object.keys(rows[0] || {}).filter((key) => key !== "metric");
  const valid = rows.length > 0 && keys.length > 0;

  return (
    <ChartCard
      title="5. Selected Model — OOF Metric Profile"
      empty={!valid}
      icon="pent"
      message="OOF profile unavailable."
      description="The selected Stage 9 model has no complete aggregate metric set."
    >
      <Frame>
        <RadarChart data={rows} outerRadius="70%" margin={{ top: 16, right: 24, bottom: 12, left: 24 }}>
          <PolarGrid stroke={GRID} />
          <PolarAngleAxis dataKey="metric" tick={{ fontSize: 10, fill: "#8fb0d8" }} />
          <PolarRadiusAxis domain={[0, 100]} ticks={[20, 40, 60, 80, 100]} tick={{ fontSize: 8, fill: "#8fb0d8" }} axisLine={false} />
          <Tooltip content={<ChartTooltip />} />
          {keys.map((key, index) => {
            const color = [COLORS.blue, COLORS.violet, COLORS.green][index % 3];
            return <Radar key={key} name={key} dataKey={key} stroke={color} fill={color} strokeWidth={2} fillOpacity={0.14} />;
          })}
        </RadarChart>
      </Frame>
    </ChartCard>
  );
}

export function InferenceTimeChart({ data = [], selectedModel = null, note = null }) {
  const rows = Array.isArray(data)
    ? data
        .map((item) => {
          const rawValue = item?.inference_time_ms ?? item?.inference_time;
          return {
            ...item,
            inference_time_ms: (
              rawValue !== null &&
              rawValue !== undefined &&
              rawValue !== "" &&
              typeof rawValue !== "boolean" &&
              Number.isFinite(Number(rawValue))
            ) ? Number(rawValue) : null,
          };
        })
        .filter((item) => item.model && item.inference_time_ms !== null && item.inference_time_ms >= 0)
    : [];
  const measured = rows.length > 0;

  return (
    <ChartCard
      title="6. Observed Runtime — Not a Controlled Benchmark"
      empty={!measured}
      icon="bars"
      message="Runtime observations unavailable."
      description={
        note ||
        "No valid stored inference-time observations were found. OCR time and total document-processing time are not model-inference timings."
      }
    >
      <div className="h-full min-h-0 w-full">
        <Frame>
          <BarChart data={rows} layout="vertical" margin={{ top: 12, right: 18, bottom: 12, left: 8 }}>
            <CartesianGrid stroke={GRID} horizontal={false} />
            <XAxis type="number" tick={TICK} tickFormatter={(v) => `${Number(v).toFixed(0)} ms`} />
            <YAxis type="category" dataKey="model" width={96} tick={TICK} />
            <Tooltip content={<ChartTooltip />} />
            <Bar dataKey="inference_time_ms" name="Observed model runtime (ms)" radius={[0, 4, 4, 0]} maxBarSize={28}>
              {rows.map((row) => (
                <Cell key={row.model} fill={row.model === selectedModel ? COLORS.green : COLORS.blue} />
              ))}
            </Bar>
          </BarChart>
        </Frame>
      </div>
    </ChartCard>
  );
}
