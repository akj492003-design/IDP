const pad = (value) => String(value).padStart(2, "0");

function localDateParts(date) {
  return {
    year: date.getFullYear(),
    month: date.getMonth(),
    day: date.getDate(),
  };
}

function localMidnight(year, monthIndex, day) {
  return new Date(year, monthIndex, day, 0, 0, 0, 0);
}

function validDateString(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) {
    return false;
  }

  const [year, month, day] = value.split("-").map(Number);
  const parsed = new Date(year, month - 1, day);

  return (
    parsed.getFullYear() === year &&
    parsed.getMonth() === month - 1 &&
    parsed.getDate() === day
  );
}

export const DATE_RANGE_OPTIONS = [
  { value: "all_time", label: "All time" },
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "last_7_days", label: "Last 7 days" },
  { value: "last_14_days", label: "Last 14 days" },
  { value: "last_30_days", label: "Last 30 days" },
  { value: "last_90_days", label: "Last 90 days" },
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
  { value: "custom", label: "Custom range" },
];

export const DEFAULT_DATE_RANGE = {
  preset: "all_time",
  from: "",
  to: "",
};

export function getLocalDateString(date = new Date()) {
  const { year, month, day } = localDateParts(date);

  return `${year}-${pad(month + 1)}-${pad(day)}`;
}

/**
 * Creates date_from inclusive and date_to exclusive ISO timestamps.
 * The boundaries are local calendar midnights converted to UTC.
 */
export function createDateRangeParams(
  selection = DEFAULT_DATE_RANGE,
  now = new Date(),
) {
  const preset = selection?.preset || "all_time";

  if (preset === "all_time") {
    return {};
  }
  // A custom range remains unfiltered until both dates are selected.
  if (preset === "custom" && (!selection?.from || !selection?.to)) {
    return {};
  }

  const today = localDateParts(now);

  let start;
  let end;

  if (preset === "custom") {
    if (
      !validDateString(selection?.from) ||
      !validDateString(selection?.to)
    ) {
      throw new Error("Choose valid From and To dates.");
    }

    const [fromYear, fromMonth, fromDay] = selection.from.split("-").map(Number);
    const [toYear, toMonth, toDay] = selection.to.split("-").map(Number);

    start = localMidnight(fromYear, fromMonth - 1, fromDay);
    end = localMidnight(toYear, toMonth - 1, toDay + 1);
  } else if (preset === "today") {
    start = localMidnight(today.year, today.month, today.day);
    end = localMidnight(today.year, today.month, today.day + 1);
  } else if (preset === "yesterday") {
    start = localMidnight(today.year, today.month, today.day - 1);
    end = localMidnight(today.year, today.month, today.day);
  } else if (
    preset === "last_7_days" ||
    preset === "last_14_days" ||
    preset === "last_30_days" ||
    preset === "last_90_days"
  ) {
    const days = Number(preset.split("_")[1]);

    start = localMidnight(
      today.year,
      today.month,
      today.day - days + 1,
    );

    end = localMidnight(
      today.year,
      today.month,
      today.day + 1,
    );
  } else if (preset === "this_month") {
    start = localMidnight(today.year, today.month, 1);
    end = localMidnight(today.year, today.month, today.day + 1);
  } else if (preset === "last_month") {
    start = localMidnight(today.year, today.month - 1, 1);
    end = localMidnight(today.year, today.month, 1);
  } else {
    throw new Error("Unsupported date range selection.");
  }

  if (end.getTime() <= start.getTime()) {
    throw new Error("The selected date range is invalid.");
  }

  return {
    date_from: start.toISOString(),
    date_to: end.toISOString(),
  };
}

export function getDateRangeLabel(selection = DEFAULT_DATE_RANGE) {
  const found = DATE_RANGE_OPTIONS.find(
    (option) => option.value === selection?.preset,
  );

  if (selection?.preset === "custom" && selection.from && selection.to) {
    return `${selection.from} to ${selection.to}`;
  }

  return found?.label || "All time";
}
