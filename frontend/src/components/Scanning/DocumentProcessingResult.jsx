import { useEffect, useState } from "react";
import { FileText } from "lucide-react";
import ProcessingStatus from "./ProcessingStatus.jsx";
import DocumentPreview from "./DocumentPreview.jsx";
import ExtractedInformation from "./ExtractedInformation.jsx";
import OcrExtractedText from "./OcrExtractedText.jsx";
import PredictionResults from "./PredictionResults.jsx";
import ClassProbability from "./ClassProbability.jsx";
import Icon from "../Common/Icon.jsx";
import { updateDocument } from "../../services/idpService.js";

export default function DocumentProcessingResult({ documentData = null, onViewAnalytics, onVerificationSaved }) {
  const segments = Array.isArray(documentData?.documents) ? documentData.documents : [];
  const firstDocumentIndex = Number(segments[0]?.document_index ?? 1);
  const [selectedDocumentIndex, setSelectedDocumentIndex] = useState(firstDocumentIndex);
  const [confirmingClassification, setConfirmingClassification] = useState(false);
  const [verificationError, setVerificationError] = useState("");

  useEffect(() => {
    // The selected segment must reset when a different upload is opened.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedDocumentIndex(firstDocumentIndex);
  }, [documentData?.id, firstDocumentIndex]);

  const selectedSegment = segments.find(
    (segment) => Number(segment.document_index) === Number(selectedDocumentIndex)
  ) || segments[0] || null;

  const activeVerification = selectedSegment
    ? selectedSegment.classification_verification || null
    : documentData?.classification_verification || null;

  const pageRangeStart = Number(selectedSegment?.page_range?.start || 1);
  const pageRangeEnd = Number(
    selectedSegment?.page_range?.end || pageRangeStart
  );

  const pageCountCandidate = selectedSegment
    ? (
        Number(selectedSegment.page_count) > 0
          ? selectedSegment.page_count
          : selectedSegment.page_range
            ? pageRangeEnd - pageRangeStart + 1
            : selectedSegment.page_texts?.length
      )
    : (
        Number(documentData?.page_count) > 0
          ? documentData.page_count
          : documentData?.page_texts?.length
      );

  const numericPageCount = Number(pageCountCandidate);
  const selectedPagesAnalyzed =
    Number.isFinite(numericPageCount) && numericPageCount > 0
      ? Math.trunc(numericPageCount)
      : null;

  // Prediction Results reports pages analyzed across the original PDF,
  // not only the currently selected logical-document segment.
  const sourcePdfPageCount = Number(documentData?.page_count);
  const fallbackSourcePageCount = Number(documentData?.page_texts?.length);

  const totalSourcePagesAnalyzed =
    Number.isFinite(sourcePdfPageCount) && sourcePdfPageCount > 0
      ? Math.trunc(sourcePdfPageCount)
      : Number.isFinite(fallbackSourcePageCount) && fallbackSourcePageCount > 0
        ? Math.trunc(fallbackSourcePageCount)
        : selectedPagesAnalyzed;

  const basePrediction =
    selectedSegment?.prediction || documentData?.prediction || {};

  const selectedPrediction = {
    ...basePrediction,
    pages_analyzed: totalSourcePagesAnalyzed,
    verification_status:
      activeVerification?.verified === true ? "verified" : "pending",
  };

  const handlePreviewPageChange = (pageNumber) => {
    const page = Number(pageNumber);
    if (!Number.isInteger(page) || page < 1) return;

    const matchingSegment = segments.find((segment) => {
      const start = Number(segment?.page_range?.start || 1);
      const end = Number(segment?.page_range?.end || start);
      return page >= start && page <= end;
    });

    if (matchingSegment) {
      const nextIndex = Number(matchingSegment.document_index || 1);
      setSelectedDocumentIndex((current) =>
        current === nextIndex ? current : nextIndex
      );
    }
  };

  const selectedDocumentData = selectedSegment ? {
    ...documentData,
    extracted_information: selectedSegment.extracted_information || {},
    field_coverage: selectedSegment.field_coverage || null,
    confidence_review: selectedSegment.confidence_review || null,
    field_provenance: selectedSegment.field_provenance || {},
    validation_issues: selectedSegment.validation_issues || [],
    review_required: Boolean(selectedSegment.review_required),
    review_reasons: selectedSegment.review_reasons || [],
    ocr_text: selectedSegment.extracted_text || "",
    page_texts: selectedSegment.page_texts || [],
    page_numbers: selectedSegment.page_numbers || [],
    performance: segments.length > 1 ? (selectedSegment.performance || null) : documentData?.performance,
    prediction: selectedPrediction,
    classification_verification: activeVerification,
    review_decision: selectedSegment?.review_decision || documentData?.review_decision || null,
    pages_analyzed: selectedPagesAnalyzed,
    class_probabilities: selectedSegment.class_probabilities || documentData?.class_probabilities,
    selected_document_index: selectedSegment.document_index,
    selected_document_label: selectedSegment.label,
    page_range: selectedSegment.page_range || null,
  } : documentData;

  const handleConfirmClassification = async () => {
    if (!documentData?.id) {
      setVerificationError("The document ID is unavailable.");
      return;
    }

    const finalClass = String(
      selectedPrediction?.predicted_class || ""
    ).trim();

    if (!["resume", "invoice", "form"].includes(finalClass.toLowerCase())) {
      setVerificationError(
        "Only a classified Resume, Invoice, or Form can be confirmed."
      );
      return;
    }

    setConfirmingClassification(true);
    setVerificationError("");

    try {
      await updateDocument(documentData.id, {
        classification_verification: {
          verified: true,
          document_index: selectedSegment
            ? Number(selectedSegment.document_index ?? selectedDocumentIndex)
            : null,
          confirmed_class: finalClass,
        },
      });

      if (typeof onVerificationSaved === "function") {
        await onVerificationSaved();
      }
    } catch (error) {
      setVerificationError(
        error?.response?.data?.message ||
        error?.message ||
        "Unable to save the document confirmation."
      );
    } finally {
      setConfirmingClassification(false);
    }
  };



  return (
    <section id="result" className="scroll-mt-20 w-full min-w-0 max-w-full rounded-lg border border-(color:--c-line) bg-(color:--c-panel) p-3 sm:p-4" aria-labelledby="resultTitle">
      <div className="mb-3 flex items-center gap-3">
        <span className="grid h-9 w-9 place-items-center rounded-lg bg-(color:--c-primary) text-white"><FileText className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1"><h2 id="resultTitle" className="text-lg font-semibold leading-tight">Document Processing Result</h2><p className="text-sm text-(color:--c-muted)">Details of the recently processed document.</p></div>
      </div>
      <div className="grid min-w-0 w-full max-w-full grid-cols-1 gap-3">
        <ProcessingStatus
          key={`status:${documentData?.id || documentData?.file_name || "empty"}`}
          status={documentData?.status ?? "idle"}
          currentStage={documentData?.current_stage}
          progress={documentData?.progress ?? 0}
          errorMessage={documentData?.error_message}
        />

        <div className="grid min-w-0 grid-cols-1 items-stretch gap-3 lg:grid-cols-2 [&>*]:min-w-0">
          <DocumentPreview
            key={`preview:${documentData?.id || documentData?.file_name || "empty"}:${documentData?.preview_url || "no-preview"}`}
            fileUrl={documentData?.preview_url}
            fileName={documentData?.file_name}
            processing={["queued", "processing"].includes(documentData?.status)}
            pageRange={selectedSegment?.page_range || null}
            allowDocumentNavigation={documentData?.status === "completed" && segments.length > 1}
            onPageChange={handlePreviewPageChange}
          />

          <ExtractedInformation
            key={`fields:${documentData?.id || documentData?.file_name || "empty"}`}
            data={selectedDocumentData?.extracted_information}
            documentId={documentData?.id}
            documentIndex={selectedSegment?.document_index ?? null}
            documents={segments}
            selectedDocumentIndex={selectedSegment?.document_index ?? selectedDocumentIndex}
            onSelectDocument={setSelectedDocumentIndex}
            confidenceReview={selectedDocumentData?.confidence_review}
            fieldProvenance={selectedDocumentData?.field_provenance}
            reviewRequired={selectedDocumentData?.review_required}
            reviewReasons={selectedDocumentData?.review_reasons}
            validationIssues={selectedDocumentData?.validation_issues}
            reviewDecision={selectedDocumentData?.review_decision || null}
            onReviewSaved={onVerificationSaved}
            status={documentData?.status}
            fileName={documentData?.file_name || "document"}
          />
        </div>

        <OcrExtractedText
          key={`ocr:${documentData?.id || documentData?.file_name || "empty"}:${selectedSegment?.document_index ?? "all"}`}
          text={selectedDocumentData?.ocr_text ?? ""}
          pages={selectedDocumentData?.page_texts ?? []}
          pageNumbers={selectedDocumentData?.page_numbers ?? []}
          totalPageCount={documentData?.page_count ?? documentData?.page_texts?.length ?? 0}
        />
      </div>
      {documentData?.status === "completed" && selectedDocumentData?.performance && (
        <div className="mt-3 rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3">
          <h3 className="text-sm font-semibold">Processing performance breakdown</h3>
          <p className="mt-1 text-[11px] text-(color:--c-muted)">
            Per-document timings. Native PDF text extraction can bypass OCR; missing stage timings are shown as not measured.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
            {[
              ["OCR", selectedDocumentData.performance.ocr_ms],
              ["Preprocessing", selectedDocumentData.performance.preprocessing_ms],
              ["Model input prep", selectedDocumentData.performance.model_input_preprocessing_ms],
              ["Model inference", selectedDocumentData.performance.model_inference_ms],
              ["Classification", selectedDocumentData.performance.classification_ms],
              ["Field extraction", selectedDocumentData.performance.field_extraction_ms],
              ["MongoDB reads", selectedDocumentData.performance.database_read_ms],
              ["MongoDB writes", selectedDocumentData.performance.database_write_ms],
              ["Text extraction", selectedDocumentData.performance.text_extraction_ms],
              ["Total processing", selectedDocumentData.performance.total_processing_ms],
            ].map(([label, value]) => (
              <div key={label} className="min-w-0 rounded-md border border-(color:--c-line) bg-(color:--c-panel)/60 px-2.5 py-2">
                <p className="truncate text-[10px] text-(color:--c-muted)">{label}</p>
                <p className="mt-1 text-sm font-semibold tabular-nums text-(color:--c-fg)">
                  {label === "OCR" && Number(selectedDocumentData.performance.ocr_page_count || 0) === 0
                    ? Number(selectedDocumentData.performance.ocr_disabled_page_count || 0) > 0
                      ? "Disabled by setting"
                      : Number(selectedDocumentData.performance.ocr_skipped_native_page_count || 0) > 0
                        ? "Not needed (native text)"
                        : "Not run"
                    : value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value))
                      ? `${Number(value).toFixed(2)} ms`
                      : "Not measured"}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
      {documentData?.status === "completed" && selectedDocumentData?.field_coverage && (
        <div className="mt-3 rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3 [.light_&]:border-[#cbd8ea] [.light_&]:bg-white">
          <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">Extracted-field coverage</h3><span className="text-sm font-bold text-(color:--c-accent)">{Number(selectedDocumentData.field_coverage.coverage_percent || 0).toFixed(1)}%</span></div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-(color:--c-line)"><div className="h-full rounded-full bg-blue-500 transition-[width] duration-500" style={{ width: `${Math.max(0, Math.min(100, Number(selectedDocumentData.field_coverage.coverage_percent || 0)))}%` }} /></div>
          <p className="mt-2 text-xs text-(color:--c-muted)">{selectedDocumentData.field_coverage.extracted_field_count || 0} of {selectedDocumentData.field_coverage.expected_field_count || 0} expected field groups found. This is field coverage, not classifier accuracy.</p>
          
        </div>
      )}
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <PredictionResults prediction={selectedDocumentData?.prediction} />
        <ClassProbability
          data={selectedDocumentData?.class_probabilities}
          prediction={selectedDocumentData?.prediction}
          onConfirm={handleConfirmClassification}
          confirming={confirmingClassification}
          verificationError={verificationError}
        />
        <article className="flex flex-col items-center justify-center gap-4 rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-5 text-center [.light_&]:border-[#cbd8ea] [.light_&]:bg-white">
          {documentData?.status === "completed" ? <button type="button" onClick={onViewAnalytics} className="group flex h-12 w-full max-w-[330px] cursor-pointer items-center justify-center gap-2 rounded-md border border-(color:--c-accent) bg-linear-to-b from-[#1a7bff] to-(color:--c-primary) text-[15px] font-semibold text-white shadow-lg shadow-blue-600/30 transition duration-300 hover:scale-[1.03] hover:shadow-blue-500/50 active:scale-95 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(color:--c-accent)">
            <Icon name="bars" className="h-5 w-5" />View Analytics<Icon name="arrow" className="h-5 w-5 transition-transform duration-300 group-hover:translate-x-1" />
          </button> : <p className="max-w-xs text-xs leading-relaxed text-(color:--c-muted)">Model metrics and charts are available after document extraction succeeds.</p>}
          {documentData?.status === "completed" && <p className="max-w-xs text-xs leading-relaxed text-(color:--c-muted)">View the Stage 9 development comparison and Stage 10 held-out model evaluation graphs.</p>}
        </article>
      </div>
    </section>
  );
}
