"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getMarketSignals, type MarketSignalItem, type MarketSignalsResponse } from "@/lib/api";

export default function MarketSignalsPage() {
  const [data, setData] = useState<MarketSignalsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getMarketSignals({ limit: 50 })
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load market signals");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signals = data?.signals ?? [];
  const source = data?.source ?? "binance";

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Market signals (Live)</h1>
          <p className="mt-1 text-zinc-400">
            Top movers from Binance 24h — no API key. Use this when Discord/DB are not set up.
          </p>
        </div>
        <Link href="/signals" className="text-sm text-zinc-400 hover:text-white">
          ← Signals (DB)
        </Link>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
          {error}
        </div>
      )}

      {loading && (
        <div className="mt-8 flex justify-center py-12">
          <p className="text-zinc-500">Loading market signals…</p>
        </div>
      )}

      {!loading && !error && (
        <>
          <p className="mt-4 text-sm text-zinc-500">
            {data?.total ?? 0} signal(s) from {source}
          </p>

          <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-700">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="border-b border-zinc-700 bg-zinc-800/80">
                <tr>
                  <th className="px-4 py-3 font-medium text-zinc-300">Symbol</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Direction</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">24h %</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Confidence</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Price</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Source</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Preview</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-700">
                {signals.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                      No market signals. Check backend and network.
                    </td>
                  </tr>
                ) : (
                  signals.map((s) => <MarketSignalRow key={s.id} signal={s} />)
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function MarketSignalRow({ signal }: { signal: MarketSignalItem }) {
  const pct = signal.price_change_pct;
  const isPositive = pct != null && pct >= 0;

  return (
    <tr className="bg-zinc-900/30 hover:bg-zinc-800/50">
      <td className="px-4 py-3 font-medium text-white">{signal.symbol}</td>
      <td className="px-4 py-3">
        <span className={signal.direction === "long" ? "text-emerald-400" : "text-red-400"}>
          {signal.direction}
        </span>
      </td>
      <td className={`px-4 py-3 font-medium ${isPositive ? "text-emerald-400" : "text-red-400"}`}>
        {pct != null ? `${pct > 0 ? "+" : ""}${pct}%` : "—"}
      </td>
      <td className="px-4 py-3 text-zinc-300">
        {signal.confidence_pct != null ? `${signal.confidence_pct}%` : "—"}
      </td>
      <td className="px-4 py-3 text-zinc-300">
        {signal.entry_price != null
          ? signal.entry_price.toLocaleString(undefined, { maximumFractionDigits: 6 })
          : "—"}
      </td>
      <td className="px-4 py-3 text-zinc-400">{signal.source}</td>
      <td className="max-w-xs truncate px-4 py-3 text-zinc-500" title={signal.raw_text_preview}>
        {signal.raw_text_preview}
      </td>
    </tr>
  );
}
