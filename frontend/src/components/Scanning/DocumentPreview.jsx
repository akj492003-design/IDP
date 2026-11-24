import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import Icon from "../Common/Icon.jsx";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const buttonClass =
  "inline-flex min-h-8 items-center justify-center rounded-md border border-[#344761] bg-[#17243a] px-2.5 text-xs font-medium text-[#e2eaf5] transition hover:border-blue-400 hover:bg-[#233a5b] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400 disabled:cursor-not-allowed disabled:opacity-40";

export default function DocumentPreview({
  fileUrl = null,
  fileName = null,
  processing = false,
  pageRange = null,
  allowDocumentNavigation = false,
  onPageChange = null,
}) {
  const viewerRootRef = useRef(null);
  const containerRef = useRef(null);
  const lineRef = useRef(null);
  const scanLineRefs = useRef([]);
  const canvasRefs = useRef([]);
  const pageRefs = useRef([]);

  const [pdf, setPdf] = useState(null);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const currentPageRef = useRef(1);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [viewportWidth, setViewportWidth] = useState(0);
  const [loading, setLoading] = useState(Boolean(fileUrl));
  const [rendering, setRendering] = useState(Boolean(fileUrl));
  const [error, setError] = useState("");
  const [controlMessage, setControlMessage] = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [renderedPageNumber, setRenderedPageNumber] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
  const rangeStart = pageCount > 0
    ? Math.max(1, Math.min(pageCount, Math.floor(Number(pageRange?.start) || 1)))
    : 1;
  const rangeEnd = pageCount > 0
    ? Math.max(rangeStart, Math.min(pageCount, Math.floor(Number(pageRange?.end) || pageCount)))
    : 1;

  // During processing, scan the original PDF from page one to its last page.
  // Outside processing, retain the selected logical document's page range.
  const fullDocumentScan = processing && !reducedMotion;
  const singlePageNavigation = fullDocumentScan || allowDocumentNavigation;
  const renderStart = singlePageNavigation ? currentPage : rangeStart;
  const renderEnd = singlePageNavigation ? currentPage : rangeEnd;
  const viewStart = singlePageNavigation ? 1 : rangeStart;
  const viewEnd = singlePageNavigation ? pageCount : rangeEnd;

  useEffect(() => {
    currentPageRef.current = currentPage;
  }, [currentPage]);


  // Respect the user's reduced-motion preference.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;

    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(media.matches);

    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  // Track fullscreen state.
  useEffect(() => {
    const update = () => {
      setIsFullscreen(
        Boolean(
          viewerRootRef.current &&
          document.fullscreenElement === viewerRootRef.current
        )
      );
    };

    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);

  // Measure the actual PDF viewport width.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;

    const measure = () => {
      setViewportWidth(element.clientWidth);
      setRendering(true);
    };

    // ResizeObserver or the animation-frame fallback performs initial measurement.

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(measure);
      observer.observe(element);
      return () => observer.disconnect();
    }

    const frame = window.requestAnimationFrame(measure);
    window.addEventListener("resize", measure);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", measure);
    };
  }, [fileUrl]);

  // Load the authenticated preview URL.
  useEffect(() => {
    let active = true;
    let loadingTask = null;

    if (!fileUrl) return undefined;

    try {
      loadingTask = getDocument({
        url: fileUrl,
        useSystemFonts: true,
        isEvalSupported: false,
      });

      loadingTask.promise
        .then((documentProxy) => {
          if (!active) {
            void documentProxy.destroy();
            return;
          }

          setPdf(documentProxy);
          setPageCount(documentProxy.numPages);
          setLoading(false);
          setRendering(true);
        })
        .catch((loadError) => {
          if (!active) return;

          console.error("PDF preview loading failed:", loadError);
          setError(
            loadError?.message ||
              "The PDF could not be opened. Try downloading the original file."
          );
          setLoading(false);
        });
    } catch (loadError) {
      Promise.resolve().then(() => {
        if (!active) return;

        setError(
          loadError?.message ||
            "The PDF preview could not be initialized."
        );
        setLoading(false);
        setRendering(false);
      });
    }

    return () => {
      active = false;

      if (loadingTask) {
        void loadingTask.destroy().catch(() => {});
      }
    };
  }, [fileUrl]);

  // Reset the preview to the correct starting page when processing begins,
  // the source PDF changes, or the selected logical document changes.
  useEffect(() => {
    if (!pdf || pageCount <= 0) return undefined;

    const previousPage = currentPageRef.current;
    const targetPage = fullDocumentScan
      ? 1
      : previousPage >= rangeStart && previousPage <= rangeEnd
        ? previousPage
        : rangeStart;

    if (targetPage !== previousPage) {
      setCurrentPage(targetPage);
    }
    setRendering(true);

    const frame = window.requestAnimationFrame(() => {
      const page = pageRefs.current[targetPage - 1];

      if (page && containerRef.current) {
        containerRef.current.scrollTo({
          top: Math.max(0, page.offsetTop - 8),
          behavior: "auto",
        });
      }
    });

    return () => window.cancelAnimationFrame(frame);
  }, [pdf, pageCount, rangeStart, rangeEnd, fullDocumentScan]);

  // Render the PDF pages on canvases. A new render replaces the previous
  // one when zoom, rotation, document, or viewport width changes.
  useEffect(() => {
    if (!pdf || !containerRef.current || viewportWidth <= 0) {
      return undefined;
    }

    let cancelled = false;
    const renderTasks = [];

    const renderPages = async () => {
      try {
        const availableWidth = Math.max(220, viewportWidth - 40);
        const outputScale = Math.min(window.devicePixelRatio || 1, 2);

        for (let pageNumber = renderStart; pageNumber <= renderEnd; pageNumber += 1) {
          if (cancelled) return;

          const page = await pdf.getPage(pageNumber);
          if (cancelled) return;

          const canvas = canvasRefs.current[pageNumber - 1];
          if (!canvas) continue;

          const context = canvas.getContext("2d", { alpha: false });
          if (!context) {
            throw new Error(`Could not initialize canvas for page ${pageNumber}.`);
          }

          const baseViewport = page.getViewport({
            scale: 1,
            rotation,
          });

          const scale = (availableWidth / baseViewport.width) * zoom;

          const viewport = page.getViewport({
            scale,
            rotation,
          });

          canvas.width = Math.ceil(viewport.width * outputScale);
          canvas.height = Math.ceil(viewport.height * outputScale);
          canvas.style.width = `${Math.ceil(viewport.width)}px`;
          canvas.style.height = `${Math.ceil(viewport.height)}px`;

          context.fillStyle = "#ffffff";
          context.fillRect(0, 0, canvas.width, canvas.height);

          const task = page.render({
            canvasContext: context,
            viewport,
            transform:
              outputScale !== 1
                ? [outputScale, 0, 0, outputScale, 0, 0]
                : null,
          });

          renderTasks.push(task);
          if (fullDocumentScan) {
            try {
              await task.promise;
            } catch (renderTaskError) {
              if (cancelled) return;
              throw renderTaskError;
            }
            if (!cancelled) setRenderedPageNumber(pageNumber);
          }
        }

        if (!cancelled) setRendering(false);
      } catch (renderError) {
        if (!cancelled) {
          console.error("PDF page rendering failed:", renderError);
          setError(
            renderError?.message ||
              "One or more PDF pages could not be rendered."
          );
          setRendering(false);
        }
      }
    };

    void renderPages();

    return () => {
      cancelled = true;
      renderTasks.forEach((task) => {
        try {
          task.cancel();
        } catch {
          // The render task may already have completed.
        }
      });
    };
  }, [
    pdf,
    viewportWidth,
    zoom,
    rotation,
    renderStart,
    renderEnd,
    fullDocumentScan,
  ]);

  // The scanline traverses each rendered source page in sequence.
  const scanning = processing && !reducedMotion;

  useEffect(() => {
    if (!scanning) return undefined;

    const box = containerRef.current;
    if (!box) return undefined;

    // While the PDF is still loading, retain a loading-state animation.
    if (!pdf) {
      const line = lineRef.current;

      if (!line || typeof line.animate !== "function") {
        return undefined;
      }

      const travel = Math.max(0, box.clientHeight - line.offsetHeight);
      const animation = line.animate(
        [
          { transform: "translateY(0px)", opacity: 0.85 },
          { transform: `translateY(${travel}px)`, opacity: 1 },
        ],
        {
          duration: 2400,
          easing: "ease-in-out",
          direction: "alternate",
          iterations: Infinity,
        }
      );

      return () => animation.cancel();
    }

    // Wait until the active page canvas is fully rendered.
    if (rendering || renderedPageNumber !== currentPage) {
      return undefined;
    }

    const page = pageRefs.current[currentPage - 1];
    const line = scanLineRefs.current[currentPage - 1];

    if (
      !page ||
      !line ||
      typeof line.animate !== "function"
    ) {
      return undefined;
    }

    box.scrollTo({
      top: Math.max(0, page.offsetTop - 8),
      behavior: "auto",
    });

    const travel = Math.max(0, page.clientHeight - line.offsetHeight);
    const duration = Math.max(
      450,
      Math.min(1600, 18000 / Math.max(1, pageCount))
    );

    const animation = line.animate(
      [
        { transform: "translateY(0px)", opacity: 0.85 },
        { transform: `translateY(${travel}px)`, opacity: 1 },
      ],
      {
        duration,
        easing: "linear",
        direction: "alternate",
        iterations: pageCount > 1 ? 2 : Infinity,
      }
    );

    // Move to the next source page only after the current page is scanned.
    if (pageCount > 1) {
      animation.onfinish = () => {
        setRendering(true);
        setCurrentPage((previous) =>
          previous >= pageCount ? 1 : previous + 1
        );
      };
    }

    return () => {
      animation.onfinish = null;
      animation.cancel();
    };
  }, [
    scanning,
    pdf,
    rendering,
    renderedPageNumber,
    currentPage,
    pageCount,
    fileUrl,
  ]);

  const goToPage = (pageNumber) => {
    const target = Math.max(viewStart, Math.min(viewEnd, pageNumber));

    if (target !== currentPage) {
      setRendering(true);
    }

    setCurrentPage(target);

    if (!fullDocumentScan) {
      onPageChange?.(target);
    }

    if (!fullDocumentScan && !allowDocumentNavigation) {
      pageRefs.current[target - 1]?.scrollIntoView({
        behavior: reducedMotion ? "auto" : "smooth",
        block: "start",
      });
    }
  };

  const changeZoom = (direction) => {
    setRendering(true);
    setZoom((previous) => {
      const next =
        direction > 0
          ? previous * 1.15
          : previous / 1.15;

      return Math.max(0.4, Math.min(3, Number(next.toFixed(2))));
    });
  };

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === viewerRootRef.current) {
        await document.exitFullscreen();
      } else {
        await viewerRootRef.current?.requestFullscreen();
      }
    } catch {
      setControlMessage("Fullscreen mode is unavailable in this browser.");
    }
  };

  const frameClass = fileUrl
    ? "relative h-[min(70vh,760px)] min-h-[360px] w-full min-w-0 flex-1 overflow-auto rounded-md border border-[#344761] bg-[#0b1220] p-3 sm:p-4"
    : "relative flex min-h-[220px] flex-1 flex-col items-center justify-center overflow-hidden rounded-md border border-dashed border-(color:--c-dash)/70 bg-(color:--c-panel) p-4 text-center text-xs leading-relaxed text-(color:--c-muted)";

  return (
    <article
      ref={viewerRootRef}
      className="flex min-w-0 w-full flex-col rounded-lg border border-(color:--c-line) bg-(color:--c-inner)/60 p-3 shadow-[0_2px_10px_rgba(0,0,0,.04)] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:shadow-[0_6px_18px_rgba(15,35,70,.06)]"
    >
      <div className="mb-3 flex min-w-0 flex-wrap items-center justify-between gap-2">
        <h3 className="shrink-0 text-sm font-semibold text-(color:--c-fg)">
          Document Preview
        </h3>

        {fileUrl && (
          <a
            href={fileUrl}
            download={fileName || "document.pdf"}
            className="inline-flex min-h-8 items-center rounded-md border border-[#344761] bg-[#17243a] px-3 text-xs font-semibold text-[#e2eaf5] transition hover:bg-[#233a5b] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400"
          >
            Download original
          </a>
        )}
      </div>

      {fileUrl && (
        <div className="mb-2 flex min-w-0 flex-wrap items-center gap-2 rounded-md border border-[#263a58] bg-[#111e32] p-2">
          <button
            type="button"
            className={buttonClass}
            disabled={currentPage <= viewStart}
            onClick={() => goToPage(currentPage - 1)}
            aria-label="Previous PDF page"
          >
            Previous
          </button>

          <label className="flex items-center gap-1.5 text-xs text-[#dbeafe]">
            Page
            <input
              type="number"
              min={viewStart}
              max={viewEnd}
              value={currentPage}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (
                  Number.isInteger(value) &&
                  value >= viewStart &&
                  value <= viewEnd
                ) {
                  goToPage(value);
                }
              }}
              className="h-8 w-14 rounded border border-[#344761] bg-[#0b1220] px-1 text-center text-xs text-white outline-none focus:border-blue-400"
              aria-label="PDF page number"
            />
            of {pageCount || "—"}
          </label>

          <button
            type="button"
            className={buttonClass}
            disabled={currentPage >= viewEnd}
            onClick={() => goToPage(currentPage + 1)}
            aria-label="Next PDF page"
          >
            Next
          </button>

          <span className="mx-1 hidden h-5 border-l border-[#344761] sm:block" />

          <button
            type="button"
            className={buttonClass}
            onClick={() => changeZoom(-1)}
            disabled={zoom <= 0.4}
            aria-label="Zoom out"
          >
            −
          </button>

          <span className="min-w-12 text-center text-xs tabular-nums text-[#dbeafe]">
            {Math.round(zoom * 100)}%
          </span>

          <button
            type="button"
            className={buttonClass}
            onClick={() => changeZoom(1)}
            disabled={zoom >= 3}
            aria-label="Zoom in"
          >
            +
          </button>

          <button
            type="button"
            className={buttonClass}
            onClick={() => { setRendering(true); setZoom(1); }}
          >
            Fit width
          </button>

          <button
            type="button"
            className={buttonClass}
            onClick={() => { setRendering(true); setRotation((value) => (value + 90) % 360); }}
          >
            Rotate
          </button>

          <button
            type="button"
            className={buttonClass}
            onClick={toggleFullscreen}
          >
            {isFullscreen ? "Exit full screen" : "Full screen"}
          </button>
        </div>
      )}

      {controlMessage && (
        <p className="mb-2 text-xs text-amber-300" role="status">
          {controlMessage}
        </p>
      )}

      <div
        ref={containerRef}
        className={frameClass}
        aria-busy={loading || rendering}
      >
        {fileUrl ? (
          <>
            {loading && (
              <div className="flex min-h-[240px] items-center justify-center text-sm text-slate-300">
                Opening PDF preview…
              </div>
            )}

            {error && (
              <div
                className="m-3 rounded-md border border-rose-400/40 bg-rose-950/40 p-4 text-sm text-rose-200"
                role="alert"
              >
                {error}
              </div>
            )}

            {pdf &&
              !error &&
              Array.from({ length: Math.max(0, renderEnd - renderStart + 1) }, (_, index) => renderStart + index).map(
                (pageNumber) => (
                  <div
                    key={`${fileUrl}-${pageNumber}`}
                    ref={(element) => {
                      pageRefs.current[pageNumber - 1] = element;
                    }}
                    className="relative mx-auto mb-4 w-fit max-w-none"
                    aria-label={`PDF page ${pageNumber}`}
                  >
                    <canvas
                      ref={(element) => {
                        canvasRefs.current[pageNumber - 1] = element;
                      }}
                      className="block bg-white shadow-[0_5px_24px_rgba(0,0,0,.4)]"
                    />

                    {scanning && pageNumber === currentPage && (
                      <>
                        <div className="pointer-events-none absolute inset-0 z-10 bg-cyan-400/[.035]" />
                        <div
                          ref={(element) => {
                            scanLineRefs.current[pageNumber - 1] = element;
                          }}
                          aria-hidden="true"
                          className="pointer-events-none absolute left-0 right-0 top-0 z-20 h-1 bg-linear-to-r from-transparent via-cyan-300 to-transparent shadow-[0_0_16px_4px_rgba(34,211,238,.65)]"
                        />
                      </>
                    )}
                  </div>
                )
              )}

            {rendering && pdf && (
              <div className="sticky bottom-2 mx-auto w-fit rounded-full border border-[#344761] bg-[#17243a]/95 px-3 py-1.5 text-[11px] text-slate-200">
                Rendering PDF pages…
              </div>
            )}
          </>
        ) : (
          <>
            <Icon
              name="doc"
              className="relative z-0 mb-3 h-10 w-10 text-(color:--c-accent)"
            />
            <p className="relative z-0 text-[13px] font-semibold text-(color:--c-accent)">
              {processing ? "Scanning document" : "Document preview unavailable"}
            </p>
            <p className="relative z-0 mt-2 max-w-[200px]">
              {processing
                ? "The document is being read. Extracted text and fields will appear here when it finishes."
                : "Extracted text and structured fields are still available for this document."}
            </p>
          </>
        )}

        {scanning && !pdf && (
          <>
            <div className="pointer-events-none absolute inset-0 z-10 bg-cyan-400/[.035]" />
            <div
              ref={lineRef}
              aria-hidden="true"
              className="pointer-events-none absolute left-0 right-0 top-0 z-20 h-1 bg-linear-to-r from-transparent via-cyan-300 to-transparent shadow-[0_0_16px_4px_rgba(34,211,238,.65)]"
            />
          </>
        )}

        {processing && (
          <span className="absolute right-2 top-2 z-30 rounded-full border border-cyan-300/40 bg-slate-950/80 px-2 py-1 text-[10px] font-semibold text-cyan-200">
            {scanning && pdf ? `Live scan · Page ${currentPage} / ${pageCount}` : reducedMotion ? "Processing" : "Preparing scan"}
          </span>
        )}
      </div>
    </article>
  );
}