import { Link } from "react-router-dom";
import ModelPerformanceAnalytics from "../components/Analytics/ModelPerformanceAnalytics.jsx";

export default function ModelPerformancePage() {
  return (
    <div className="min-h-dvh bg-(color:--c-bg) px-3 py-5 text-(color:--c-fg) sm:px-6 sm:py-8">
      <main className="mx-auto max-w-7xl space-y-4">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[.18em] text-cyan-300">Evaluation dashboard</p>
            <h1 className="mt-1 text-2xl font-bold sm:text-3xl">Model Performance</h1>
            <p className="mt-1 text-sm text-(color:--c-muted)">
              Stage 9 model selection and Stage 10 final held-out evaluation.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to="/scanning" className="rounded-lg border border-(color:--c-line) px-4 py-2 text-sm font-semibold transition hover:bg-blue-500/15">Scanning</Link>
            <Link to="/home" className="rounded-lg border border-(color:--c-line) px-4 py-2 text-sm font-semibold transition hover:bg-blue-500/15">Dashboard</Link>
          </div>
        </header>
        <ModelPerformanceAnalytics />
      </main>
    </div>
  );
}
