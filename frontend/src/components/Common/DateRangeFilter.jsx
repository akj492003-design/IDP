import { useState } from "react";
import {
  DATE_RANGE_OPTIONS,
  getDateRangeLabel,
} from "../../utils/dateRange.js";

const controlClass =
  "h-10 min-w-0 rounded-lg border border-[#1c48a0] bg-[#071d5c] px-3 text-sm text-white outline-none transition hover:border-[#3f83ff] focus:border-[#3f83ff] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#7fb0ff] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:text-[#203653]";

function CustomDateRangeInputs({ selection, idPrefix, onApply }) {
  const [draftFrom, setDraftFrom] = useState(selection.from || "");
  const [draftTo, setDraftTo] = useState(selection.to || "");
  const [customError, setCustomError] = useState("");

  const applyCustomRange = () => {
    if (!draftFrom || !draftTo) {
      setCustomError("Choose both From and To dates.");
      return;
    }

    if (draftFrom > draftTo) {
      setCustomError("From date cannot be later than To date.");
      return;
    }

    onApply({ from: draftFrom, to: draftTo });
    setCustomError("");
  };

  return (
    <>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor={`${idPrefix}-from`}>
          From date
        </label>
        <input
          id={`${idPrefix}-from`}
          type="date"
          value={draftFrom}
          max={draftTo || undefined}
          onChange={(event) => {
            setDraftFrom(event.target.value);
            setCustomError("");
          }}
          className={`${controlClass} min-w-[135px] flex-1`}
        />

        <label className="sr-only" htmlFor={`${idPrefix}-to`}>
          To date
        </label>
        <input
          id={`${idPrefix}-to`}
          type="date"
          value={draftTo}
          min={draftFrom || undefined}
          onChange={(event) => {
            setDraftTo(event.target.value);
            setCustomError("");
          }}
          className={`${controlClass} min-w-[135px] flex-1`}
        />

        <button
          type="button"
          onClick={applyCustomRange}
          className="h-10 rounded-lg border border-blue-400/50 bg-[#1457c8] px-3 text-sm font-semibold text-white transition hover:bg-[#1d6bff] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-300"
        >
          Apply
        </button>
      </div>
      {customError && (
        <p role="alert" className="basis-full text-xs text-rose-400">
          {customError}
        </p>
      )}
    </>
  );
}

export default function DateRangeFilter({
  value,
  onChange,
  idPrefix = "date-range",
  compact = false,
}) {
  const selection = value || {
    preset: "all_time",
    from: "",
    to: "",
  };

  const handlePresetChange = (event) => {
    const preset = event.target.value;

    if (preset === "custom") {
      onChange({
        preset: "custom",
        from: selection.from || "",
        to: selection.to || "",
      });
      return;
    }

    onChange({ preset, from: "", to: "" });
  };

  return (
    <div className={`flex min-w-0 flex-wrap items-center gap-2 ${compact ? "" : "w-full"}`}>
      <label className="sr-only" htmlFor={`${idPrefix}-preset`}>
        Filter by date range
      </label>

      <select
        id={`${idPrefix}-preset`}
        value={selection.preset || "all_time"}
        onChange={handlePresetChange}
        className={`${controlClass} min-w-[145px] flex-1 sm:flex-none`}
        aria-label="Filter by date range"
      >
        {DATE_RANGE_OPTIONS.map((option) => (
          <option
            key={option.value}
            value={option.value}
            className="bg-[#071d5c] text-white [.light_&]:bg-white [.light_&]:text-[#203653]"
          >
            {option.label}
          </option>
        ))}
      </select>

      {selection.preset === "custom" && (
        <CustomDateRangeInputs
          key={`${selection.from || ""}:${selection.to || ""}`}
          selection={selection}
          idPrefix={idPrefix}
          onApply={(range) => onChange({ preset: "custom", ...range })}
        />
      )}

      <span className="sr-only" aria-live="polite">
        Active date filter: {getDateRangeLabel(selection)}
      </span>
    </div>
  );
}
