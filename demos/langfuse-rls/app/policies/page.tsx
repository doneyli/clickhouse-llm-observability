import { DEMO_POLICIES } from "@/lib/policies";
import { PERSONAS } from "@/lib/personas";
import { evaluate } from "@/lib/rls-policy";
import type { LangfuseTrace } from "@/lib/types";

const CLASSIFICATIONS = ["ceo-only", "restricted", "general"] as const;
const TEAMS = ["executive", "compliance", "analyst"] as const;

function classificationColor(c: string): { bg: string; text: string; border: string } {
  if (c === "ceo-only")   return { bg: "var(--rls-ceo-bg)",        text: "var(--rls-ceo)",        border: "var(--rls-ceo)" };
  if (c === "restricted") return { bg: "var(--rls-restricted-bg)", text: "var(--rls-restricted)", border: "var(--rls-restricted)" };
  return                         { bg: "var(--rls-general-bg)",    text: "var(--rls-general)",    border: "var(--rls-general)" };
}

function Badge({ label, c }: { label: string; c: string }) {
  const col = classificationColor(c);
  return (
    <span
      style={{
        background: col.bg,
        color: col.text,
        border: `1px solid ${col.border}`,
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        padding: "1px 7px",
        letterSpacing: "0.03em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}

export default function PoliciesPage() {
  // Build the 3x3 access matrix: for each persona x each classification, what does the policy engine say?
  const matrix = PERSONAS.map((persona) =>
    CLASSIFICATIONS.map((cls) => {
      const syntheticTrace: LangfuseTrace = {
        id: `synthetic-${cls}`,
        name: `Synthetic trace`,
        metadata: { classification: cls, team: "executive", topic: "" },
      };
      const result = evaluate(persona, syntheticTrace);
      return result;
    })
  );

  return (
    <div className="max-w-3xl mx-auto space-y-10">

      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold tracking-tight" style={{ color: "var(--rls-text)" }}>
          Policy Reference
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--rls-muted)" }}>
          Policies are evaluated in order. First match wins. Deny beats allow on ties. All unmatched traces hit the default deny.
        </p>
      </div>

      {/* Policy cards */}
      <div className="space-y-3">
        <h2 className="text-xs font-semibold uppercase tracking-widest" style={{ color: "var(--rls-muted)" }}>
          Active Policies — evaluated in order
        </h2>
        {DEMO_POLICIES.map((policy, i) => (
          <div
            key={policy.id}
            style={{
              background: "var(--rls-surface)",
              border: `1px solid ${policy.effect === "deny" ? "var(--rls-denied)" : "var(--rls-border)"}`,
              borderRadius: 8,
              padding: "14px 16px",
              display: "flex",
              gap: 14,
              alignItems: "flex-start",
            }}
          >
            {/* Order number */}
            <span
              style={{
                minWidth: 26,
                height: 26,
                borderRadius: "50%",
                background: "var(--rls-surface-2)",
                border: "1px solid var(--rls-border)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 700,
                color: "var(--rls-muted)",
                flexShrink: 0,
                marginTop: 1,
              }}
            >
              {i + 1}
            </span>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <code
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    fontFamily: "'JetBrains Mono', monospace",
                    color: "var(--rls-text)",
                  }}
                >
                  {policy.id}
                </code>
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    letterSpacing: "0.05em",
                    textTransform: "uppercase",
                    padding: "1px 8px",
                    borderRadius: 4,
                    background: policy.effect === "allow" ? "var(--rls-general-bg)" : "var(--rls-denied-bg)",
                    color: policy.effect === "allow" ? "var(--rls-ok)" : "var(--rls-denied)",
                    border: `1px solid ${policy.effect === "allow" ? "var(--rls-ok)" : "var(--rls-denied)"}`,
                  }}
                >
                  {policy.effect}
                </span>
              </div>
              <p className="text-sm mt-1" style={{ color: "var(--rls-muted)" }}>
                {policy.reason}
              </p>

              {/* Inline condition description */}
              <div
                style={{
                  marginTop: 8,
                  padding: "6px 10px",
                  background: "var(--rls-surface-2)",
                  borderRadius: 5,
                  fontSize: 12,
                  fontFamily: "'JetBrains Mono', monospace",
                  color: "var(--rls-text)",
                }}
              >
                {policy.id === "allow-clearance-ge-classification" && (
                  <span>clearance_rank(subject.clearance) <strong>&gt;=</strong> clearance_rank(trace.classification)<br />
                    <span style={{ color: "var(--rls-muted)" }}>
                      &nbsp;&nbsp;rank: ceo-only = 2 &gt; restricted = 1 &gt; general = 0
                    </span>
                  </span>
                )}
                {policy.id === "deny-default" && (
                  <span style={{ color: "var(--rls-denied)" }}>catch-all — no prior rule matched → deny</span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Clearance rank table */}
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-widest mb-3" style={{ color: "var(--rls-muted)" }}>
          Clearance Rank Order
        </h2>
        <div
          style={{
            background: "var(--rls-surface)",
            border: "1px solid var(--rls-border)",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--rls-surface-2)" }}>
                <th style={{ padding: "8px 14px", textAlign: "left", borderBottom: "1px solid var(--rls-border)", fontWeight: 600, color: "var(--rls-muted)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>Rank</th>
                <th style={{ padding: "8px 14px", textAlign: "left", borderBottom: "1px solid var(--rls-border)", fontWeight: 600, color: "var(--rls-muted)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>Level</th>
                <th style={{ padding: "8px 14px", textAlign: "left", borderBottom: "1px solid var(--rls-border)", fontWeight: 600, color: "var(--rls-muted)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>Can see</th>
              </tr>
            </thead>
            <tbody>
              {([["2", "ceo-only", "All classifications"], ["1", "restricted", "restricted + general"], ["0", "general", "general only"]] as const).map(([rank, level, sees], idx) => (
                <tr key={level} style={{ background: idx % 2 === 1 ? "var(--rls-surface-2)" : undefined }}>
                  <td style={{ padding: "9px 14px", borderBottom: "1px solid var(--rls-border)", fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, color: "var(--rls-muted)" }}>{rank}</td>
                  <td style={{ padding: "9px 14px", borderBottom: "1px solid var(--rls-border)" }}>
                    <Badge label={level} c={level} />
                  </td>
                  <td style={{ padding: "9px 14px", borderBottom: "1px solid var(--rls-border)", color: "var(--rls-muted)", fontSize: 12 }}>{sees} (via clearance rule)</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Access matrix */}
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-widest mb-3" style={{ color: "var(--rls-muted)" }}>
          Access Matrix — persona × classification
        </h2>
        <p className="text-sm mb-3" style={{ color: "var(--rls-muted)" }}>
          Computed live from the policy engine. Switch personas in the Demo tab to see this in action.
        </p>
        <div
          style={{
            background: "var(--rls-surface)",
            border: "1px solid var(--rls-border)",
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--rls-surface-2)" }}>
                <th style={{ padding: "8px 14px", textAlign: "left", borderBottom: "1px solid var(--rls-border)", fontWeight: 600, color: "var(--rls-muted)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>Persona</th>
                {CLASSIFICATIONS.map((cls) => (
                  <th key={cls} style={{ padding: "8px 14px", textAlign: "center", borderBottom: "1px solid var(--rls-border)", fontWeight: 600, fontSize: 11 }}>
                    <Badge label={cls} c={cls} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PERSONAS.map((persona, pi) => (
                <tr key={persona.id} style={{ background: pi % 2 === 1 ? "var(--rls-surface-2)" : undefined }}>
                  <td style={{ padding: "10px 14px", borderBottom: "1px solid var(--rls-border)" }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{persona.name}</div>
                    <div style={{ fontSize: 11, color: "var(--rls-muted)", marginTop: 2 }}>
                      {persona.team} · <Badge label={persona.clearance} c={persona.clearance} />
                    </div>
                  </td>
                  {matrix[pi].map((result, ci) => (
                    <td key={ci} style={{ padding: "10px 14px", borderBottom: "1px solid var(--rls-border)", textAlign: "center", verticalAlign: "middle" }}>
                      <div
                        style={{
                          display: "inline-flex",
                          flexDirection: "column",
                          alignItems: "center",
                          gap: 3,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 700,
                            padding: "2px 8px",
                            borderRadius: 4,
                            background: result.allow ? "var(--rls-general-bg)" : "var(--rls-denied-bg)",
                            color: result.allow ? "var(--rls-ok)" : "var(--rls-denied)",
                          }}
                        >
                          {result.allow ? "allow" : "deny"}
                        </span>
                        <span style={{ fontSize: 10, color: "var(--rls-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
                          {result.matchedRule}
                        </span>
                      </div>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs mt-2" style={{ color: "var(--rls-muted)" }}>
          Note: access is decided purely by clearance rank. A subject sees a trace only if their clearance is &gt;= the trace classification. Team is metadata for display and audit only — it never grants access and cannot override the classification gate.
        </p>
      </div>

    </div>
  );
}
