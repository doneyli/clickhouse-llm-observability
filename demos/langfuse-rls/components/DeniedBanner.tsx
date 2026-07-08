"use client";

import { useState } from "react";
import type { TracesApiResponse } from "@/lib/types";

interface DeniedBannerProps {
  denied: TracesApiResponse["denied"];
  onShowDetails: () => void;
}

export default function DeniedBanner({ denied, onShowDetails }: DeniedBannerProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed || denied.count === 0) return null;

  return (
    <div
      className="mb-4 flex items-center justify-between rounded-xl border px-4 py-3 text-sm anim-rise"
      style={{
        background: "var(--rls-denied-bg)",
        borderColor: "#FECACA",
        color: "var(--rls-denied)",
      }}
    >
      <p className="font-medium">
        <span className="font-bold">{denied.count}</span>{" "}
        {denied.count === 1 ? "trace" : "traces"} hidden by RLS policy.{" "}
        <button
          onClick={onShowDetails}
          className="underline underline-offset-2 transition-opacity hover:opacity-70 focus:outline-none focus-visible:ring-2"
          style={{ color: "var(--rls-denied)" }}
        >
          See which rules fired &rarr;
        </button>
      </p>
      <button
        onClick={() => setDismissed(true)}
        className="ml-4 shrink-0 rounded p-1 transition-colors hover:bg-red-100 focus:outline-none focus-visible:ring-2"
        aria-label="Dismiss"
        style={{ color: "var(--rls-denied)" }}
      >
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <path d="M1 1l12 12M13 1L1 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}
