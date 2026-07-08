"use client";

import { useEffect, useRef } from "react";
import type { TracesApiResponse } from "@/lib/types";

interface PolicyExplainerProps {
  samples: TracesApiResponse["denied"]["samples"];
  onClose: () => void;
}

export default function PolicyExplainer({ samples, onClose }: PolicyExplainerProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.45)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="policy-explainer-title"
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="relative w-full max-w-lg rounded-2xl p-6 shadow-2xl focus:outline-none anim-rise"
        style={{ background: "var(--rls-surface)", border: "1px solid var(--rls-border)" }}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2
              id="policy-explainer-title"
              className="text-base font-bold"
              style={{ color: "var(--rls-text)" }}
            >
              Policy Decisions
            </h2>
            <p className="mt-0.5 text-xs" style={{ color: "var(--rls-muted)" }}>
              Rules that fired to deny access
            </p>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 rounded-lg p-1.5 transition-colors hover:bg-gray-100 focus:outline-none focus-visible:ring-2"
            aria-label="Close"
            style={{ color: "var(--rls-muted)" }}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M2 2l12 12M14 2L2 14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        {samples.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--rls-muted)" }}>
            No denied traces to display.
          </p>
        ) : (
          <ul className="space-y-3 max-h-96 overflow-y-auto pr-1">
            {samples.map((s) => (
              <li
                key={s.traceId}
                className="rounded-xl border p-3"
                style={{
                  background: "var(--rls-denied-bg)",
                  borderColor: "#FECACA",
                }}
              >
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <p
                    className="text-sm font-semibold leading-snug"
                    style={{ color: "var(--rls-text)" }}
                  >
                    {s.name}
                  </p>
                  <span
                    className="shrink-0 rounded px-1.5 py-0.5 font-mono text-xs"
                    style={{ background: "#FECACA", color: "var(--rls-denied)" }}
                  >
                    {s.matchedRule}
                  </span>
                </div>
                <p className="text-xs" style={{ color: "var(--rls-muted)" }}>
                  {s.reason}
                </p>
              </li>
            ))}
          </ul>
        )}

        <div
          className="mt-4 border-t pt-4"
          style={{ borderColor: "var(--rls-border)" }}
        >
          <p className="text-xs" style={{ color: "var(--rls-muted)" }}>
            Policy is evaluated client-side in this demo. In a native Langfuse RLS implementation, filtering would occur server-side before data leaves the database.
          </p>
        </div>

        <button
          onClick={onClose}
          className="mt-4 w-full rounded-lg px-4 py-2 text-sm font-medium transition-opacity hover:opacity-80 focus:outline-none focus-visible:ring-2"
          style={{ background: "var(--rls-accent)", color: "#fff" }}
        >
          Close
        </button>
      </div>
    </div>
  );
}
