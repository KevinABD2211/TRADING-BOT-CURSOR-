import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4">
      <h1 className="text-2xl font-bold text-white">404 — Page not found</h1>
      <p className="text-zinc-400">
        The page you’re looking for doesn’t exist or hasn’t been deployed yet.
      </p>
      <nav className="mt-4 flex flex-wrap justify-center gap-4 text-sm">
        <Link href="/" className="text-blue-400 hover:underline">Dashboard</Link>
        <Link href="/signals" className="text-blue-400 hover:underline">Signals</Link>
        <Link href="/advice" className="text-blue-400 hover:underline">Advice</Link>
        <Link href="/trades" className="text-blue-400 hover:underline">Trades</Link>
        <Link href="/executions" className="text-blue-400 hover:underline">Executions</Link>
        <Link href="/sources" className="text-blue-400 hover:underline">Sources</Link>
      </nav>
      <p className="mt-6 text-xs text-zinc-500">
        Set NEXT_PUBLIC_API_URL to your backend URL when deploying so /api/* is proxied and data loads.
      </p>
    </div>
  );
}
