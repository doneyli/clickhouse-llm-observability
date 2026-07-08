import type { Subject } from "./types";

export const PERSONAS: Subject[] = [
  { id: "alice", name: "Alice Chen",  team: "executive",  clearance: "ceo-only"   },
  { id: "bob",   name: "Bob Singh",   team: "compliance", clearance: "restricted" },
  { id: "carol", name: "Carol Diaz",  team: "analyst",    clearance: "general"    },
];

export function getPersona(id: string): Subject | undefined {
  return PERSONAS.find((p) => p.id === id);
}
