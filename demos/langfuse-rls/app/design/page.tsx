"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function DesignPage() {
  useEffect(() => {
    import("mermaid").then((m) => {
      m.default.initialize({
        startOnLoad: false,
        theme: "default",
        sequence: {
          actorMargin: 60,
          messageMargin: 40,
          mirrorActors: false,
          useMaxWidth: true,
        },
      });
      m.default.run({ querySelector: ".mermaid" });
    });
  }, []);

  return (
    <>
      {/* TOP BANNER - sticky, cannot be missed */}
      <div
        className="sticky top-14 z-40 w-full"
        style={{ background: "#FFFBEB", borderBottom: "1px solid #FCD34D" }}
      >
        <div className="max-w-5xl mx-auto px-6 py-3 flex items-start gap-3">
          <span className="text-lg shrink-0" aria-hidden="true">⚠️</span>
          <p className="text-sm font-medium" style={{ color: "#92400E", lineHeight: "1.5" }}>
            <strong>DEMO ONLY</strong> — Langfuse does not natively support RLS as of 2026-05-19.
            This page documents a proposed future feature. The demo simulates the behavior in the
            app layer using trace metadata + a client-side policy engine.
          </p>
        </div>
      </div>

      {/* Main content */}
      <div className="max-w-5xl mx-auto px-6 py-10">
        <div className="prose-rls mx-auto">

          {/* Back link */}
          <div className="mb-8">
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 text-sm font-medium no-underline transition-opacity hover:opacity-70"
              style={{ color: "var(--rls-accent)" }}
            >
              <span>←</span>
              <span>Back to Demo</span>
            </Link>
          </div>

          {/* Page header */}
          <div className="mb-10">
            <div
              className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold mb-4"
              style={{
                background: "var(--rls-accent-light)",
                color: "var(--rls-accent)",
              }}
            >
              DESIGN DOCUMENT
            </div>
            <h1>Row-Level Security for Langfuse</h1>
            <p
              className="mt-3 text-base"
              style={{ color: "var(--rls-muted)" }}
            >
              A proposed attribute-based access control model for Langfuse traces.
              This document captures the design rationale, proposed data model, evaluation
              logic, and open questions for Langfuse engineering consideration.
            </p>
            <div
              className="mt-4 flex flex-wrap gap-4 text-xs font-mono"
              style={{ color: "var(--rls-muted)" }}
            >
              <span>Author: Doneyli De Jesus, ClickHouse SA</span>
              <span>Date: 2026-05-19</span>
              <span>Status: Proposal / Discussion</span>
            </div>
          </div>

          {/* ------------------------------------------------------------------ */}
          {/* Section 1: Why RLS                                                  */}
          {/* ------------------------------------------------------------------ */}
          <h2>1. Why Row-Level Security</h2>

          <p>
            Enterprise prospects are hitting a hard wall with Langfuse&apos;s current access model.
            A global bank flagged data residency and access control as deal blockers.
            Other large financial institutions have raised similar concerns. The pattern is consistent: large
            financial institutions and regulated enterprises need to share a single Langfuse project
            across multiple teams with different data clearance levels — and the current model
            does not support that.
          </p>

          <h3>How This Conversation Started</h3>

          <p>
            On 2026-05-13, Doneyli (ClickHouse Solutions Architect) asked Langfuse engineering:
          </p>

          <blockquote>
            &ldquo;Does Langfuse support client-side Field Level Encryption? Or attribute-based
            access control on traces?&rdquo;
          </blockquote>

          <p>
            Langfuse engineering responded directly:
          </p>

          <blockquote>
            &ldquo;FLE is not on the roadmap. Long-term answer is attribute-based RBAC /
            Row-Level Security: user has rights to see data if condition is met on the
            observation/trace.&rdquo;
          </blockquote>

          <p>
            That response is the seed of this design document. Langfuse described the right model —
            this document fleshes it out into a concrete spec and invites engineering discussion
            on implementation details.
          </p>

          <h3>The Banking Use Case</h3>

          <p>
            Consider the bank&apos;s situation: a single AI observability platform shared across three
            teams — executive strategy (uses AI for board-level analysis), compliance
            (uses AI for regulatory review), and general analysts (uses AI for routine work).
            Each team&apos;s traces may contain data the other teams should not see. Today, the
            only solution is separate Langfuse projects — which breaks cross-project analytics
            and multiplies operational overhead.
          </p>

          {/* ------------------------------------------------------------------ */}
          {/* Section 2: Current Langfuse RBAC                                    */}
          {/* ------------------------------------------------------------------ */}
          <h2>2. Current Langfuse RBAC</h2>

          <p>
            Langfuse today supports four roles at both the organization and project level:{" "}
            <strong>Owner</strong>, <strong>Admin</strong>, <strong>Member</strong>, and{" "}
            <strong>Viewer</strong>. See the{" "}
            <a
              href="https://langfuse.com/docs/administration/rbac"
              target="_blank"
              rel="noopener noreferrer"
            >
              Langfuse RBAC documentation
            </a>{" "}
            for full capability breakdown.
          </p>

          <p>
            The critical gap: <strong>all members see all traces in a project</strong>. There
            is no row-level filtering. Role differences control what operations a user can
            perform (edit prompts, manage integrations, etc.) — they do not control which
            traces a user can see.
          </p>

          <h3>What&apos;s Missing</h3>

          <table>
            <thead>
              <tr>
                <th>Capability</th>
                <th>Today</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Restrict trace visibility by team</td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>No</span> — all traces visible to all project members
                </td>
              </tr>
              <tr>
                <td>Restrict trace visibility by sensitivity level</td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>No</span> — only project-level isolation
                </td>
              </tr>
              <tr>
                <td>Attribute-based policy rules</td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>No</span> — not yet designed
                </td>
              </tr>
              <tr>
                <td>Role-based operation control</td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>Yes</span> — Owner / Admin / Member / Viewer
                </td>
              </tr>
            </tbody>
          </table>

          <p>
            A compliance officer and an executive analyst cannot share a project today if some
            traces contain board-level material that should not be visible to the compliance team.
            Workaround is separate projects, which creates cross-project analytics gaps.
          </p>

          {/* ------------------------------------------------------------------ */}
          {/* Section 3: Proposed RLS Model                                        */}
          {/* ------------------------------------------------------------------ */}
          <h2>3. Proposed RLS Model</h2>

          <p>
            The model follows attribute-based access control (ABAC): access decisions are made by
            evaluating conditions over attributes of both the <em>subject</em> (the requesting user)
            and the <em>object</em> (the trace being accessed).
          </p>

          <h3>Subject Attributes</h3>
          <ul>
            <li><code>subject.team</code> — which team the user belongs to (sourced from SSO claims or SCIM provisioning)</li>
            <li><code>subject.clearance</code> — the user&apos;s data clearance level: <code>general</code>, <code>restricted</code>, or <code>ceo-only</code></li>
          </ul>

          <h3>Object (Trace) Attributes</h3>
          <ul>
            <li><code>trace.metadata.team</code> — which team produced the trace (set at ingest time via SDK)</li>
            <li><code>trace.metadata.classification</code> — sensitivity tier: <code>general</code>, <code>restricted</code>, or <code>ceo-only</code></li>
          </ul>

          <h3>Policy Structure</h3>

          <p>
            Each policy rule is a tuple of{" "}
            <strong>subject predicate</strong>,{" "}
            <strong>object predicate</strong>,{" "}
            <strong>effect</strong> (<code>allow</code> or <code>deny</code>), and a{" "}
            <strong>reason</strong> string for audit purposes.
            Evaluation order: <strong>explicit deny &gt; explicit allow &gt; default deny</strong>.
          </p>

          <pre>
            <code>{`policies:
  - id: allow-clearance-ge-classification
    effect: allow
    condition: clearance_rank(subject.clearance) >= clearance_rank(trace.metadata.classification)
    # clearance_rank: ceo-only=2, restricted=1, general=0
    reason: Higher clearance sees lower or equal classification

  - id: deny-default
    effect: deny
    condition: true  # implicit catch-all
    reason: No matching allow rule`}</code>
          </pre>

          <p>
            Access is decided by a single gate: a trace is visible only if the
            user&apos;s clearance rank is at least as high as the trace&apos;s classification
            rank. The <code>subject.team</code> / <code>trace.metadata.team</code> fields are
            carried for display and audit, not for authorization — team membership never grants
            access on its own.
          </p>

          <p>
            An earlier draft added an <code>allow-own-team</code> rule, making the two allow
            rules an <em>OR</em> gate (visible if same team <em>or</em> sufficient clearance).
            That was rejected: it let a general-clearance analyst read{" "}
            <code>restricted</code> and <code>ceo-only</code> traces simply because they were
            owned by the analyst team, which defeats classification entirely. A team-scoped
            grant must <em>narrow</em> access (need-to-know on top of clearance), never widen it
            past the classification ceiling. If the bank wants need-to-know compartments, the correct
            shape is <code>clearance &gt;= classification</code> <strong>AND</strong> team
            membership — an intersection, not a union.
          </p>

          {/* ------------------------------------------------------------------ */}
          {/* Section 4: End-to-End Flow                                           */}
          {/* ------------------------------------------------------------------ */}
          <h2>4. End-to-End Flow</h2>

          <p>The sequence below shows how a trace is stored and then filtered at read time.</p>

          {/* Mermaid diagram */}
          <div
            className="my-6 p-4 rounded-lg overflow-x-auto"
            style={{
              background: "var(--rls-surface)",
              border: "1px solid var(--rls-border)",
            }}
          >
            <div className="mermaid">
              {`sequenceDiagram
    participant SDK as Application SDK
    participant LF as Langfuse Server
    participant CH as ClickHouse
    participant PE as Policy Engine
    participant UI as Langfuse UI / API

    SDK->>LF: trace(name, metadata.classification=ceo-only, metadata.team=executive, ...)
    LF->>CH: INSERT INTO traces ...
    Note over CH: Trace stored with all metadata intact

    UI->>LF: GET /api/public/traces (with user identity)
    LF->>CH: SELECT * FROM traces WHERE project_id = ?
    CH-->>LF: [all traces, unfiltered]
    LF->>PE: evaluate(subject=current_user, traces=[...])
    PE-->>LF: visible=[...], denied_count=N
    LF-->>UI: { data: visible_traces, meta: { denied_count: N } }`}
            </div>
          </div>

          <div
            className="mt-2 px-4 py-3 rounded-lg text-sm"
            style={{
              background: "var(--rls-accent-light)",
              border: "1px solid #C4B5FD",
              color: "#4C1D95",
            }}
          >
            <strong>Implementation note:</strong> Today, the Policy Engine lives in our demo
            app layer (steps 5-6 happen in{" "}
            <code style={{ background: "#DDD6FE", color: "#4C1D95" }}>
              app/api/traces/route.ts
            </code>
            ). In the native RLS design, this logic moves server-side into Langfuse, with
            push-down to ClickHouse <code style={{ background: "#DDD6FE", color: "#4C1D95" }}>WHERE</code>{" "}
            clauses for efficiency at scale. The demo demonstrates the behavior correctly;
            only the enforcement point changes.
          </div>

          {/* ------------------------------------------------------------------ */}
          {/* Section 5: Demo Walkthrough                                          */}
          {/* ------------------------------------------------------------------ */}
          <h2>5. Demo Walkthrough</h2>

          <p>
            The demo app includes three personas that map to a realistic banking scenario.
            Each persona has a different team and clearance level, producing different filtered
            views of the same set of traces.
          </p>

          <table>
            <thead>
              <tr>
                <th>Persona</th>
                <th>Team</th>
                <th>Clearance</th>
                <th>ceo-only traces</th>
                <th>restricted traces</th>
                <th>general traces</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>Alice Chen</strong></td>
                <td>executive</td>
                <td>
                  <span
                    className="px-2 py-0.5 rounded text-xs font-semibold"
                    style={{ background: "var(--rls-ceo-bg)", color: "var(--rls-ceo)" }}
                  >
                    ceo-only
                  </span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>allow</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(clearance + team)</span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>allow</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(clearance)</span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>allow</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(clearance)</span>
                </td>
              </tr>
              <tr>
                <td><strong>Bob Singh</strong></td>
                <td>compliance</td>
                <td>
                  <span
                    className="px-2 py-0.5 rounded text-xs font-semibold"
                    style={{ background: "var(--rls-restricted-bg)", color: "var(--rls-restricted)" }}
                  >
                    restricted
                  </span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>deny</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(default)</span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>allow</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(clearance + team)</span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>allow</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(clearance)</span>
                </td>
              </tr>
              <tr>
                <td><strong>Carol Diaz</strong></td>
                <td>analyst</td>
                <td>
                  <span
                    className="px-2 py-0.5 rounded text-xs font-semibold"
                    style={{ background: "var(--rls-general-bg)", color: "var(--rls-general)" }}
                  >
                    general
                  </span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>deny</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(default)</span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>deny</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(default)</span>
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>allow</span>
                  <span className="text-xs ml-1" style={{ color: "var(--rls-muted)" }}>(clearance + team)</span>
                </td>
              </tr>
            </tbody>
          </table>

          <p>
            Switch personas using the selector in the{" "}
            <Link href="/" style={{ color: "var(--rls-accent)" }}>Demo tab</Link>.
            The trace list updates immediately to show only the traces that persona can see.
            The <strong>&ldquo;N traces hidden&rdquo;</strong> banner at the top of the trace
            list tells you how many were filtered out and which policy rule caused each denial.
            This is what native RLS would expose to the user — visibility into the fact that
            filtering is happening, without revealing what was filtered.
          </p>

          {/* ------------------------------------------------------------------ */}
          {/* Section 6: Gap Analysis                                              */}
          {/* ------------------------------------------------------------------ */}
          <h2>6. Gap Analysis: Demo vs. Native RLS</h2>

          <p>
            The demo proves the concept and lets the bank interact with the proposed behavior.
            It is not production-grade. The table below maps what the demo does today against
            what native Langfuse RLS would need to deliver.
          </p>

          <table>
            <thead>
              <tr>
                <th>Capability</th>
                <th>This Demo</th>
                <th>Native Langfuse RLS</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>Policy storage</strong></td>
                <td>Hardcoded in <code>lib/policies.ts</code></td>
                <td>Policy editor UI in Langfuse settings</td>
              </tr>
              <tr>
                <td><strong>Subject attributes</strong></td>
                <td>Hardcoded in <code>lib/personas.ts</code></td>
                <td>SSO claims / SCIM provisioning at login</td>
              </tr>
              <tr>
                <td><strong>Enforcement point</strong></td>
                <td>
                  <span style={{ color: "var(--rls-warn)", fontWeight: 600 }}>Client app layer</span>
                  {" "}(bypassable via direct API call)
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>Server-side</span>
                  {" "}(authoritative, no bypass)
                </td>
              </tr>
              <tr>
                <td><strong>ClickHouse push-down</strong></td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>No</span>
                  {" "}— fetch all traces, filter in JS
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>Yes</span>
                  {" "}— <code>WHERE</code> clause push-down, efficient at scale
                </td>
              </tr>
              <tr>
                <td><strong>Audit log</strong></td>
                <td>
                  <span style={{ color: "var(--rls-err)", fontWeight: 600 }}>None</span>
                </td>
                <td>Policy decision log per query, viewable by Org Admin</td>
              </tr>
              <tr>
                <td><strong>Export behavior</strong></td>
                <td>Not covered</td>
                <td>Must enforce in CSV / API exports too — not UI-only</td>
              </tr>
              <tr>
                <td><strong>Performance at scale</strong></td>
                <td>
                  <span style={{ color: "var(--rls-warn)", fontWeight: 600 }}>Degrades</span>
                  {" "}linearly with trace count
                </td>
                <td>
                  <span style={{ color: "var(--rls-ok)", fontWeight: 600 }}>Constant</span>
                  {" "}via index push-down in ClickHouse
                </td>
              </tr>
              <tr>
                <td><strong>Session invalidation</strong></td>
                <td>Not covered</td>
                <td>Policy change mid-session triggers re-evaluation</td>
              </tr>
            </tbody>
          </table>

          <div
            className="mt-4 px-4 py-3 rounded-lg text-sm"
            style={{
              background: "#FFF7ED",
              border: "1px solid #FED7AA",
              color: "#7C2D12",
            }}
          >
            <strong>ClickHouse push-down is critical at enterprise scale.</strong> Without
            it, every query fetches the full trace table and filters in the application layer.
            At the bank&apos;s projected data volumes, this will be untenable. Native RLS must push
            filter predicates down to ClickHouse <code style={{ background: "#FECACA", color: "#7C2D12" }}>WHERE</code>{" "}
            clauses to keep response times acceptable.
          </div>

          {/* ------------------------------------------------------------------ */}
          {/* Section 7: Open Questions                                            */}
          {/* ------------------------------------------------------------------ */}
          <h2>7. Open Questions for Langfuse Engineering</h2>

          <p>
            These questions need answers before this design can be implemented. Each
            represents a genuine decision point — not just implementation detail.
          </p>

          <ol>
            <li>
              <strong>Policy scope: per-org, per-project, or both?</strong>
              <p>
                Per-project is more granular and lets teams customize independently.
                Per-org enables centralized policy governance. The tradeoff: per-project
                creates policy sprawl at large orgs (hundreds of projects, each with
                custom policies). Recommend supporting both, with org-level policies
                taking precedence over project-level via explicit override syntax.
              </p>
            </li>
            <li>
              <strong>How are SSO claims mapped to subject attributes?</strong>
              <p>
                SCIM attribute mapping is the most common enterprise pattern (e.g., map
                the <code>clearance</code> SAML attribute from the IdP to{" "}
                <code>subject.clearance</code> in Langfuse). Custom claim transformers
                give more flexibility but add operational complexity. What&apos;s the
                planned interface?
              </p>
            </li>
            <li>
              <strong>Push-down to ClickHouse WHERE clauses or post-filter?</strong>
              <p>
                Post-filter (fetch all, filter in app layer) is simpler to implement
                and matches the current demo approach. Push-down is required for
                performance at scale — at millions of traces, post-filter becomes
                a bottleneck. Push-down also requires the policy engine to emit
                ClickHouse-compatible SQL predicates, which constrains policy
                expression language design. This is a fundamental architecture decision.
              </p>
            </li>
            <li>
              <strong>How are policy decisions cached?</strong>
              <p>
                Hot dashboards may evaluate policies thousands of times per second for
                the same user. Without caching, the policy engine becomes a bottleneck.
                Caching too aggressively means policy updates take time to propagate.
                What is the invalidation strategy?
              </p>
            </li>
            <li>
              <strong>Is RLS enforced in CSV/API export contexts?</strong>
              <p>
                UI-only enforcement is insufficient for regulated industries. A compliance
                officer who cannot see a trace in the UI should also not be able to
                download it via CSV export or retrieve it via the public API. Is RLS
                applied at the data layer (enforced everywhere) or only at the UI layer?
              </p>
            </li>
            <li>
              <strong>Policy update mid-session: does the view auto-refresh?</strong>
              <p>
                If an admin updates a policy while a user has the trace list open, does
                the user&apos;s view immediately reflect the change? Or on next page load?
                Or on next session? For security-critical use cases (revoking access),
                immediate invalidation is required.
              </p>
            </li>
            <li>
              <strong>Audit log access: who can view policy decisions?</strong>
              <p>
                Banks will need a full audit trail: which user requested which traces,
                which policy evaluated each request, what the outcome was, and when.
                Who has access to this log? Only Org Admins? Compliance roles? Can it
                be exported for external SIEM integration?
              </p>
            </li>
          </ol>

          {/* ------------------------------------------------------------------ */}
          {/* Section 8: Implementation Recommendation for a Global Bank         */}
          {/* ------------------------------------------------------------------ */}
          <h2>8. Implementation Recommendation for a Global Bank</h2>

          <p>
            While native RLS is not yet on the Langfuse roadmap, the bank has a viable path
            forward at each time horizon.
          </p>

          <h3>Short-Term (Available Today)</h3>

          <div
            className="px-4 py-3 rounded-lg"
            style={{
              background: "var(--rls-surface-2)",
              border: "1px solid var(--rls-border)",
            }}
          >
            <p className="mt-0">
              <strong>Separate Langfuse projects per sensitivity tier.</strong> Create three
              projects: <code>exec-ceo-only</code>, <code>compliance-restricted</code>, and{" "}
              <code>general</code>. Grant team members project-level access matching their
              clearance. SDK callers route traces to the correct project at ingest based on
              <code>metadata.classification</code>.
            </p>
            <p>
              <strong>Downside:</strong> Cross-project analytics require aggregating data
              outside Langfuse (e.g., in ClickHouse directly). Dashboard comparisons across
              sensitivity tiers are not possible in the Langfuse UI.
            </p>
          </div>

          <h3>Medium-Term (Implementable Now, Unofficial)</h3>

          <div
            className="px-4 py-3 rounded-lg"
            style={{
              background: "var(--rls-surface-2)",
              border: "1px solid var(--rls-border)",
            }}
          >
            <p className="mt-0">
              <strong>App-layer RLS wrapper</strong> — exactly what this demo implements.
              Build an internal API proxy in front of Langfuse that applies the policy engine
              before returning traces to the UI. Teams interact with the proxy, not directly
              with Langfuse.
            </p>
            <p>
              <strong>Downside:</strong> The enforcement point is bypassable if users have
              direct Langfuse API credentials. Requires maintaining a custom proxy service.
              Performance degrades with trace volume (no ClickHouse push-down).
            </p>
          </div>

          <h3>Long-Term (Advocate for Native RLS)</h3>

          <div
            className="px-4 py-3 rounded-lg"
            style={{
              background: "var(--rls-accent-light)",
              border: "1px solid #C4B5FD",
            }}
          >
            <p className="mt-0" style={{ color: "#4C1D95" }}>
              <strong>Use this demo as a concrete spec to advocate for native RLS</strong>{" "}
              with Langfuse engineering. The design described here is aligned with what
              Langfuse described as the long-term direction. A working demo with a
              real bank expressing production interest is the strongest possible signal
              for roadmap prioritization.
            </p>
            <p style={{ color: "#4C1D95" }}>
              Timeline is unknown — Langfuse has not committed. But the gap analysis and
              open questions above give engineering a clear picture of what &ldquo;done&rdquo;
              looks like, which shortens the design phase when they begin.
            </p>
          </div>

          {/* Footer */}
          <div
            className="mt-16 pt-6 flex items-center justify-between text-xs"
            style={{
              borderTop: "1px solid var(--rls-border)",
              color: "var(--rls-muted)",
            }}
          >
            <span>Langfuse RLS Demo — Design Document</span>
            <Link
              href="/"
              className="no-underline transition-opacity hover:opacity-70"
              style={{ color: "var(--rls-accent)" }}
            >
              ← Back to Demo
            </Link>
          </div>

        </div>
      </div>
    </>
  );
}
