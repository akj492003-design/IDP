import EmptyState from "./EmptyState.jsx";

export default function ChartCard({
  title,
  children,
  empty = false,
  icon = "bars",
  message = "No evaluation data yet.",
  description = "Valid evaluation data will appear here.",
}) {
  return (
    <article className="flex h-full min-w-0 flex-col overflow-hidden rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3">
      <h3 className="mb-2 min-h-5 shrink-0 text-[13px] font-semibold leading-5 text-(color:--c-fg)">
        {title}
      </h3>
      {empty ? (
        <div className="flex min-h-[245px] flex-1 items-center justify-center rounded-md px-2 text-center">
          <EmptyState icon={icon} title={message} description={description} compact />
        </div>
      ) : (
        <div className="h-[245px] min-h-[245px] min-w-0 w-full">
          {children}
        </div>
      )}
    </article>
  );
}
