import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getHistory } from "../../services/idpService.js";

const STORAGE_KEY = "idp.notifications.enabled";
const LAST_SEEN_KEY = "idp.notifications.lastSeen";

function readPreference() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

function relativeTime(timestamp) {
  if (!timestamp) return "Time unavailable";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function presentEvent(event, index) {
  const type = String(event.type || "Document activity");
  const failed = /failed/i.test(type) || /failed/i.test(event.status || "");
  const uploaded = /uploaded/i.test(type);
  const completed = /completed/i.test(type);
  return {
    id: String(event.id || `activity-${index}`),
    title: type,
    detail: event.description || `${event.fileName || "Document"} · ${event.status || "Activity"}`,
    time: relativeTime(event.timestamp),
    icon: failed ? "i-warn" : uploaded ? "i-upload" : completed ? "i-doc" : "i-bars",
    tone: failed ? "rose" : uploaded ? "blue" : completed ? "green" : "purple",
    isNew: false,
    timestamp: event.timestamp || null,
    system: String(event.user || "").toLowerCase() === "system",
  };
}

const iconTone = {
  blue: "bg-[linear-gradient(135deg,#2563eb,#06b6d4)]",
  purple: "bg-[linear-gradient(135deg,#6d28d9,#8b5cf6)]",
  green: "bg-[linear-gradient(135deg,#059669,#22c55e)]",
  rose: "bg-[linear-gradient(135deg,#e11d48,#fb7185)]",
};

export default function Notifications({
  isOpen,
  onToggle,
  onClose,
  badgeVisible,
}) {
  const [items, setItems] = useState([]);
  const [filter, setFilter] = useState("all");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notificationsEnabled, setNotificationsEnabled] = useState(readPreference);
  const emptyRef = useRef(null);
  const buttonRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    let active = true;
    // Re-opening the panel starts a fresh request and must immediately show its loading state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);

    getHistory({ page: 1, limit: 30 })
      .then((payload) => {
        if (!active) return;
        const events = Array.isArray(payload?.events) ? payload.events : [];
        let lastSeen = 0;
        try {
          lastSeen = Date.parse(window.localStorage.getItem(LAST_SEEN_KEY) || "") || 0;
        } catch {
          lastSeen = 0;
        }
        const nextItems = events.map((event, index) => {
          const item = presentEvent(event, index);
          const eventTime = Date.parse(item.timestamp || "") || 0;
          return {
            ...item,
            isNew: eventTime > lastSeen || (!lastSeen && index < 5),
          };
        });
        setItems(nextItems);
        setLoadError("");
      })
      .catch((error) => {
        if (!active) return;
        setLoadError(
          error?.response?.data?.message ||
          "Recent activity notifications could not be loaded."
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [isOpen]);

  useEffect(() => {
    const sync = (event) => {
      if (typeof event?.detail?.enabled === "boolean") {
        setNotificationsEnabled(event.detail.enabled);
      } else {
        setNotificationsEnabled(readPreference());
      }
    };
    window.addEventListener("idp:notifications-preference", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("idp:notifications-preference", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === "Escape" && isOpen) {
        onClose();
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [isOpen, onClose]);

  const visibleItems = items.filter((item) => {
    if (filter === "unread") return item.isNew;
    if (filter === "system") return item.system;
    return true;
  });

  const markRead = () => {
    try {
      window.localStorage.setItem(LAST_SEEN_KEY, new Date().toISOString());
    } catch {
      // Read state remains session-local when browser storage is unavailable.
    }
    setItems((current) => current.map((item) => ({ ...item, isNew: false })));
    setStatus("All notifications marked as read.");
  };

  const clearAll = () => {
    if (!items.length) return;
    try {
      window.localStorage.setItem(LAST_SEEN_KEY, new Date().toISOString());
    } catch {
      // Clearing remains session-local when browser storage is unavailable.
    }
    setItems([]);
    setStatus("Recent notifications cleared from this view.");
    window.requestAnimationFrame(() => emptyRef.current?.focus());
  };

  const togglePreference = () => {
    const next = !notificationsEnabled;
    setNotificationsEnabled(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      // Preference remains active for this session if storage is unavailable.
    }
    window.dispatchEvent(
      new CustomEvent("idp:notifications-preference", {
        detail: { enabled: next },
      })
    );
    setStatus(next ? "In-app notifications enabled." : "In-app notifications disabled.");
  };

  return (
    <div className="notification-wrap relative flex-none">
      <button
        ref={buttonRef}
        id="notificationButton"
        type="button"
        className="relative grid size-9 flex-none cursor-pointer place-items-center rounded-full border border-transparent bg-transparent text-[#9cc8ff] transition duration-200 hover:border-blue-400/45 hover:bg-blue-500/[.18] hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan-400 min-[701px]:size-[38px]"
        aria-label="Open in-app notifications"
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-controls="notificationPanel"
        onClick={onToggle}
      >
        <svg className="size-[19px]" aria-hidden="true"><use href="#i-bell" /></svg>
        {badgeVisible && items.some((item) => item.isNew) ? (
          <span className="absolute right-0 top-px size-2.5 rounded-full border-2 border-[#060f42] bg-[#f43f5e]" aria-label="Unread notifications" />
        ) : null}
      </button>

      <section
        id="notificationPanel"
        className="absolute right-0 top-[calc(100%+10px)] z-[60] w-[min(360px,calc(100vw-24px))] overflow-hidden rounded-xl border border-[rgba(70,136,255,.45)] bg-[linear-gradient(180deg,rgba(5,24,82,.98),rgba(4,15,54,.99))] text-[#e5efff] shadow-[0_18px_42px_rgba(0,0,0,.42),0_0_22px_rgba(36,99,235,.2)] [.light_&]:border-[#cbd8ea] [.light_&]:bg-white [.light_&]:text-[#203653] [.light_&]:shadow-[0_8px_24px_rgba(15,35,70,.12)]"
        aria-label="Notifications"
        hidden={!isOpen}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-blue-400/[.18] px-4 py-3">
          <div>
            <h2 className="text-base font-semibold">Notifications</h2>
            <p className="mt-0.5 text-[10px] text-[#9fb4df]">Recent document activity</p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={notificationsEnabled}
            onClick={togglePreference}
            className={`relative inline-flex h-6 w-11 items-center rounded-full border transition-colors ${notificationsEnabled ? "border-blue-400 bg-blue-600" : "border-slate-500 bg-slate-600"}`}
          >
            <span className={`size-4 rounded-full bg-white shadow transition-transform ${notificationsEnabled ? "translate-x-5" : "translate-x-1"}`} />
          </button>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-blue-400/[.14] px-4 py-2">
          <span className="text-[10px] text-[#a9bde8]">
            In-app notifications {notificationsEnabled ? "On" : "Off"}
          </span>
          <div className="flex gap-3">
            <button type="button" onClick={markRead} className="text-[10px] text-[#79aefc] hover:text-white">Mark read</button>
            <button type="button" onClick={clearAll} disabled={!items.length} className="text-[10px] text-[#79aefc] hover:text-white disabled:opacity-40">Clear</button>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-1 border-b border-blue-400/[.16] p-2" role="tablist" aria-label="Notification filters">
          {[
            ["all", `All (${items.length})`],
            ["unread", `Unread (${items.filter((item) => item.isNew).length})`],
            ["system", `System (${items.filter((item) => item.system).length})`],
          ].map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={filter === key}
              onClick={() => setFilter(key)}
              className={`rounded-md px-1 py-1.5 text-[10px] ${filter === key ? "border border-blue-500/45 bg-blue-500/25 text-white" : "text-[#a9bde8] hover:bg-blue-500/15"}`}
            >
              {label}
            </button>
          ))}
        </div>

        <div id="notificationList" className="max-h-[360px] overflow-y-auto">
          {!notificationsEnabled ? (
            <p className="px-4 py-8 text-center text-[11px] text-[#9fb4df]">
              Notifications are disabled. Turn the switch on to display recent activity.
            </p>
          ) : loading ? (
            <p className="px-4 py-8 text-center text-[11px] text-[#9fb4df]">Loading recent activity…</p>
          ) : loadError ? (
            <p className="px-4 py-8 text-center text-[11px] text-rose-200">{loadError}</p>
          ) : visibleItems.length ? (
            visibleItems.map((item) => (
              <article key={item.id} className="grid grid-cols-[8px_34px_minmax(0,1fr)_auto] items-start gap-[9px] border-b border-blue-400/[.14] p-3 last:border-b-0">
                <span className="mt-[7px] size-2 rounded-full bg-[#38bdf8]" aria-hidden="true" />
                <span className={`grid size-[34px] place-items-center rounded-full ${iconTone[item.tone] || iconTone.blue} text-white`} aria-hidden="true">
                  <svg className="size-[19px]"><use href={`#${item.icon}`} /></svg>
                </span>
                <div className="min-w-0">
                  <strong className="mb-[3px] block break-words text-[11px] text-[#f8fbff]">{item.title}</strong>
                  <span className="block break-words text-[9px] leading-[1.4] text-[#9fb4df]">{item.detail}</span>
                  <small className="mt-1 block text-[9px] text-[#7f96c5]">{item.time}</small>
                </div>
                {item.isNew ? <span className="self-center rounded-full bg-[#1683f7] px-[7px] py-1 text-[8px] font-bold text-white">New</span> : <span />}
              </article>
            ))
          ) : (
            <p ref={emptyRef} tabIndex={-1} className="px-4 py-8 text-center text-[11px] text-[#9fb4df]">
              No recent activity notifications to show.
            </p>
          )}
        </div>

        <p className="sr-only" role="status" aria-live="polite">{status}</p>
        <Link
          to="/history"
          onClick={onClose}
          className="m-2.5 flex w-[calc(100%-20px)] items-center justify-center rounded-md border border-blue-500/40 bg-blue-500/10 p-2.5 text-[10px] text-[#dbeafe] hover:bg-blue-500/25"
        >
          View activity history →
        </Link>
      </section>
    </div>
  );
}
