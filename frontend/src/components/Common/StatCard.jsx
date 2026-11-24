export default function StatCard({
  icon: Icon,
  label,
  value = "—",
  tone = "blue",
  note = null,
}) {
  const tones = {
    blue: "bg-(color:--c-secondary) text-[#bcd7ff]",
    green: "bg-[#034a45] text-emerald-300",
    red: "bg-[#5a2230] text-red-300",
    violet: "bg-[#2c1a78] text-violet-300",
    sky: "bg-[#003a7e] text-sky-300",
    orange: "bg-[#5a3a22] text-orange-300",
  };

  return (
    <article className="flex min-w-0 items-center gap-3 rounded-lg border border-(color:--c-line) bg-(color:--c-panel) p-3.5 transition duration-300 hover:-translate-y-0.5 hover:border-(color:--c-accent)/60 sm:p-4">
      <span className={`grid h-12 w-12 shrink-0 place-items-center rounded-lg sm:h-14 sm:w-14 ${tones[tone] || tones.blue}`}>
        {Icon ? <Icon className="h-6 w-6 sm:h-7 sm:w-7" strokeWidth={1.8} /> : null}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs text-(color:--c-muted)">{label}</p>
        <p className="mt-1 text-2xl font-bold tabular-nums text-(color:--c-fg)">{value}</p>
        {note ? <p className="mt-1 text-[10px] leading-snug text-(color:--c-muted)">{note}</p> : null}
      </div>
    </article>
  );
}
