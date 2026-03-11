"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  createExecution,
  getExecutions,
  type ExecutionItem,
  type ExecutionsResponse,
} from "@/lib/api";

const PAGE_SIZE = 20;

export default function ExecutionsPage() {
  const [data, setData] = useState<ExecutionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [symbol, setSymbol] = useState("");
  const [status, setStatus] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    getExecutions({
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      symbol: symbol || undefined,
      status: status || undefined,
    })
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [page, symbol, status]);

  useEffect(() => {
    load();
  }, [load]);

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;
  const executions = data?.executions ?? [];

  async function handleCreate(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const sym = (form.querySelector('[name="symbol"]') as HTMLInputElement)?.value?.trim();
    const dir = (form.querySelector('[name="direction"]') as HTMLSelectElement)?.value;
    const qty = parseFloat((form.querySelector('[name="quantity"]') as HTMLInputElement)?.value || "0");
    const price = parseFloat((form.querySelector('[name="price"]') as HTMLInputElement)?.value || "0");
    const notes = (form.querySelector('[name="notes"]') as HTMLInputElement)?.value?.trim() || undefined;
    if (!sym || !dir) {
      setCreateError("Symbol and direction required.");
      return;
    }
    setSubmitting(true);
    setCreateError(null);
    try {
      await createExecution({
        symbol: sym,
        direction: dir,
        quantity: qty > 0 ? qty : undefined,
        price: price > 0 ? price : undefined,
        notes,
      });
      load();
      form.reset();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create execution");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Executions</h1>
          <p className="mt-1 text-zinc-400">Trade executions (paper or live). Record paper trades from the form below.</p>
        </div>
        <Link href="/" className="text-sm text-zinc-400 hover:text-white">
          ← Dashboard
        </Link>
      </div>

      <div className="mt-8 rounded-lg border border-zinc-700 bg-zinc-800/30 p-4">
        <h2 className="text-lg font-medium text-white">Record paper execution</h2>
        <form onSubmit={handleCreate} className="mt-4 flex flex-wrap gap-4">
          <input
            name="symbol"
            placeholder="Symbol (e.g. BTC)"
            required
            className="rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500"
          />
          <select
            name="direction"
            required
            className="rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-sm text-white"
          >
            <option value="long">Long</option>
            <option value="short">Short</option>
          </select>
          <input
            name="quantity"
            type="number"
            step="any"
            min="0"
            placeholder="Quantity"
            className="w-28 rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500"
          />
          <input
            name="price"
            type="number"
            step="any"
            min="0"
            placeholder="Price (optional)"
            className="w-32 rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500"
          />
          <input
            name="notes"
            placeholder="Notes (optional)"
            className="w-48 rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500"
          />
          <button
            type="submit"
            disabled={submitting}
            className="rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Record execution"}
          </button>
        </form>
        {createError && (
          <p className="mt-2 text-sm text-red-400">{createError}</p>
        )}
      </div>

      <div className="mt-6 flex flex-wrap gap-4">
        <input
          type="text"
          placeholder="Filter by symbol"
          value={symbol}
          onChange={(e) => {
            setSymbol(e.target.value);
            setPage(0);
          }}
          className="rounded border border-zinc-600 bg-zinc-800/50 px-3 py-2 text-sm text-white placeholder-zinc-500"
        />
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(0);
          }}
          className="rounded border border-zinc-600 bg-zinc-800/50 px-3 py-2 text-sm text-white"
        >
          <option value="">All statuses</option>
          <option value="filled">Filled</option>
          <option value="pending">Pending</option>
          <option value="cancelled">Cancelled</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-red-400">
          {error}
        </div>
      )}

      {loading && (
        <div className="mt-8 flex justify-center py-12">
          <p className="text-zinc-500">Loading executions…</p>
        </div>
      )}

      {!loading && !error && (
        <>
          <p className="mt-4 text-sm text-zinc-500">
            {data?.total ?? 0} execution(s)
          </p>
          <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-700">
            <table className="w-full min-w-[700px] text-left text-sm">
              <thead className="border-b border-zinc-700 bg-zinc-800/80">
                <tr>
                  <th className="px-4 py-3 font-medium text-zinc-300">Symbol</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Direction</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Side</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Quantity</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Price</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Notional</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Status</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Broker</th>
                  <th className="px-4 py-3 font-medium text-zinc-300">Executed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-700">
                {executions.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-4 py-8 text-center text-zinc-500">
                      No executions yet. Record a paper execution above (requires DB).
                    </td>
                  </tr>
                ) : (
                  executions.map((ex) => <ExecutionRow key={ex.id} execution={ex} />)
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
                className="rounded border border-zinc-600 bg-zinc-800 px-3 py-1.5 text-sm text-white disabled:opacity-50 hover:bg-zinc-700"
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
                className="rounded border border-zinc-600 bg-zinc-800 px-3 py-1.5 text-sm text-white disabled:opacity-50 hover:bg-zinc-700"
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

function ExecutionRow({ execution }: { execution: ExecutionItem }) {
  const fmt = (v: number | null) =>
    v != null ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : "—";
  const at = execution.executed_at || execution.created_at;
  const time = at ? new Date(at).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }) : "—";

  return (
    <tr className="bg-zinc-900/30 hover:bg-zinc-800/50">
      <td className="px-4 py-3 font-medium text-white">{execution.symbol}</td>
      <td className="px-4 py-3">
        <span className={execution.direction === "long" ? "text-emerald-400" : "text-red-400"}>
          {execution.direction}
        </span>
      </td>
      <td className="px-4 py-3 text-zinc-300">{execution.side}</td>
      <td className="px-4 py-3 text-zinc-300">{fmt(execution.quantity)}</td>
      <td className="px-4 py-3 text-zinc-300">{fmt(execution.price)}</td>
      <td className="px-4 py-3 text-zinc-300">{execution.notional_usd != null ? `$${execution.notional_usd.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "—"}</td>
      <td className="px-4 py-3">
        <span
          className={
            execution.status === "filled"
              ? "text-emerald-400"
              : execution.status === "failed"
                ? "text-red-400"
                : "text-zinc-400"
          }
        >
          {execution.status}
        </span>
      </td>
      <td className="px-4 py-3 text-zinc-400">{execution.broker}</td>
      <td className="px-4 py-3 text-zinc-400">{time}</td>
    </tr>
  );
}
