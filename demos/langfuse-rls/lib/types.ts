export type ClearanceLevel = "ceo-only" | "restricted" | "general";
export type TeamName = "executive" | "compliance" | "analyst";
export type Classification = "ceo-only" | "restricted" | "general";

export interface Subject {
  id: string;
  name: string;
  team: TeamName;
  clearance: ClearanceLevel;
}

export interface TraceMetadata {
  classification: Classification;
  team: TeamName;
  topic: string;
}

export interface LangfuseTrace {
  id: string;
  name: string;
  userId?: string;
  metadata?: TraceMetadata & Record<string, unknown>;
  tags?: string[];
  input?: unknown;
  output?: unknown;
  timestamp?: string;
  createdAt?: string;
}

export interface PolicyMatch {
  subject?: {
    team?: TeamName | TeamName[];
    clearance?: ClearanceLevel | ClearanceLevel[];
  };
  object?: {
    classification?: Classification | Classification[];
    team?: TeamName | TeamName[];
  };
}

export interface Policy {
  id: string;
  effect: "allow" | "deny";
  match: PolicyMatch;
  reason: string;
}

export interface EvaluationResult {
  allow: boolean;
  matchedRule: string;
  reason: string;
}

export interface TracesApiResponse {
  visible: Array<LangfuseTrace & { _rls: EvaluationResult }>;
  denied: {
    count: number;
    samples: Array<{ traceId: string; name: string; reason: string; matchedRule: string }>;
  };
  persona: Subject;
  error?: string;
}
