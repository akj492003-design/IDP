import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import ActivityTimeline from '../components/history/ActivityTimeline.jsx';
import EventDetails from '../components/history/EventDetails.jsx';
import HistoryFilters from '../components/history/HistoryFilters.jsx';
import Icon from '../components/history/Icon.jsx';
import { DEMO_EVENTS, pad2, dateToString } from '../Data/historyData.js';
import { DEFAULT_DATE_RANGE, createDateRangeParams } from '../utils/dateRange.js';
import { getHistory } from '../services/idpService.js';
import { getApiErrorMessage } from '../services/api.js';

const demoMode = new URLSearchParams(window.location.search).has('demo');
const DEMO_REFERENCE_DATE = '2023-09-07';

const DEFAULT_HISTORY_DATE_RANGE = {
  ...DEFAULT_DATE_RANGE,
  preset: 'last_7_days',
};

const initialFilters = {
  q: '',
  type: 'All Event Types',
  status: 'All Status',
  dateRange: DEFAULT_HISTORY_DATE_RANGE,
  selected: null,
  expanded: [],
  openDropdown: null,
};

function resolveHistoryDateParams(selection) {
  const referenceNow = demoMode
    ? new Date(`${DEMO_REFERENCE_DATE}T12:00:00`)
    : new Date();

  return createDateRangeParams(selection, referenceNow);
}

function Header() {
  return (
    <section className="relative z-40 flex min-h-24 flex-wrap items-center gap-3.5 rounded-[14px] border border-[#12358f] bg-linear-to-b from-[rgba(8,30,88,.78)] to-[rgba(4,18,60,.92)] p-3.5 shadow-[inset_0_0_18px_rgba(20,80,200,.16)] transition duration-300 sm:gap-5 sm:p-4 motion-safe:animate-[pulse_8s_ease-in-out_infinite] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:shadow-[0_5px_18px_rgba(15,35,70,.06)]">
      <div className="grid h-[52px] w-[52px] flex-none place-items-center rounded-full bg-[#1b74ff] shadow-[0_0_16px_rgba(27,116,255,.65)] transition-all duration-300 hover:scale-[1.03] hover:shadow-[0_0_22px_rgba(27,116,255,.85)] motion-safe:animate-pulse sm:h-16 sm:w-16"><Icon name="clock" className="h-7 w-7 stroke-2 sm:h-[34px] sm:w-[34px]"/></div>
      <div className="min-w-[180px] flex-1"><h1 className="text-[23px] font-bold sm:text-[28px] [.light_&]:text-[#1b2b45]">Activity History</h1><p className="mt-0.5 text-[13px] text-[#e6edff] sm:text-sm [.light_&]:text-[#526782]">View and track all system events, document processing activities, and user actions.</p></div>
    </section>
  );
}

function normalizeHistoryEvent(event, index) {
  const date = event.timestamp ? new Date(event.timestamp) : null;
  const ts = date && !Number.isNaN(date.getTime())
    ? `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
    : '';

  return {
    id: String(event.id || `history-${index}`),
    docId: String(event.documentId || ''),
    type: String(event.type || 'Activity'),
    file: String(event.fileName || 'Document'),
    status: String(event.status || 'Success'),
    ts,
    user: String(event.user || 'system'),
    actorUserId: String(event.actorUserId || ''),
    eventType: String(event.eventType || ''),
    changes: Array.isArray(event.changes) ? event.changes : [],
    desc: String(event.description || ''),
  };
}

export default function HistoryPage() {
  const now = new Date();
  const today = dateToString(now);

  const [filters, setFilters] = useState(initialFilters);
  const [serverEvents, setServerEvents] = useState([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPage, setHistoryPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [loadingHistory, setLoadingHistory] = useState(!demoMode);

  const historyDateKey = JSON.stringify([
    filters.dateRange.preset,
    filters.dateRange.from,
    filters.dateRange.to,
  ]);

  const historyDateKeyRef = useRef(historyDateKey);
  useEffect(() => {
    historyDateKeyRef.current = historyDateKey;
  }, [historyDateKey]);

  // Reload page 1 whenever the selected date range changes.
  useEffect(() => {
    if (demoMode) {
      return undefined;
    }

    let active = true;
    const requestKey = historyDateKey;

    const loadFirstPage = async () => {
      setLoadingHistory(true);
      setLoadingMore(false);
      setServerEvents([]);
      setHistoryTotal(0);
      setHistoryPage(1);
      setLoadError('');

      try {
        const dateParams = createDateRangeParams(filters.dateRange);
        const payload = await getHistory({
          page: 1,
          limit: 100,
          ...dateParams,
        });

        if (!active || requestKey !== historyDateKeyRef.current) return;

        const responseEvents = Array.isArray(payload?.events)
          ? payload.events
          : [];

        setServerEvents(responseEvents.map(normalizeHistoryEvent));
        setHistoryTotal(Number(
          payload?.pagination?.total ?? responseEvents.length
        ));
        setHistoryPage(1);
      } catch (error) {
        if (active && requestKey === historyDateKeyRef.current) {
          setLoadError(
            getApiErrorMessage(error, 'Unable to load activity history.')
          );
        }
      } finally {
        if (active && requestKey === historyDateKeyRef.current) {
          setLoadingHistory(false);
        }
      }
    };

    loadFirstPage();

    return () => {
      active = false;
    };
  }, [
    historyDateKey,
    filters.dateRange,
  ]);

  const loadOlderEvents = async () => {
    if (
      demoMode ||
      loadingMore ||
      serverEvents.length >= historyTotal
    ) {
      return;
    }

    const requestKey = historyDateKey;
    setLoadingMore(true);
    setLoadError('');

    try {
      const nextPage = historyPage + 1;
      const dateParams = createDateRangeParams(filters.dateRange);

      const payload = await getHistory({
        page: nextPage,
        limit: 100,
        ...dateParams,
      });

      if (requestKey !== historyDateKeyRef.current) return;

      const responseEvents = Array.isArray(payload?.events)
        ? payload.events
        : [];

      const normalized = responseEvents.map(normalizeHistoryEvent);

      setServerEvents((current) => {
        const knownIds = new Set(current.map((event) => event.id));
        return [
          ...current,
          ...normalized.filter((event) => !knownIds.has(event.id)),
        ];
      });

      setHistoryPage(nextPage);
      setHistoryTotal(Number(
        payload?.pagination?.total ?? historyTotal
      ));
    } catch (error) {
      if (requestKey === historyDateKeyRef.current) {
        setLoadError(
          getApiErrorMessage(error, 'Unable to load older activity events.')
        );
      }
    } finally {
      if (requestKey === historyDateKeyRef.current) {
        setLoadingMore(false);
      }
    }
  };

  const events = demoMode ? DEMO_EVENTS : serverEvents;
  const query = filters.q.trim().toLowerCase();

  const dateParams = resolveHistoryDateParams(filters.dateRange);
  const lowerTimestamp = dateParams.date_from
    ? Date.parse(dateParams.date_from)
    : Number.NEGATIVE_INFINITY;
  const upperTimestamp = dateParams.date_to
    ? Date.parse(dateParams.date_to)
    : Number.POSITIVE_INFINITY;

  const filteredEvents = events.filter((event) => {
    const queryMatch = !query || [
      event.file,
      event.type,
      event.id,
      event.docId,
    ].some((value) =>
      String(value || '').toLowerCase().includes(query)
    );

    const typeMatch =
      filters.type === 'All Event Types' ||
      event.type === filters.type;

    const statusMatch =
      filters.status === 'All Status' ||
      event.status === filters.status;

    const eventTimestamp = Date.parse(
      String(event.ts || '').replace(' ', 'T')
    );

    const dateMatch =
      Number.isFinite(eventTimestamp) &&
      eventTimestamp >= lowerTimestamp &&
      eventTimestamp < upperTimestamp;

    return queryMatch && typeMatch && statusMatch && dateMatch;
  });

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') {
        setFilters((current) => ({
          ...current,
          openDropdown: null,
        }));
      }
    };

    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, []);

  const selectedEvent =
    filteredEvents.find((event) => event.id === filters.selected) || null;

  const asOf = demoMode
    ? '2023-09-07 14:33'
    : `${today} ${pad2(now.getHours())}:${pad2(now.getMinutes())}`;

  const clearFilters = () => setFilters({
    ...initialFilters,
    dateRange: { ...DEFAULT_HISTORY_DATE_RANGE },
  });

  const toggleExpanded = (id) => setFilters((current) => ({
    ...current,
    expanded: current.expanded.includes(id)
      ? current.expanded.filter((item) => item !== id)
      : [...current.expanded, id],
  }));

  return (
    <div className="flex min-h-dvh min-w-0 flex-col scroll-smooth overflow-x-hidden bg-[#050b1f] text-[#e6eeff] [.light_&]:bg-[#eef3fb] [.light_&]:text-[#1b2b45] motion-reduce:scroll-auto">
      <main className="mx-auto flex min-h-dvh min-w-0 w-full max-w-[1920px] flex-1 flex-col gap-4 p-3 sm:gap-5 sm:p-4 xl:px-5 xl:pb-5 2xl:px-7 2xl:pt-6">
        <div className="flex">
          <Link
            to="/home"
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-blue-400/40 bg-[#0b2454] px-3 text-xs font-semibold text-white transition hover:-translate-y-0.5 hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400 [.light_&]:border-[#d2deef] [.light_&]:bg-white [.light_&]:text-[#203653]"
          >
            {"\u2190"} Back to Home
          </Link>
        </div>
        <Header/>

        <HistoryFilters
          filters={filters}
          setFilters={setFilters}
          onClear={clearFilters}
          dateRange={filters.dateRange}
          onDateRangeChange={(dateRange) => setFilters((current) => ({
            ...current,
            dateRange,
            selected: null,
          }))}
        />

        <section className="grid flex-1 grid-cols-1 items-stretch gap-[17px] lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] xl:grid-cols-[minmax(0,1.62fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            {loadError && (
              <p role="alert" className="mb-2 rounded-lg border border-rose-500/40 bg-rose-950/40 px-3 py-2 text-sm text-rose-200">
                {loadError}
              </p>
            )}

            <ActivityTimeline
              events={filteredEvents}
              totalEvents={demoMode ? filteredEvents.length : historyTotal}
              selected={filters.selected}
              expanded={filters.expanded}
              onSelect={(id) => setFilters((current) => ({
                ...current,
                selected: id,
              }))}
              onToggle={toggleExpanded}
              demoMode={demoMode}
              asOf={asOf}
              loading={loadingHistory}
            />

            {!demoMode && serverEvents.length < historyTotal && (
              <div className="mt-3 flex justify-center">
                <button
                  type="button"
                  onClick={loadOlderEvents}
                  disabled={loadingMore || loadingHistory}
                  className="rounded-lg border border-blue-400/40 bg-[#0b2454] px-4 py-2 text-xs font-semibold text-white transition hover:bg-blue-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400 disabled:cursor-wait disabled:opacity-60 [.light_&]:border-[#d2deef] [.light_&]:bg-white [.light_&]:text-[#203653]"
                >
                  {loadingMore
                    ? 'Loading older events…'
                    : `Load older events (${historyTotal - serverEvents.length} remaining)`}
                </button>
              </div>
            )}
          </div>

          <EventDetails
            event={selectedEvent}
            onClose={() => setFilters((current) => ({
              ...current,
              selected: null,
            }))}
          />
        </section>
      </main>
    </div>
  );
}
