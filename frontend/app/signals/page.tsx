"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { getSignals, type SignalItem, type SignalsResponse } from "@/lib/api";

const PAGE_SIZE = 20;

export default function SignalsPage() {
  const searchParams = useSearchParams();
  const [data, setData] = useState<SignalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [symbol, setSymbol] = useState("");
  const [direction, setDirection] = useState("");
  const [source, setSource] = useState("");
  const [minConfidence, setMinConfidence] = useState("");

  useEffect(() => {
    const fromUrl = searchParams.get("source");
    if (fromUrl) setSource(fromUrl);
  }, [searchParams]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSignals({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      symbol: symbol || undefined,
      direction: direction || undefined,
      source: source || undefined,
      min_confidence: minConfidence !== "" ? parseInt(minConfidence, 10) : undefined,
    })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load signals");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [page, symbol, direction, source, minConfidence]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;
  const signals = data?.signals ?? [];

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Signals</h1>
          <p className="mt-1 text-zinc-400">Parsed trading signals from Discord.</p>
        </div>
        <Link
          href="/"
          className="text-sm text-zinc-400 hover:text-white"
        >
          ← Dashboard
        </Link>
      </div>

      <div className="mt-6 flex flex-wrap gap-4">
        <input
          type="text"
          placeholder="Symbol (e.g. BTC)"
          value={symbol}
          onChange={(e) => {
            setSymbol(e.target.value);
            setPage(0);
          }}
          className="rounded-lg border border-zinc-600 bg-zinc-800/50 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <select
          value={direction}
          onChange={(e) => {
            setDirection(e.target.value);
            setPage(0);
          }}
          className="rounded-lg border border-zinc-600 bg-zinc-800/50 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All directions</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>
        <select
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setPage(0);
          }}
          className="rounded-lg border border-zinc-600 bg-zinc-800/50 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All sources</option>
          <option value="discord">Discord</option>
          <option value="tradingview">TradingView</option>
          <option value="opportunity_scanner">Opportunity scanner</option>
          <option value="manual">Manual</option>
          <option value="x_twitter">X / Twitter</option>
        </select>
        <input
          type="number"
          min={0}
          max={100}
          placeholder="Min confidence %"
          value={minConfidence}
          onChange={(e) => {
            setMinConfidence(e.target.value);
            setPage(0);
          }}
          className="w-32 rounded-lg border border-zinc-600 bg-zinc-800/50 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
          {error}
        </div>
      )}

      {loading && (
        <div className="mt-8 flex justify-center py-12">
          <p className="text-zinc-500">Loading signals…</p>
        </div>
      )}

      {!loading && !error && (
        <>
          <p className="mt-4 text-sm text-zinc-500">
            {data?.total ?? 0} signal(s) total
          </p>

          <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-700">
            <table className="w-full min-w-[800px] text-left text-sm">
              <thead className="border-b border-zinc-700 bg-zinc-800/80">
                <tr>
                  <th className="px-4 py-3 font-medium text-zinc-300">Symbol</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Direction</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Source</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Confidence</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Entry</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">SL</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">TP1</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Method</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Parsed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-700">
                {signals.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-zinc-500">
                      No signals found.
                    </td>
                  </tr>
                ) : (
                  signals.map((s) => (
                    <SignalRow key={s.id} signal={s} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="mt-4 flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded-lg border border-zinc-600 bg-zinc-800 px-3 py-1.5 text-sm text-white disabled:opacity-50 hover:bg-zinc-700 disabled:hover:bg-zinc-800"
              >
                Previous
              </button>
              <span className="text-zinc-400">
                Page {page + 1} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="rounded-lg border border-zinc-600 bg-zinc-800 px-3 py-1.5 text-sm text-white disabled:opacity-50 hover:bg-zinc-700 disabled:hover:bg-zinc-800"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SignalRow({ signal }: { signal: SignalItem }) {
  const fmt = (v: number | null) => (v != null ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : "—");
  const parsedAt = signal.parsed_at
    ? new Date(signal.parsed_at).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })
    : "—";
  const confidence =
    signal.signal_completeness_pct != null
      ? `${signal.signal_completeness_pct}%`
      : signal.llm_confidence != null
        ? `${Math.round(signal.llm_confidence * 100)}%`
        : signal.confidence_wording || "—";

  return (
    <tr className="bg-zinc-900/30 hover:bg-zinc-800/50">
      <td className="px-4 py-3 font-medium text-white">{signal.symbol}</td>
      <td className="px-4 py-3">
        <span
          className={
            signal.direction === "long"
              ? "text-emerald-400"
              : "text-red-400"
          }
        >
          {signal.direction}
        </span>
      </td>
      <td className="px-4 py-3 text-zinc-400">{signal.source || "—"}</td>
      <td className="px-4 py-3 text-zinc-300" title={signal.confidence_wording || undefined}>
        {confidence}
      </td>
      <td className="px-4 py-3 text-zinc-300">{fmt(signal.entry_price)}</td>
      <td className="px-4 py-3 text-zinc-300">{fmt(signal.stop_loss)}</td>
      <td className="px-4 py-3 text-zinc-300">{fmt(signal.take_profit_1)}</td>
      <td className="px-4 py-3 text-zinc-400">{signal.parse_method}</td>
      <td className="px-4 py-3 text-zinc-400">{parsedAt}</td>
    </tr>
  );
}
