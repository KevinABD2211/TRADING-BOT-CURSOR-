"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getStats, type Stats } from "@/lib/api";

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getStats()
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load stats");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <p className="text-zinc-500">Loading stats…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
        <p className="font-medium">Could not reach the API</p>
        <p className="mt-1 text-sm">{error}</p>
        <p className="mt-2 text-sm text-zinc-400">
          Ensure the backend is running (e.g. <code className="rounded bg-zinc-800 px-1">uvicorn main:app --port 8000</code>) and{" "}
          <code className="rounded bg-zinc-800 px-1">NEXT_PUBLIC_API_URL</code> points to it.
        </p>
      </div>
    );
  }

  if (!stats) return null;

  const cards = [
    { label: "Raw messages", value: stats.raw_messages_total, color: "border-zinc-600 bg-zinc-800/50" },
    { label: "Signals parsed", value: stats.signals_total, color: "border-blue-600/50 bg-blue-950/30" },
    { label: "Actionable signals", value: stats.signals_actionable, color: "border-emerald-600/50 bg-emerald-950/30" },
    { label: "Executions", value: stats.executions_total ?? 0, color: "border-amber-600/50 bg-amber-950/30" },
    { label: "Mode", value: stats.execution_mode, color: "border-zinc-600 bg-zinc-800/50" },
    { label: "Environment", value: stats.environment, color: "border-zinc-600 bg-zinc-800/50" },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold text-white">Dashboard</h1>
      <p className="mt-1 text-zinc-400">Overview of ingested messages and parsed signals.</p>

      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => (
          <div
            key={c.label}
            className={`rounded-lg border p-4 ${c.color}`}
          >
            <p className="text-sm text-zinc-400">{c.label}</p>
            <p className="mt-1 text-xl font-semibold text-white">
              {typeof c.value === "number" ? c.value.toLocaleString() : c.value}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-8">
        <Link
          href="/signals"
          className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          View all signals →
        </Link>
        <Link
          href="/executions"
          className="ml-4 inline-flex items-center rounded-lg border border-amber-600/50 bg-amber-950/30 px-4 py-2 text-sm font-medium text-white hover:bg-amber-900/50"
        >
          Executions →
        </Link>
        <Link
          href="/sources"
          className="ml-4 inline-flex items-center rounded-lg border border-zinc-600 bg-zinc-800 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700"
        >
          Sources →
        </Link>
        <Link
          href="/trades"
          className="ml-4 inline-flex items-center rounded-lg border border-emerald-600/50 bg-emerald-950/30 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-900/50"
        >
          Trades (Alpaca) →
        </Link>
        <span className="ml-4 text-zinc-500">or</span>
        <Link
          href="/signals/market"
          className="ml-4 inline-flex items-center rounded-lg border border-zinc-600 bg-zinc-800 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700"
        >
          Live market signals →
        </Link>
      </div>
    </div>
  );
}
