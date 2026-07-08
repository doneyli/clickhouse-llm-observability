"use client";

import type { Subject, TeamName, ClearanceLevel } from "@/lib/types";

interface PersonaSwitcherProps {
  personas: Subject[];
  selected: Subject;
  onChange: (p: Subject) => void;
}

const TEAM_COLORS: Record<TeamName, { bg: string; text: string }> = {
  executive:  { bg: "#EDE9FE", text: "#7C3AED" },
  compliance: { bg: "#FFF7ED", text: "#EA580C" },
  analyst:    { bg: "#F0F9FF", text: "#0284C7" },
};

const CLEARANCE_COLORS: Record<ClearanceLevel, { bg: string; text: string }> = {
  "ceo-only":   { bg: "var(--rls-ceo-bg)",        text: "var(--rls-ceo)"        },
  "restricted": { bg: "var(--rls-restricted-bg)",  text: "var(--rls-restricted)" },
  "general":    { bg: "var(--rls-general-bg)",      text: "var(--rls-general)"    },
};

function TeamBadge({ team }: { team: TeamName }) {
  const { bg, text } = TEAM_COLORS[team];
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-medium capitalize"
      style={{ background: bg, color: text }}
    >
      {team}
    </span>
  );
}

function ClearanceBadge({ clearance }: { clearance: ClearanceLevel }) {
  const { bg, text } = CLEARANCE_COLORS[clearance];
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ background: bg, color: text }}
    >
      {clearance}
    </span>
  );
}

const PERSONA_ROLE: Record<string, string> = {
  alice: "Chief Executive",
  bob:   "Compliance Officer",
  carol: "Senior Analyst",
};

export default function PersonaSwitcher({ personas, selected, onChange }: PersonaSwitcherProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row">
      {personas.map((persona) => {
        const isSelected = persona.id === selected.id;
        return (
          <button
            key={persona.id}
            onClick={() => onChange(persona)}
            className="flex-1 rounded-xl border-2 p-4 text-left transition-all duration-150 focus:outline-none focus-visible:ring-2"
            style={{
              background: isSelected ? "var(--rls-accent-light)" : "var(--rls-surface)",
              borderColor: isSelected ? "var(--rls-accent)" : "var(--rls-border)",
              boxShadow: isSelected
                ? "0 0 0 1px var(--rls-accent), 0 2px 8px rgba(124,58,237,0.12)"
                : "0 1px 3px rgba(0,0,0,0.06)",
            }}
          >
            <div className="mb-2 flex items-center gap-2">
              <div
                className="flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold"
                style={{
                  background: isSelected ? "var(--rls-accent)" : "var(--rls-surface-2)",
                  color: isSelected ? "#fff" : "var(--rls-muted)",
                }}
              >
                {persona.name[0]}
              </div>
              <div>
                <div
                  className="text-sm font-bold leading-tight"
                  style={{ color: isSelected ? "var(--rls-accent)" : "var(--rls-text)" }}
                >
                  {persona.name}
                </div>
                <div className="text-xs" style={{ color: "var(--rls-muted)" }}>
                  {PERSONA_ROLE[persona.id] ?? persona.id}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <TeamBadge team={persona.team} />
              <ClearanceBadge clearance={persona.clearance} />
            </div>
          </button>
        );
      })}
    </div>
  );
}
