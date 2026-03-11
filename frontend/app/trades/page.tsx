"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getAlpacaAccount,
  getAlpacaSuggestions,
  alpacaExecute,
  type AlpacaSuggestion,
  type AlpacaSuggestionsResponse,
  type AlpacaAccount,
} from "@/lib/api";

export default function TradesPage() {
  const [account, setAccount] = useState<AlpacaAccount | null>(null);
  const [data, setData] = useState<AlpacaSuggestionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [execError, setExecError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([getAlpacaAccount(), getAlpacaSuggestions({ limit: 30 })])
      .then(([acc, res]) => {
        setAccount(acc);
        setData(res);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleExecute(s: AlpacaSuggestion, useNotional: boolean) {
    setExecutingId(s.signal_id);
    setExecError(null);
    try {
      await alpacaExecute({
        symbol: s.symbol,
        direction: s.direction,
        signal_id: s.signal_id,
        ...(useNotional
          ? { notional_usd: s.suggested_notional_usd }
          : { quantity: s.suggested_qty ?? 0 }),
      });
      load();
    } catch (e) {
      setExecError(e instanceof Error ? e.message : "Execute failed");
    } finally {
      setExecutingId(null);
    }
  }

  const suggestions = data?.suggestions ?? [];
  const configured = data?.alpaca_configured ?? false;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Trade suggestions (Alpaca)</h1>
          <p className="mt-1 text-zinc-400">
            Stock signals scanned and offered as trades. Execute on Alpaca (paper or live).
          </p>
        </div>
        <Link href="/" className="text-sm text-zinc-400 hover:text-white">
          ← Dashboard
        </Link>
      </div>

      {account && configured && (
        <div className="mt-6 rounded-lg border border-emerald-700/50 bg-emerald-950/30 p-4">
          <h2 className="text-sm font-medium text-emerald-300">Alpaca account</h2>
          <p className="mt-1 text-zinc-400">
            Environment: <span className="text-white">{account.environment ?? "—"}</span>
            {account.buying_power != null && (
              <> · Buying power: <span className="text-white">{account.buying_power}</span></>
            )}
            {account.portfolio_value != null && (
              <> · Portfolio: <span className="text-white">{account.portfolio_value}</span></>
            )}
          </p>
        </div>
      )}

      {!configured && (
        <div className="mt-6 rounded-lg border border-amber-700/50 bg-amber-950/30 p-4 text-amber-200">
          <p className="font-medium">Alpaca not configured</p>
          <p className="mt-1 text-sm text-zinc-400">
            Set ALPACA_API_KEY and ALPACA_API_SECRET in the backend .env to connect. Suggestions still show stock signals from the DB.
          </p>
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
          {error}
        </div>
      )}

      {execError && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
          {execError}
        </div>
      )}

      {loading && (
        <div className="mt-8 flex justify-center py-12">
          <p className="text-zinc-500">Loading suggestions…</p>
        </div>
      )}

      {!loading && !error && (
        <>
          <p className="mt-4 text-sm text-zinc-500">
            {data?.total ?? 0} stock signal(s) · Only actionable stock signals are shown.
          </p>
          <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-700">
            <table className="w-full min-w-[800px] text-left text-sm">
              <thead className="border-b border-zinc-700 bg-zinc-800/80">
                <tr>
                  <th className="px-4 py-3 font-medium text-zinc-300">Symbol</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Direction</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Entry / Current</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Suggested $</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Conf.</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Source</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-700">
                {suggestions.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-zinc-500">
                      No stock signals. Add signals with asset_type=stock (e.g. from Discord or manual).
                    </td>
                  </tr>
                ) : (
                  suggestions.map((s) => (
                    <SuggestionRow
                      key={s.signal_id}
                      suggestion={s}
                      onExecuteNotional={() => handleExecute(s, true)}
                      onExecuteQty={() => handleExecute(s, false)}
                      executing={executingId === s.signal_id}
                      canExecute={configured}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
          <div className="mt-6 flex gap-4">
            <Link
              href="/executions"
              className="rounded-lg border border-zinc-600 bg-zinc-800 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700"
            >
              View executions
            </Link>
          </div>
        </>
      )}
    </div>
  );
}

function SuggestionRow({
  suggestion: s,
  onExecuteNotional,
  onExecuteQty,
  executing,
  canExecute,
}: {
  suggestion: AlpacaSuggestion;
  onExecuteNotional: () => void;
  onExecuteQty: () => void;
  executing: boolean;
  canExecute: boolean;
}) {
  const price = s.current_price ?? s.entry_price;
  const priceStr = price != null ? `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}` : "—";
  return (
    <tr className="bg-zinc-900/30 hover:bg-zinc-800/50">
      <td className="px-4 py-3 font-medium text-white">{s.symbol}</td>
      <td className="px-4 py-3">
        <span className={s.direction === "long" ? "text-emerald-400" : "text-red-400"}>
          {s.direction}
        </span>
      </td>
      <td className="px-4 py-3 text-zinc-300">
        {s.entry_price != null && `$${s.entry_price.toFixed(2)}`}
        {s.current_price != null && ` / ${s.current_price.toFixed(2)}`}
        {priceStr === "—" && priceStr}
      </td>
      <td className="px-4 py-3 text-zinc-300">
        ${s.suggested_notional_usd.toLocaleString(undefined, { maximumFractionDigits: 2 })}
        {s.suggested_qty != null && (
          <span className="ml-1 text-zinc-500">({s.suggested_qty.toFixed(4)} shares)</span>
        )}
      </td>
      <td className="px-4 py-3 text-zinc-400">{s.confidence_pct != null ? `${s.confidence_pct}%` : "—"}</td>
      <td className="px-4 py-3 text-zinc-400">{s.source}</td>
      <td className="px-4 py-3">
        {canExecute ? (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onExecuteNotional}
              disabled={executing}
              className="rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {executing ? "…" : `$${s.suggested_notional_usd}`}
            </button>
            {s.suggested_qty != null && s.suggested_qty > 0 && (
              <button
                type="button"
                onClick={onExecuteQty}
                disabled={executing}
                className="rounded border border-zinc-600 bg-zinc-800 px-2 py-1 text-xs text-white hover:bg-zinc-700 disabled:opacity-50"
              >
                {executing ? "…" : `${s.suggested_qty.toFixed(2)} sh`}
              </button>
            )}
          </div>
        ) : (
          <span className="text-zinc-500 text-xs">Configure Alpaca</span>
        )}
      </td>
    </tr>
  );
}
