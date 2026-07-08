import type { Policy } from "./types";

export const DEMO_POLICIES: Policy[] = [
  {
    id: "allow-clearance-ge-classification",
    effect: "allow",
    match: {},
    reason: "Subject's clearance level is >= trace classification level.",
  },
  {
    id: "deny-default",
    effect: "deny",
    match: {},
    reason: "No matching allow rule — default deny.",
  },
];
