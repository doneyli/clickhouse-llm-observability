"use client";

import { useEffect, useState, useCallback } from "react";
import PersonaSwitcher from "@/components/PersonaSwitcher";
import type { Subject, TracesApiResponse, LangfuseTrace, EvaluationResult, Classification, TeamName } from "@/lib/types";

// --- Classification and team badge helpers ---

const CLASSIFICATION_STYLE: Record<Classification, { bg: string; text: string; label: string }> = {
  "ceo-only":   { bg: "var(--rls-ceo-bg)",       text: "var(--rls-ceo)",        label: "CEO Only"   },
  "restricted": { bg: "var(--rls-restricted-bg)", text: "var(--rls-restricted)", label: "Restricted" },
  "general":    { bg: "var(--rls-general-bg)",    text: "var(--rls-general)",    label: "General"    },
};

const TEAM_STYLE: Record<TeamName, { bg: string; text: string }> = {
  executive:  { bg: "#EDE9FE", text: "#7C3AED" },
  compliance: { bg: "#FFF7ED", text: "#EA580C" },
  analyst:    { bg: "#F0F9FF", text: "#0284C7" },
};

function ClassificationBadge({ classification }: { classification: Classification }) {
  const style = CLASSIFICATION_STYLE[classification];
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-semibold"
      style={{ background: style.bg, color: style.text }}
    >
      {style.label}
    </span>
  );
}

function TeamBadge({ team }: { team: TeamName }) {
  const style = TEAM_STYLE[team];
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-medium capitalize"
      style={{ background: style.bg, color: style.text }}
    >
      {team}
    </span>
  );
}

// --- TraceCard ---

// Pull readable text out of a Langfuse input/output payload, which may be a
// raw string, a {query}/{response}-style object, or arbitrary JSON.
function extractText(value: unknown, preferredKeys: string[]): string | null {
  if (value == null) return null;
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    for (const key of preferredKeys) {
      if (typeof obj[key] === "string") return obj[key] as string;
    }
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

const INPUT_KEYS = ["query", "prompt", "question", "input", "text", "messages"];
const OUTPUT_KEYS = ["response", "answer", "completion", "output", "text"];

function ContentBlock({ label, body }: { label: string; body: string }) {
  return (
    <div>
      <p
        className="mb-1 text-[10px] font-semibold uppercase tracking-wide"
        style={{ color: "var(--rls-muted)" }}
      >
        {label}
      </p>
      <p
        className="whitespace-pre-wrap break-words rounded-lg px-3 py-2 font-mono text-xs leading-relaxed"
        style={{ background: "var(--rls-surface-2)", color: "var(--rls-text)" }}
      >
        {body}
      </p>
    </div>
  );
}

function TraceCard({ trace }: { trace: LangfuseTrace & { _rls: EvaluationResult } }) {
  const [open, setOpen] = useState(false);
  const meta = trace.metadata;
  const ts = trace.timestamp ?? trace.createdAt;
  const displayTs = ts
    ? new Date(ts).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : null;

  const prompt = extractText(trace.input, INPUT_KEYS);
  const response = extractText(trace.output, OUTPUT_KEYS);
  const hasContent = Boolean(prompt || response);

  return (
    <article
      className="anim-rise rounded-xl border p-4 transition-shadow hover:shadow-sm"
      style={{ background: "var(--rls-surface)", borderColor: "var(--rls-border)" }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold" style={{ color: "var(--rls-text)" }}>
            {trace.name}
          </p>
          <p className="mt-0.5 truncate text-xs font-mono" style={{ color: "var(--rls-muted)" }}>
            {trace.id}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-1.5">
          {meta?.classification && <ClassificationBadge classification={meta.classification} />}
          {meta?.team && <TeamBadge team={meta.team} />}
        </div>
      </div>

      {meta?.topic && (
        <p className="mt-2 text-xs" style={{ color: "var(--rls-muted)" }}>
          {meta.topic}
        </p>
      )}

      {hasContent && (
        <div className="mt-3 border-t pt-3" style={{ borderColor: "var(--rls-border)" }}>
          {open ? (
            <div className="flex flex-col gap-2">
              {prompt && <ContentBlock label="Prompt" body={prompt} />}
              {response && <ContentBlock label="Response" body={response} />}
            </div>
          ) : (
            prompt && (
              <p
                className="line-clamp-2 text-xs leading-relaxed"
                style={{ color: "var(--rls-muted)" }}
              >
                <span className="font-medium" style={{ color: "var(--rls-text)" }}>
                  Prompt:{" "}
                </span>
                {prompt}
              </p>
            )
          )}
          <button
            onClick={() => setOpen((o) => !o)}
            className="mt-2 text-xs font-medium transition-colors hover:underline"
            style={{ color: "var(--rls-ok)" }}
          >
            {open ? "Hide content" : "Show content"}
          </button>
        </div>
      )}

      <div className="mt-3 flex items-center gap-3 text-xs" style={{ color: "var(--rls-muted)" }}>
        {trace.userId && <span>user: {trace.userId}</span>}
        {displayTs && <span>{displayTs}</span>}
        <span
          className="ml-auto rounded-full px-2 py-0.5 font-medium"
          style={{ background: "var(--rls-surface-2)", color: "var(--rls-ok)" }}
        >
          {trace._rls.matchedRule}
        </span>
      </div>
    </article>
  );
}

// --- TraceList ---

function TraceList({ traces }: { traces: Array<LangfuseTrace & { _rls: EvaluationResult }> }) {
  if (traces.length === 0) {
    return (
      <div
        className="rounded-xl border border-dashed px-6 py-12 text-center"
        style={{ borderColor: "var(--rls-border)" }}
      >
        <p className="text-sm font-medium" style={{ color: "var(--rls-muted)" }}>
          No traces visible for this persona.
        </p>
        <p className="mt-1 text-xs" style={{ color: "var(--rls-muted)" }}>
          All traces were denied by policy.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {traces.map((t) => (
        <TraceCard key={t.id} trace={t} />
      ))}
    </div>
  );
}

// --- DeniedBanner ---

interface DeniedBannerProps {
  count: number;
  samples: TracesApiResponse["denied"]["samples"];
}

function DeniedBanner({ count, samples }: DeniedBannerProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className="rounded-xl border px-4 py-3"
      style={{ background: "var(--rls-denied-bg)", borderColor: "var(--rls-denied)" }}
    >
      <button
        className="flex w-full items-center justify-between gap-2 text-left"
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="text-sm font-semibold" style={{ color: "var(--rls-denied)" }}>
          {count} trace{count !== 1 ? "s" : ""} hidden by RLS policy
        </span>
        <span className="text-xs font-medium" style={{ color: "var(--rls-denied)" }}>
          {expanded ? "Hide samples" : "Show samples"}
        </span>
      </button>

      {expanded && samples.length > 0 && (
        <ul className="mt-3 flex flex-col gap-2">
          {samples.map((s) => (
            <li
              key={s.traceId}
              className="rounded-lg border px-3 py-2"
              style={{ background: "var(--rls-surface)", borderColor: "var(--rls-border)" }}
            >
              <p className="text-xs font-semibold" style={{ color: "var(--rls-text)" }}>
                {s.name}
              </p>
              <p className="mt-0.5 text-xs" style={{ color: "var(--rls-muted)" }}>
                Rule: <span className="font-mono">{s.matchedRule}</span> — {s.reason}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --- Page ---

export default function DemoPage() {
  const [personas, setPersonas] = useState<Subject[]>([]);
  const [selected, setSelected] = useState<Subject | null>(null);
  const [data, setData] = useState<TracesApiResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [initError, setInitError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/personas")
      .then((r) => r.json())
      .then((list: Subject[]) => {
        setPersonas(list);
        setSelected(list[0] ?? null);
      })
      .catch(() => setInitError("Failed to load personas."));
  }, []);

  const loadTraces = useCallback((persona: Subject) => {
    setLoading(true);
    fetch(`/api/traces?persona=${persona.id}`)
      .then((r) => r.json())
      .then((d: TracesApiResponse) => setData(d))
      .catch(() =>
        setData({
          visible: [],
          denied: { count: 0, samples: [] },
          persona,
          error: "Could not reach /api/traces.",
        }),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (selected) loadTraces(selected);
  }, [selected, loadTraces]);

  function handlePersonaChange(p: Subject) {
    setSelected(p);
  }

  if (initError) {
    return (
      <div className="rounded-xl border px-6 py-8 text-center" style={{ borderColor: "var(--rls-border)" }}>
        <p className="text-sm" style={{ color: "var(--rls-err)" }}>
          {initError}
        </p>
      </div>
    );
  }

  if (!selected || personas.length === 0) {
    return (
      <div className="flex justify-center py-16">
        <div className="text-sm" style={{ color: "var(--rls-muted)" }}>
          Loading personas…
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-bold tracking-tight" style={{ color: "var(--rls-text)" }}>
          Persona Switcher
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--rls-muted)" }}>
          Select a persona to see which Langfuse traces the RLS policy engine allows.
        </p>
      </div>

      <PersonaSwitcher personas={personas} selected={selected} onChange={handlePersonaChange} />

      {data?.error && (
        <div
          className="rounded-xl border px-4 py-3"
          style={{ background: "var(--rls-denied-bg)", borderColor: "var(--rls-denied)" }}
        >
          <p className="text-sm font-medium" style={{ color: "var(--rls-denied)" }}>
            {data.error}
          </p>
          <p className="mt-0.5 text-xs" style={{ color: "var(--rls-muted)" }}>
            Ensure Langfuse is running at localhost:3001 and your .env keys are set.
          </p>
        </div>
      )}

      {!data?.error && data && (
        <div
          className="flex items-center gap-4 rounded-xl border px-4 py-3"
          style={{ background: "var(--rls-surface)", borderColor: "var(--rls-border)" }}
        >
          <div className="text-center">
            <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--rls-ok)" }}>
              {data.visible.length}
            </p>
            <p className="text-xs" style={{ color: "var(--rls-muted)" }}>
              visible
            </p>
          </div>
          <div
            className="h-8 w-px"
            style={{ background: "var(--rls-border)" }}
          />
          <div className="text-center">
            <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--rls-denied)" }}>
              {data.denied.count}
            </p>
            <p className="text-xs" style={{ color: "var(--rls-muted)" }}>
              denied
            </p>
          </div>
          <div
            className="h-8 w-px"
            style={{ background: "var(--rls-border)" }}
          />
          <div className="text-center">
            <p className="text-2xl font-bold tabular-nums" style={{ color: "var(--rls-text)" }}>
              {data.visible.length + data.denied.count}
            </p>
            <p className="text-xs" style={{ color: "var(--rls-muted)" }}>
              total
            </p>
          </div>
        </div>
      )}

      {data?.denied.count && data.denied.count > 0 ? (
        <DeniedBanner count={data.denied.count} samples={data.denied.samples} />
      ) : null}

      {loading ? (
        <div className="flex justify-center py-12">
          <div className="text-sm" style={{ color: "var(--rls-muted)" }}>
            Loading traces…
          </div>
        </div>
      ) : (
        <TraceList traces={data?.visible ?? []} />
      )}
    </div>
  );
}
