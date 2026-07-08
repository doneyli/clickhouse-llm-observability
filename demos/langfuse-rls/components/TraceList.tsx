import type { LangfuseTrace, EvaluationResult } from "@/lib/types";
import TraceCard from "./TraceCard";

interface TraceListProps {
  traces: Array<LangfuseTrace & { _rls: EvaluationResult }>;
  personaName: string;
}

export default function TraceList({ traces, personaName }: TraceListProps) {
  if (traces.length === 0) {
    return (
      <div
        className="rounded-xl border p-10 text-center"
        style={{ background: "var(--rls-surface)", borderColor: "var(--rls-border)" }}
      >
        <p className="text-sm" style={{ color: "var(--rls-muted)" }}>
          No traces visible to {personaName} under current policy.
        </p>
      </div>
    );
  }

  return (
    <div>
      <p className="mb-3 text-xs font-medium" style={{ color: "var(--rls-muted)" }}>
        Showing{" "}
        <span style={{ color: "var(--rls-text)" }} className="font-semibold">
          {traces.length}
        </span>{" "}
        {traces.length === 1 ? "trace" : "traces"}
      </p>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {traces.map((trace) => (
          <TraceCard key={trace.id} trace={trace} personaName={personaName} />
        ))}
      </div>
    </div>
  );
}
