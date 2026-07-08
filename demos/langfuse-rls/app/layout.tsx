import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Langfuse RLS Demo",
  description: "Row-Level Security demo — simulated attribute-based access control on Langfuse traces.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>
        <header
          style={{ background: "var(--rls-surface)", borderBottom: "1px solid var(--rls-border)" }}
          className="sticky top-0 z-50"
        >
          <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between gap-6">
            <div className="flex items-center gap-3 min-w-0">
              <span className="font-semibold text-sm tracking-tight" style={{ color: "var(--rls-accent)" }}>
                Langfuse RLS
              </span>
              <span
                className="text-xs hidden sm:block truncate"
                style={{ color: "var(--rls-muted)" }}
              >
                Row-Level Security demo (simulated)
              </span>
            </div>

            <nav className="flex items-center gap-1">
              <Link
                href="/"
                className="px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
                style={{ color: "var(--rls-text)" }}
              >
                Demo
              </Link>
              <Link
                href="/policies"
                className="px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
                style={{ color: "var(--rls-text)" }}
              >
                Policies
              </Link>
              <Link
                href="/design"
                className="px-3 py-1.5 rounded-md text-sm font-medium transition-colors"
                style={{ color: "var(--rls-text)" }}
              >
                Design
              </Link>
            </nav>

            <div className="flex items-center gap-2 shrink-0">
              <span
                className="pulse-dot w-2 h-2 rounded-full"
                style={{ background: "var(--rls-ok)" }}
              />
              <span className="text-xs font-mono" style={{ color: "var(--rls-muted)" }}>
                localhost:3001
              </span>
            </div>
          </div>
        </header>

        <main className="max-w-5xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
