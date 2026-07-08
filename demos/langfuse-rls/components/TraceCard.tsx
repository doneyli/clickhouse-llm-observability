import type { LangfuseTrace, EvaluationResult, Classification, TeamName } from "@/lib/types";

interface TraceCardProps {
  trace: LangfuseTrace & { _rls: EvaluationResult };
  personaName: string;
}

const CLASSIFICATION_STYLE: Record<Classification, { bg: string; text: string; label: string }> = {
  "ceo-only":   { bg: "var(--rls-ceo-bg)",        text: "var(--rls-ceo)",        label: "CEO Only"   },
  "restricted": { bg: "var(--rls-restricted-bg)",  text: "var(--rls-restricted)", label: "Restricted" },
  "general":    { bg: "var(--rls-general-bg)",      text: "var(--rls-general)",    label: "General"    },
};

const TEAM_STYLE: Record<TeamName, { bg: string; text: string }> = {
  executive:  { bg: "#EDE9FE", text: "#7C3AED" },
  compliance: { bg: "#FFF7ED", text: "#EA580C" },
  analyst:    { bg: "#F0F9FF", text: "#0284C7" },
};

function inputPreview(input: unknown): string {
  if (!input) return "";
  const raw =
    typeof input === "string"
      ? input
      : typeof (input as Record<string, unknown>).query === "string"
      ? ((input as Record<string, unknown>).query as string)
      : JSON.stringify(input);
  return raw.length > 120 ? raw.slice(0, 117) + "..." : raw;
}

export default function TraceCard({ trace, personaName: _personaName }: TraceCardProps) {
  const { _rls } = trace;
  const meta = trace.metadata;
  const classification = meta?.classification;
  const team = meta?.team;
  const topic = meta?.topic;
  const classStyle = classification ? CLASSIFICATION_STYLE[classification] : null;
  const teamStyle = team ? TEAM_STYLE[team] : null;
  const preview = inputPreview(trace.input);

  return (
    <div
      className="rounded-xl border p-4 transition-shadow duration-150 hover:shadow-md"
      style={{
        background: "var(--rls-surface)",
        borderColor: _rls.allow ? "var(--rls-border)" : "var(--rls-denied-bg)",
        opacity: _rls.allow ? 1 : 0.6,
      }}
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <h3
          className="text-sm font-semibold leading-snug"
          style={{ color: "var(--rls-text)" }}
        >
          {trace.name}
        </h3>
        <span
          className="shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold"
          style={
            _rls.allow
              ? { background: "#D1FAE5", color: "var(--rls-ok)" }
              : { background: "var(--rls-denied-bg)", color: "var(--rls-denied)" }
          }
        >
          {_rls.allow ? "Allowed" : "Denied"}
        </span>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {classStyle && (
          <span
            className="rounded-full px-2 py-0.5 text-xs font-medium"
            style={{ background: classStyle.bg, color: classStyle.text }}
          >
            {classStyle.label}
          </span>
        )}
        {teamStyle && team && (
          <span
            className="rounded-full px-2 py-0.5 text-xs font-medium capitalize"
            style={{ background: teamStyle.bg, color: teamStyle.text }}
          >
            {team}
          </span>
        )}
      </div>

      {topic && (
        <p className="mb-2 text-xs" style={{ color: "var(--rls-muted)" }}>
          <span className="font-medium" style={{ color: "var(--rls-text)" }}>Topic:</span>{" "}
          {topic}
        </p>
      )}

      {trace.userId && (
        <p className="mb-2 text-xs" style={{ color: "var(--rls-muted)" }}>
          <span className="font-medium" style={{ color: "var(--rls-text)" }}>User:</span>{" "}
          {trace.userId}
        </p>
      )}

      {preview && (
        <p
          className="mb-3 rounded-lg px-3 py-2 font-mono text-xs leading-relaxed"
          style={{ background: "var(--rls-surface-2)", color: "var(--rls-muted)" }}
        >
          {preview}
        </p>
      )}

      {_rls.allow && (
        <p className="text-xs" style={{ color: "var(--rls-muted)" }}>
          via{" "}
          <span
            className="rounded px-1 py-0.5 font-mono"
            style={{ background: "var(--rls-surface-2)", color: "var(--rls-text)" }}
          >
            {_rls.matchedRule}
          </span>
        </p>
      )}
    </div>
  );
}
