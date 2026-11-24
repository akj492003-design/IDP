import Icon from './Icon.jsx';
import { STATUS_CLASS, TYPES } from '../../Data/historyData.js';

const formatAuditValue = (value) => {
  if (value === null || value === undefined || value === "") return "—";

  let formatted;

  try {
    formatted = typeof value === "string"
      ? value
      : JSON.stringify(value, null, 2);
  } catch {
    formatted = String(value);
  }

  return String(formatted || "—").length > 1500
    ? `${String(formatted).slice(0, 1500)}…`
    : String(formatted || "—");
};

export default function EventDetails({ event, onClose }) {
  const type = event ? (TYPES[event.type] || { icon: "doc", color: "#4da3ff" }) : null;
  const statusClass = event ? (STATUS_CLASS[event.status] || "bg-slate-600") : "";

  return (
    <aside className="flex min-w-0 flex-col rounded-[14px] border border-[#12358f] bg-linear-to-b from-[rgba(8,30,88,.78)] to-[rgba(4,18,60,.92)] shadow [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:text-[#203653] [.light_&]:shadow-[0_5px_18px_rgba(15,35,70,.06)]" aria-label="Event Details">
      <div className="flex h-[54px] items-center justify-between border-b border-[#0d2870] px-3.5 sm:px-5">
        <h2 className="text-xl font-bold sm:text-[23px]">Event Details</h2>
        <button type="button" onClick={onClose} aria-label="Close event details" className="grid h-8 w-8 place-items-center rounded-md transition-colors hover:bg-[rgba(27,116,255,.22)] focus-visible:outline-2 focus-visible:outline-[#7fb0ff]"><Icon name="close" className="h-5 w-5" strokeWidth={2}/></button>
      </div>
      {!event ? <p className="mx-5 my-auto py-9 text-center text-base lg:py-[60px]">Select an event from the timeline to view its details.</p> : <div className="m-4 rounded-xl border border-[#12358f] bg-[rgba(10,38,112,.45)] p-4 shadow [.light_&]:border-[#d8e1ee] [.light_&]:bg-white [.light_&]:shadow-[0_3px_12px_rgba(15,35,70,.05)] transition-shadow sm:m-5">
        <div className="flex items-start gap-3.5">
          <span className="grid h-11 w-11 flex-none place-items-center rounded-full text-white" style={{ background: type.color }}><Icon name={type.icon} className="h-[22px] w-[22px]"/></span>
          <div className="min-w-0"><b className="block text-lg font-bold leading-6">{event.type}</b><span className="block break-words text-[15px] text-[#e3ebff] [.light_&]:text-[#203653]">{event.file}</span><p className="mt-1.5 text-sm text-[#c3d0f0] [.light_&]:text-[#526782]">{event.desc}</p></div>
        </div>
        <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[15px]">
          <dt className="text-[#c3d0f0] [.light_&]:text-[#526782]">Status:</dt><dd><span className={`inline-flex h-6 items-center rounded-[13px] px-3 text-[13px] ${statusClass}`}>{event.status}</span></dd>
          <dt className="text-[#c3d0f0] [.light_&]:text-[#526782]">User:</dt><dd className="break-words">{event.user}</dd>
          <dt className="text-[#c3d0f0] [.light_&]:text-[#526782]">Timestamp:</dt><dd className="break-words">{event.ts}</dd>
          <dt className="text-[#c3d0f0] [.light_&]:text-[#526782]">Event ID:</dt><dd>{event.id}</dd>
          <dt className="text-[#c3d0f0] [.light_&]:text-[#526782]">Document ID:</dt><dd>{event.docId}</dd>
        </dl>

        {Array.isArray(event.changes) && event.changes.length > 0 && (
          <section className="mt-4 border-t border-blue-400/20 pt-3">
            <h3 className="mb-2 text-sm font-semibold">
              Before-and-after changes ({event.changes.length})
            </h3>

            <div className="space-y-3">
              {event.changes.map((change, index) => (
                <article
                  key={`${change.field || "field"}-${index}`}
                  className="rounded-md border border-blue-400/20 bg-[#071b55]/40 p-2.5 [.light_&]:bg-[#f6f9ff]"
                >
                  <p className="mb-2 break-words text-xs font-semibold">
                    {String(change.field || "Updated field").replaceAll("_", " ")}
                  </p>

                  <div className="grid gap-2 sm:grid-cols-2">
                    <div className="min-w-0">
                      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[#c3d0f0] [.light_&]:text-[#526782]">
                        Before
                      </p>
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-black/15 p-2 text-[11px] [.light_&]:bg-slate-100">
                        {formatAuditValue(change.before)}
                      </pre>
                    </div>

                    <div className="min-w-0">
                      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-[#c3d0f0] [.light_&]:text-[#526782]">
                        After
                      </p>
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-black/15 p-2 text-[11px] [.light_&]:bg-slate-100">
                        {formatAuditValue(change.after)}
                      </pre>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}
      </div>}
    </aside>
  );
}
