"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { getAdvice, type AdviceItem } from "@/lib/api";

function fmtNum(v: number) {
  if (v >= 1e6) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (v >= 100) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (v >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return v.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

export default function AdvicePage() {
  const [data, setData] = useState<{ advice: AdviceItem[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getAdvice()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load advice"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const advice = data?.advice ?? [];

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Advice</h1>
          <p className="mt-1 text-zinc-400">
            One recommendation per commodity: LONG or SHORT with target price and stop loss (from DB, Binance, CoinGecko, Finnhub).
          </p>
        </div>
        <Link href="/" className="text-sm text-zinc-400 hover:text-white">
          ← Dashboard
        </Link>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
          {error}
        </div>
      )}

      {loading && (
        <div className="mt-8 flex justify-center py-12">
          <p className="text-zinc-500">Loading advice…</p>
        </div>
      )}

      {!loading && !error && (
        <>
          <p className="mt-4 text-sm text-zinc-500">
            {data?.total ?? 0} recommendation(s)
          </p>

          <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-700">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="border-b border-zinc-700 bg-zinc-800/80">
                <tr>
                  <th className="px-4 py-3 font-medium text-zinc-300">Symbol</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Advice</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Entry</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Stop loss</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Target</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Confidence</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Sources</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Rationale</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-700">
                {advice.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-zinc-500">
                      No advice yet. Add tracked symbols via ADVICE_TRACKED_SYMBOLS and ensure backend is running.
                    </td>
                  </tr>
                ) : (
                  advice.map((a) => (
                    <tr key={a.symbol} className="bg-zinc-900/30 hover:bg-zinc-800/50">
                      <td className="px-4 py-3 font-medium text-white">{a.symbol}</td>
                      <td className="px-4 py-3">
                        <span
                          className={
                            a.direction === "long"
                              ? "font-medium text-emerald-400"
                              : "font-medium text-red-400"
                          }
                        >
                          {a.direction.toUpperCase()}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-zinc-300">{fmtNum(a.entry_price)}</td>
                      <td className="px-4 py-3 text-red-300/90">{fmtNum(a.stop_loss)}</td>
                      <td className="px-4 py-3 text-emerald-300/90">{fmtNum(a.target_price)}</td>
                      <td className="px-4 py-3 text-zinc-300">{a.confidence_pct}%</td>
                      <td className="px-4 py-3 text-zinc-400">
                        {a.sources_used?.length ? a.sources_used.join(", ") : "—"}
                      </td>
                      <td className="max-w-xs truncate px-4 py-3 text-zinc-400" title={a.rationale}>
                        {a.rationale || "—"}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
