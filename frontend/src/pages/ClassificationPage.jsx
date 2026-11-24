import { useEffect, useState } from "react";
import IconSprite from "../components/classification/IconSprite.jsx";
import ClassificationPageHeader from "../components/classification/ClassificationPageHeader.jsx";
import ClassificationStats from "../components/classification/ClassificationStats.jsx";
import ClassificationOverview from "../components/classification/ClassificationOverview.jsx";
import ClassificationResults from "../components/classification/ClassificationResults.jsx";
import { getClassification } from "../services/idpService.js";
import { getApiErrorMessage } from "../services/api.js";

import { DEFAULT_DATE_RANGE, createDateRangeParams } from "../utils/dateRange.js";
export default function ClassificationPage() {
  const [data, setData] = useState({ stats: {}, categories: {}, results: [] });
  const [error, setError] = useState("");
  const [dateRange, setDateRange] = useState(DEFAULT_DATE_RANGE);
  useEffect(() => {
    let active = true;

    const loadClassification = async () => {
      setError("");

      try {
        const collectedResults = [];
        let firstPayload = null;
        let page = 1;
        const pageLimit = 500;

        while (page <= 1000) {
          const payload = await getClassification({
            page,
            limit: pageLimit,
            ...createDateRangeParams(dateRange),
          });

          if (!firstPayload) {
            firstPayload = payload || {};
          }

          const pageResults = Array.isArray(payload?.results)
            ? payload.results
            : [];

          collectedResults.push(...pageResults);

          const declaredPages = Number(payload?.pagination?.pages);
          const hasDeclaredPages =
            payload?.pagination?.pages != null &&
            Number.isFinite(declaredPages) &&
            declaredPages > 0;

          if (pageResults.length === 0) {
            break;
          }

          if (hasDeclaredPages) {
            if (page >= declaredPages) break;
          } else if (pageResults.length < pageLimit) {
            break;
          }

          page += 1;
        }

        if (page > 1000) {
          throw new Error("Classification pagination exceeded its safety limit.");
        }

        if (!active) return;

        const verificationPending = collectedResults.filter(
          (row) => row.classification_verification?.verified !== true
        ).length;

        setData({
          ...(firstPayload || {}),
          stats: {
            ...(firstPayload?.stats || {}),
            verification_pending: verificationPending,
          },
          results: collectedResults,
        });
      } catch (error) {
        if (active) {
          setError(
            getApiErrorMessage(error, "Unable to load classification data.")
          );
        }
      }
    };

    loadClassification();

    return () => {
      active = false;
    };
  }, [dateRange]);
  return (
    <>
      <IconSprite />
      <div className="min-h-dvh w-full scroll-smooth overflow-x-hidden bg-[#050b1f] text-[#e6eeff] [.light_&]:bg-[#eef3fb] [.light_&]:text-[#1b2b45] motion-reduce:scroll-auto">
        <div className="flex min-h-dvh min-w-0 flex-1 flex-col">
          <main className="mx-auto flex min-h-dvh w-full max-w-[106.25rem] flex-1 flex-col gap-4 [padding:0.625rem_1.375rem_1.5rem] min-[1920px]:max-w-[112.5rem] [@media(min-width:1366px)_and_(max-width:1439px)]:[padding:0.5rem_1.25rem_1rem] [@media(min-width:1366px)_and_(max-width:1439px)_and_(min-height:901px)]:gap-[0.875rem] [@media(min-width:1280px)_and_(max-width:1365px)]:[padding:0.4rem_1.1rem_0.9rem] [@media(min-width:1280px)_and_(max-width:1365px)_and_(min-height:901px)]:gap-3 [@media(min-width:1280px)_and_(max-height:900px)]:gap-[0.7rem] [@media(max-width:900px)]:[padding:0.6rem_0.75rem_1.25rem]">
            <ClassificationPageHeader />
            {error && <p role="alert" className="rounded-lg border border-rose-500/40 bg-rose-950/40 px-3 py-2 text-sm text-rose-200">{error}</p>}
            <ClassificationStats stats={data.stats} />
            <ClassificationOverview categories={data.categories} />
            <ClassificationResults
              results={data.results}
              dateRange={dateRange}
              onDateRangeChange={setDateRange}
              onClearDateRange={() => setDateRange(DEFAULT_DATE_RANGE)}
            />
          </main>
        </div>
      </div>
    </>
  );
}

