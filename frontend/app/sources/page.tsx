"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getSources, type SourcesResponse } from "@/lib/api";

export default function SourcesPage() {
  const [data, setData] = useState<SourcesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getSources()
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load sources");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sources = data?.sources ?? [];
  const total = sources.reduce((s, x) => s + x.count, 0);

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Sources</h1>
          <p className="mt-1 text-zinc-400">
            Signal counts by source. Filter signals by source on the Signals page.
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
          <p className="text-zinc-500">Loading sources…</p>
        </div>
      )}

      {!loading && !error && (
        <>
          <p className="mt-4 text-sm text-zinc-500">
            {total} signal(s) across {sources.length} source(s). Market (Binance) is live-only and not stored.
          </p>
          <div className="mt-6 rounded-lg border border-zinc-700 overflow-hidden">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-zinc-700 bg-zinc-800/80">
                <tr>
                  <th className="px-4 py-3 font-medium text-zinc-300">Source</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Count</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-700">
                {sources.length === 0 ? (
                  <tr>
                    <td colSpan={3} className="px-4 py-8 text-center text-zinc-500">
                      No sources yet. Ingest signals (e.g. Discord bot) or use Market (Live) for Binance.
                    </td>
                  </tr>
                ) : (
                  sources.map((s) => (
                    <tr key={s.source} className="bg-zinc-900/30 hover:bg-zinc-800/50">
                      <td className="px-4 py-3 font-medium text-white capitalize">{s.source.replace(/_/g, " ")}</td>
                      <td className="px-4 py-3 text-zinc-300">{s.count.toLocaleString()}</td>
                      <td className="px-4 py-3">
                        <Link
                          href={`/signals?source=${encodeURIComponent(s.source)}`}
                          className="text-blue-400 hover:text-blue-300 text-sm"
                        >
                          View signals →
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <div className="mt-6 flex gap-4">
            <Link
              href="/signals"
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
            >
              All signals
            </Link>
            <Link
              href="/signals/market"
              className="rounded-lg border border-zinc-600 bg-zinc-800 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700"
            >
              Market (Binance) live
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
