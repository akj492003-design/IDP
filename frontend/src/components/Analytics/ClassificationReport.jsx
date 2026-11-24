import Icon from "../Common/Icon.jsx";

export default function ClassificationReport({
  rows = null,
  title = "Classification Report",
}) {
  const valid = Array.isArray(rows) && rows.length > 0;

  return (
    <article className="mt-3 rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3">
      <h3 className="mb-2 text-sm font-semibold text-(color:--c-fg)">{title}</h3>
      <div className="overflow-x-auto rounded-md border border-(color:--c-line)">
        <table className="w-full min-w-[560px] text-left text-xs">
          <thead className="border-b border-(color:--c-line) text-(color:--c-muted)">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Class</th>
              <th scope="col" className="px-3 py-2 text-center font-medium">Precision</th>
              <th scope="col" className="px-3 py-2 text-center font-medium">Recall</th>
              <th scope="col" className="px-3 py-2 text-center font-medium">F1 Score</th>
              <th scope="col" className="px-3 py-2 text-center font-medium">Support</th>
            </tr>
          </thead>
          <tbody>
            {valid ? rows.map((row) => (
              <tr key={row.class} className="border-b border-(color:--c-line) last:border-0">
                <td className="px-3 py-2 capitalize">{row.class}</td>
                <td className="px-3 py-2 text-center tabular-nums">{row.precision ?? "—"}</td>
                <td className="px-3 py-2 text-center tabular-nums">{row.recall ?? "—"}</td>
                <td className="px-3 py-2 text-center tabular-nums">{row.f1_score ?? "—"}</td>
                <td className="px-3 py-2 text-center tabular-nums">{row.support ?? "—"}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan="5" className="px-3 py-5">
                  <div className="flex flex-col items-center gap-1 text-center text-(color:--c-muted)">
                    <Icon name="doc" className="h-7 w-7 text-(color:--c-accent)" />
                    <p className="font-semibold text-(color:--c-accent)">Classification report unavailable.</p>
                    <p>No per-class evaluation report was supplied for this evaluation.</p>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </article>
  );
}
