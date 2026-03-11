import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "Trading Assistant",
  description: "Dashboard for trading signals from Discord",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen font-sans antialiased">
        <header className="border-b border-[var(--border)] bg-[var(--card)]">
          <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
            <a href="/" className="text-lg font-semibold text-white">
              Trading Assistant
            </a>
            <nav className="flex gap-6">
              <a href="/" className="text-sm text-zinc-400 hover:text-white">
                Dashboard
              </a>
              <a href="/signals" className="text-sm text-zinc-400 hover:text-white">
                Signals
              </a>
              <a href="/signals/market" className="text-sm text-zinc-400 hover:text-white">
                Market (Live)
              </a>
              <a href="/sources" className="text-sm text-zinc-400 hover:text-white">
                Sources
              </a>
              <a href="/trades" className="text-sm text-zinc-400 hover:text-white">
                Trades (Alpaca)
              </a>
              <a href="/advice" className="text-sm text-zinc-400 hover:text-white">
                Advice
              </a>
              <a href="/executions" className="text-sm text-zinc-400 hover:text-white">
                Executions
              </a>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
