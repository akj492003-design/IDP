import { useSearchParams } from "react-router-dom";
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import DateRangeFilter from "../Common/DateRangeFilter.jsx";

export default function ClassificationResults({
  results = [],
  dateRange,
  onDateRangeChange = () => {},
  onClearDateRange = () => {},
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedCategory = String(searchParams.get("type") || "").toLowerCase();

  const categoryFromUrl = ["invoice", "form", "resume", "unclassified"].includes(requestedCategory)
    ? `${requestedCategory.charAt(0).toUpperCase()}${requestedCategory.slice(1)}`
    : "All Categories";

  const [searchText, setSearchText] = useState("");
  const category = categoryFromUrl;
  const [status, setStatus] = useState("All Status");
  const navigate = useNavigate();

  const handleCategoryChange = (value) => {
    const next = new URLSearchParams(searchParams);

    if (value === "All Categories") {
      next.delete("type");
    } else {
      next.set("type", value.toLowerCase());
    }

    setSearchParams(next, { replace: true });
  };

  const clearFilters = useCallback(() => {
    setSearchText("");
    setStatus("All Status");
    const next = new URLSearchParams(searchParams);
    next.delete("type");
    setSearchParams(next, { replace: true });
    onClearDateRange();
  }, [onClearDateRange, searchParams, setSearchParams]);
  const filtered = useMemo(() => results.filter((row) => {
    const needle = searchText.trim().toLowerCase();
    const label = String(row.category || "unclassified").toLowerCase();
    const verified = row.classification_verification?.verified === true;

    return (!needle || `${row.name || ""} ${row.model || ""} ${label}`.toLowerCase().includes(needle)) &&
      (category === "All Categories" || label === category.toLowerCase()) &&
      (status === "All Status" || (status === "Verified" ? verified : !verified));
  }), [results, searchText, category, status]);
  return (
    <section className="flex flex-col rounded-[0.625rem] border border-[#1a3a9e] bg-[rgba(8,30,110,0.45)] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:text-[#203653] [padding:0.75rem_0.875rem_0.875rem] [box-shadow:inset_0_0_40px_rgba(20,70,200,0.12),0_0_0_1px_rgba(30,90,255,0.05)] [@media(min-width:1366px)_and_(max-width:1439px)]:[padding:0.625rem_0.875rem_0.75rem]">
      <div className="flex flex-wrap items-center gap-[0.875rem]"><div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.625rem] border border-[#2a5cff] bg-[linear-gradient(135deg,#1d4fd8,#143aa8)] leading-[0] text-[#dbe7ff]"><svg className="h-5 w-5 fill-current" aria-hidden="true"><use href="#i-scan" xlinkHref="#i-scan" /></svg></div><div className="min-w-[17.5rem]"><h2 className="text-[1rem] font-semibold">Recent Classification Results</h2><p className="mt-[0.2rem] text-[0.72rem] text-[#cdd9f7] [.light_&]:text-[#526782]">View the latest document classification results and their status.</p></div>
        <div className="ml-auto flex flex-wrap items-center gap-4 [@media(max-width:1279px)]:ml-0 [@media(max-width:1279px)]:w-full"><div className="flex h-8 w-[min(21.5rem,100%)] items-center gap-[0.625rem] rounded-[0.3rem] border border-[#1c46b8] bg-[#0a2a86] px-3 focus-within:border-[#3fb4ff] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [@media(max-width:900px)]:w-full"><svg className="h-4 w-4 shrink-0 fill-current text-[#b6c8f5] [.light_&]:text-[#526782]" aria-hidden="true"><use href="#i-search" xlinkHref="#i-search" /></svg><input type="text" aria-label="Search by filename or document text" placeholder="Search by filename or document text..." className="min-w-0 flex-1 border-0 bg-transparent text-[0.75rem] outline-0 placeholder:text-[#b6c8f5] [.light_&]:text-[#526782] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3fb4ff]" value={searchText} onChange={(event) => setSearchText(event.target.value)} /></div>
          <label className="relative block h-8 w-[8.5rem] rounded-[0.3rem] border border-[#1c46b8] bg-[#0a2a86] focus-within:border-[#3fb4ff] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [@media(max-width:640px)]:min-h-11 [@media(max-width:640px)]:flex-1 [@media(max-width:640px)]:basis-32"><select aria-label="Filter by category" value={category} onChange={(event) => handleCategoryChange(event.target.value)} className="h-full w-full cursor-pointer appearance-none border-0 bg-transparent [padding:0_1.875rem_0_0.75rem] text-[0.75rem] outline-0 [&_option]:bg-[#0a2a86] [&_option]:text-white [.light_&]:text-[#203653] [&_option]:[.light_&]:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3fb4ff]"><option>All Categories</option><option>Invoice</option><option>Form</option><option>Resume</option><option>Unclassified</option></select><svg className="pointer-events-none absolute top-1/2 right-[0.625rem] h-[0.875rem] w-[0.875rem] -translate-y-1/2 fill-current text-[#b6c8f5] [.light_&]:text-[#526782]" aria-hidden="true"><use href="#i-chevron" xlinkHref="#i-chevron" /></svg></label>
          <label className="relative block h-8 w-[8.125rem] rounded-[0.3rem] border border-[#1c46b8] bg-[#0a2a86] focus-within:border-[#3fb4ff] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [@media(max-width:640px)]:min-h-11 [@media(max-width:640px)]:flex-1 [@media(max-width:640px)]:basis-32"><select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)} className="h-full w-full cursor-pointer appearance-none border-0 bg-transparent [padding:0_1.875rem_0_0.75rem] text-[0.75rem] outline-0 [&_option]:bg-[#0a2a86] [&_option]:text-white [.light_&]:text-[#203653] [&_option]:[.light_&]:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3fb4ff]"><option>All Status</option><option>Verified</option><option>Verification Pending</option></select><svg className="pointer-events-none absolute top-1/2 right-[0.625rem] h-[0.875rem] w-[0.875rem] -translate-y-1/2 fill-current text-[#b6c8f5] [.light_&]:text-[#526782]" aria-hidden="true"><use href="#i-chevron" xlinkHref="#i-chevron" /></svg></label>
          <div className="min-w-[12rem] flex-none">
            <DateRangeFilter
              value={dateRange}
              onChange={onDateRangeChange}
              idPrefix="classification-date-range"
              compact
            />
          </div>
          <button type="button" onClick={clearFilters} className="inline-flex h-8 cursor-pointer items-center justify-center gap-2 rounded-[0.3rem] border border-[#2a63e0] bg-[#0a2a86] px-[0.875rem] text-[0.75rem] text-white transition-colors duration-200 hover:bg-[#12399f] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#3fb4ff] [@media(max-width:640px)]:min-h-11"><svg className="h-[0.875rem] w-[0.875rem] shrink-0 fill-current" aria-hidden="true"><use href="#i-filter-clear" xlinkHref="#i-filter-clear" /></svg> Clear Filters</button>
        </div>
      </div>
      <div className="mt-3 max-h-[min(18rem,40vh)] overflow-auto rounded-md bg-[rgba(4,20,80,0.55)] [.light_&]:bg-white [.light_&]:border [.light_&]:border-[#e0e7f0] [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar-thumb]:rounded-sm [&::-webkit-scrollbar-thumb]:bg-[#1c46b8] [&::-webkit-scrollbar-track]:bg-transparent [@media(min-width:1280px)_and_(max-width:1365px)_and_(min-height:901px)]:max-h-[min(16rem,38vh)] [@media(min-width:1280px)_and_(min-height:751px)_and_(max-height:900px)]:max-h-[min(14rem,35vh)] [@media(min-width:1280px)_and_(max-height:750px)]:max-h-[min(12rem,32vh)]">
        <table className="w-full min-w-[61.25rem] table-fixed border-collapse"><thead><tr>
          {[["#","w-[3.125rem]"],["Filename","w-auto"],["Category","w-[12%]"],["Confidence","w-[10%]"],["Model Used","w-[15%]"],["Verification Status","w-[10%]"],["Processed At","w-[15%]"],["Actions","w-[10%]"]].map(([label,width],i)=><th key={label} scope="col" className={`sticky top-0 z-[2] h-9 border-b border-[#14307f] bg-[#0c2c8c] [.light_&]:bg-[#f1f5fb] [.light_&]:text-[#203653] px-2 py-[0.7rem] text-left text-[0.78rem] font-semibold whitespace-nowrap ${width} ${i===0?'border-l-[3px] border-l-[#2d7bff]':''}`}>{label}</th>)}
        </tr></thead><tbody>
          {filtered.length ? filtered.map((row,index) => { const hasConfidence = row.confidence !== null && row.confidence !== undefined && row.confidence !== "" && Number.isFinite(Number(row.confidence)); const confidence = hasConfidence ? Number(row.confidence) : null; return <tr key={row.segment_id || `${row.id}:${row.document_index ?? "file"}` } className="border-b border-[#14307f]/70 hover:bg-[rgba(40,90,230,0.15)] [.light_&]:border-[#e0e7f0] [.light_&]:hover:bg-[#f5f8fd]"><td className="px-2 py-2 text-xs">{index+1}</td><td className="truncate px-2 py-2 text-xs" title={row.display_name || row.name}>{row.display_name || row.name}</td><td className="px-2 py-2 text-xs capitalize">{row.category}</td><td className="px-2 py-2 text-xs">{hasConfidence ? `${(confidence*100).toFixed(1)}%` : "—"}</td><td className="px-2 py-2 text-xs">{row.model || "—"}</td><td className="px-2 py-2 text-xs">
    <span className={row.classification_verification?.verified === true ? "text-emerald-300" : "text-amber-300"}>
        {row.classification_verification?.verified === true ? "Verified" : "Verification Pending"}
    </span>
</td><td className="px-2 py-2 text-xs">{row.created_at ? new Date(row.created_at).toLocaleString() : "—"}</td><td className="px-2 py-2 text-xs"><button type="button" onClick={() => navigate(`/scanning-result/${row.id}`)} className="rounded border border-[#2a63e0] px-2 py-1 text-[#9bd7ff] hover:bg-[#12399f]">View</button></td></tr>; }) : <tr><td colSpan={8} className="overflow-hidden p-0 text-left text-[0.75rem] text-ellipsis"><div className="flex flex-col items-center justify-center gap-[0.375rem] px-4 py-9 text-center"><svg className="mb-1 h-10 w-10 fill-current text-[#3fb4ff] opacity-80" aria-hidden="true"><use href="#i-scan" xlinkHref="#i-scan" /></svg><strong className="text-[0.875rem] font-semibold">No classification results yet</strong><span className="text-[0.75rem] text-[#9db4ee] [.light_&]:text-[#526782]">Upload and classify documents to see the latest results here.</span></div></td></tr>}
        </tbody></table>
      </div>
      <div className="mt-3 pl-1 text-[0.75rem]">Showing {filtered.length} of {results.length} results</div>
    </section>
  );
}

