import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getDocument, downloadDocument } from "../services/idpService.js";
import ModelPerformanceAnalytics from "../components/Analytics/ModelPerformanceAnalytics.jsx";
import ExtractedInformation from "../components/Scanning/ExtractedInformation.jsx";

const cardClass = "rounded-2xl border border-blue-400/20 bg-[linear-gradient(145deg,rgba(15,35,104,.78),rgba(5,14,54,.92))] p-5 shadow-[inset_0_1px_0_rgba(255,255,255,.04)] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:text-[#203653] [.light_&]:shadow-[0_5px_18px_rgba(15,35,70,.06)]";


export default function ScanningResultPage() {
  const { documentId } = useParams();
  const [document, setDocument] = useState(null);
  const [error, setError] = useState("");
  const [originalDownloadError, setOriginalDownloadError] = useState("");
  const [downloadingOriginal, setDownloadingOriginal] = useState(false);

  useEffect(() => {
    let active = true;
    let timer;
    let attempts = 0;
    const poll = async () => {
      try {
        const result = await getDocument(documentId);
        if (!active) return;
        setDocument(result);
        setError("");
        if (["queued", "processing"].includes(result.status)) {
          attempts += 1;
          const delay = Math.min(5000, 1000 + Math.floor(attempts / 5) * 500);
          timer = window.setTimeout(poll, delay);
        }
      } catch (requestError) {
        if (active) {
          setError(requestError?.response?.data?.message || "Unable to load this scan result. Retrying…");
          timer = window.setTimeout(poll, 4000);
        }
      }
    };
    poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, [documentId]);

  const refreshDocument = async () => {
    const latest = await getDocument(documentId);
    setDocument(latest);
  };

  const result = document?.result;
  const classification = result?.classification;
  const singleSegment = (
    Array.isArray(result?.documents) && result.documents.length === 1
  ) ? result.documents[0] : null;
  const insights = result?.insights;
  const completed = document?.status === "completed";
  const processing = ["queued", "processing"].includes(document?.status);

  const handleOriginalDownload = async () => {
    if (!document?.id) return;
    setOriginalDownloadError("");
    setDownloadingOriginal(true);
    try {
      await downloadDocument(document.id, document.file_name || document.name || "original-document");
    } catch (downloadError) {
      setOriginalDownloadError(
        downloadError?.response?.data?.message || "Unable to download the original uploaded file."
      );
    } finally {
      setDownloadingOriginal(false);
    }
  };

  return (
    <div className="min-h-dvh bg-[#050b1f] px-3 py-5 text-[#e6eeff] [.light_&]:bg-[#eef3fb] [.light_&]:text-[#1b2b45] sm:px-6 sm:py-8">
      <main className="mx-auto max-w-6xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><p className="text-xs font-semibold uppercase tracking-[.18em] text-cyan-300">Document intelligence</p><h1 className="mt-1 text-2xl font-bold sm:text-3xl">Scanning Result</h1><p className="mt-1 text-sm text-blue-200">Live processing status, extracted content and classification insights.</p></div>
          <div className="flex flex-wrap gap-2">
            {document?.id && (
              <button
                type="button"
                onClick={handleOriginalDownload}
                disabled={downloadingOriginal}
                className="rounded-lg border border-cyan-400/40 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-500/15 disabled:opacity-60 [.light_&]:text-cyan-800"
              >
                {downloadingOriginal ? "Preparing original…" : "Download original"}
              </button>
            )}
            <Link to="/home" className="rounded-lg border border-blue-400/30 px-4 py-2 text-sm font-semibold hover:bg-blue-500/15">Dashboard</Link>
            <Link to="/history" className="rounded-lg border border-blue-400/30 px-4 py-2 text-sm font-semibold hover:bg-blue-500/15">History</Link>
          </div>
        </div>
        {originalDownloadError && (
          <p className="rounded-lg border border-rose-400/30 bg-rose-950/20 p-3 text-xs text-rose-200" role="alert">
            {originalDownloadError}
          </p>
        )}

        <section className={cardClass} aria-live="polite">
          <div className="flex flex-wrap items-center gap-4">
            <div className="grid size-14 shrink-0 place-items-center rounded-xl bg-blue-500/15 text-cyan-300">
              {processing ? <span className="size-8 animate-spin rounded-full border-[3px] border-blue-200/20 border-t-cyan-300" aria-label="Scanning" /> : <svg viewBox="0 0 24 24" className="size-7" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M4 4h16v16H4zM8 9h8M8 13h5M8 17h7" /></svg>}
            </div>
            <div className="min-w-0 flex-1"><h2 className="truncate text-lg font-semibold">{document?.file_name || document?.name || "Loading document…"}</h2><p className="mt-1 text-sm text-blue-200">{processing ? "Scanning document, extracting text and running classification…" : completed ? "Processing completed." : document?.status === "failed" ? "Processing failed." : error || "Retrieving document status…"}</p></div>
            <span className={`rounded-full px-3 py-1 text-xs font-semibold ${completed ? "bg-emerald-400/15 text-emerald-300" : document?.status === "failed" ? "bg-rose-400/15 text-rose-300" : "bg-cyan-400/15 text-cyan-200"}`}>{document?.status || "loading"}</span>
          </div>
          {processing && <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-blue-950"><div className="h-full w-2/5 animate-[scanline_1.8s_ease-in-out_infinite] rounded-full bg-gradient-to-r from-transparent via-cyan-300 to-transparent" /></div>}
          {document?.error_message && <p className="mt-3 text-sm text-rose-300">{document.error_message}</p>}
          {error && <p className="mt-3 text-sm text-rose-300">{error}</p>}
        </section>

        {completed && <>
          <section className="grid gap-4 md:grid-cols-3">
            <article className={cardClass}><p className="text-xs uppercase tracking-wide text-blue-200">Predicted class</p><p className={`mt-2 text-2xl font-bold capitalize ${insights?.confidence_review?.prediction_rejected ? "text-rose-300" : "text-cyan-300"}`}>{insights?.confidence_review?.prediction_rejected ? "Rejected — Manual Review" : classification?.label || "Unclassified"}</p><p className="mt-1 text-xs text-blue-200">Model: {classification?.model || "Not available"}</p></article>
            <article className={cardClass}><p className="text-xs uppercase tracking-wide text-blue-200">Extracted words</p><p className="mt-2 text-2xl font-bold">{insights?.word_count ?? 0}</p><p className="mt-1 text-xs text-blue-200">Based on extracted document text</p></article>
            <article className={cardClass}><p className="text-xs uppercase tracking-wide text-blue-200">Key fields found</p><p className="mt-2 text-2xl font-bold">{Object.values(insights?.key_fields || {}).reduce((n, value) => n + (Array.isArray(value) ? value.length : value && typeof value === "object" ? Object.keys(value).length : value == null || value === "" ? 0 : 1), 0)}</p><p className="mt-1 text-xs text-blue-200">Category-specific values identified in the extracted text</p></article>
          </section>

          {insights?.field_coverage && <section className={cardClass}>
            <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">Extracted-field coverage</h2><strong className="text-cyan-300">{Number(insights.field_coverage.coverage_percent || 0).toFixed(1)}%</strong></div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-blue-950/40 [.light_&]:bg-slate-100"><div className="h-full rounded-full bg-cyan-500 transition-[width] duration-500" style={{ width: `${Math.max(0, Math.min(100, Number(insights.field_coverage.coverage_percent || 0)))}%` }} /></div>
            <p className="mt-2 text-xs text-blue-200 [.light_&]:text-[#526782]">{insights.field_coverage.extracted_field_count || 0} of {insights.field_coverage.expected_field_count || 0} expected field groups were found. Coverage is not model accuracy.</p>
            
          </section>}
          <section className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,.9fr)]">
            <article className={cardClass}><h2 className="text-lg font-semibold">Document summary</h2><p className="mt-3 text-sm leading-7 text-blue-100">{insights?.summary || "No readable text was extracted."}</p></article>
            {Array.isArray(result?.documents) && result.documents.length > 1 ? (
              <article className={cardClass}>
                <h2 className="text-lg font-semibold">Detected documents</h2>
                <p className="mt-2 text-xs text-blue-200">Each segment is extracted independently. Verify page boundaries, especially when two documents of the same type are adjacent.</p>
                <p className="mt-3 text-sm font-semibold text-cyan-300">{result.documents.length} document segments detected</p>
              </article>
            ) : (
              <ExtractedInformation
                data={singleSegment?.insights?.key_fields || insights?.key_fields || {}}
                documentId={document?.id}
                documentIndex={singleSegment?.document_index ?? null}
                reviewDecision={singleSegment?.review_decision || result?.review_decision || null}
                onReviewSaved={refreshDocument}
                documents={result?.documents || []}
                fieldProvenance={insights?.field_provenance || {}}
                status={document?.status}
                fileName={document?.file_name || document?.name || "document"}
              />
            )}
          </section>
          {Array.isArray(result?.documents) && result.documents.length > 1 && (
            <section className="space-y-4">
              {result.documents.map((segment) => (
                <article key={segment.document_index} className={cardClass}>
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-base font-semibold">Document {segment.document_index}: <span className="capitalize text-cyan-300">{segment.classification?.label || "Unclassified"}</span></h2>
                    <span className="rounded-full border border-blue-400/30 px-3 py-1 text-xs text-blue-200">Pages {segment.page_range?.start}–{segment.page_range?.end}</span>
                  </div>
                  <p className="mb-3 text-xs text-blue-300">Boundary: {String(segment.boundary_method || "automatic").replaceAll("_", " ")} · {segment.page_count} page(s)</p>
                  <div className="grid gap-4 lg:grid-cols-2">
                    <div className="rounded-xl border border-blue-400/15 bg-[#030a2a]/40 p-3">
                      <h3 className="text-sm font-semibold">Segment summary</h3>
                      <p className="mt-2 text-xs leading-6 text-blue-100">{segment.insights?.summary || "No readable text extracted."}</p>
                      
                    </div>
                    <ExtractedInformation
                      data={segment.insights?.key_fields || {}}
                      documentId={document?.id}
                      documentIndex={segment.document_index}
                      reviewDecision={segment.review_decision || null}
                      onReviewSaved={refreshDocument}
                      documents={result.documents}
                      selectedDocumentIndex={segment.document_index}
                      fieldProvenance={segment.insights?.field_provenance || {}}
                      status={document?.status || "completed"}
                      fileName={`${document?.file_name || "document"}_part_${segment.document_index}`}
                    />
                  </div>
                </article>
              ))}
            </section>
          )}

          <section className={cardClass}><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">Extracted text</h2><span className="text-xs text-blue-300">{result?.text_truncated ? "Display limited to 2,000,000 characters" : `${insights?.character_count ?? 0} characters`}</span></div><pre className="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-xl border border-blue-400/10 bg-[#030a2a] p-4 text-xs leading-6 text-blue-100">{result?.extracted_text || "No text extracted."}</pre></section>
          <ModelPerformanceAnalytics />
        </>}
        {!completed && !processing && document?.status === "failed" && <section className={`${cardClass} text-sm text-rose-200`}>This file could not be processed. Check its format and contents, then upload it again.</section>}
        <div className="flex flex-wrap justify-end gap-2"><Link to="/model-performance" className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-500">Model performance</Link><Link to="/track-manage" className="rounded-lg border border-blue-400/30 px-4 py-2 text-sm font-semibold hover:bg-blue-500/15">Track &amp; Manage</Link></div>
      </main>
    </div>
  );
}
