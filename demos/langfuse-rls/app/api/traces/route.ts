import { NextRequest, NextResponse } from "next/server";
import { listTraces } from "@/lib/langfuse-client";
import { getPersona, PERSONAS } from "@/lib/personas";
import { evaluateBatch } from "@/lib/rls-policy";
import type { TracesApiResponse } from "@/lib/types";

export async function GET(req: NextRequest): Promise<NextResponse<TracesApiResponse>> {
  const personaId = req.nextUrl.searchParams.get("persona") ?? "alice";
  const persona = getPersona(personaId) ?? PERSONAS[0];

  const traces = await listTraces();

  const evaluated = evaluateBatch(persona, traces);
  const visible = evaluated.filter((t) => t._rls.allow);
  const deniedList = evaluated.filter((t) => !t._rls.allow);

  const response: TracesApiResponse = {
    persona,
    visible,
    denied: {
      count: deniedList.length,
      samples: deniedList.slice(0, 5).map((t) => ({
        traceId: t.id,
        name: t.name,
        reason: t._rls.reason,
        matchedRule: t._rls.matchedRule,
      })),
    },
    ...(traces.length === 0 ? { error: "Langfuse unreachable or no traces found." } : {}),
  };

  return NextResponse.json(response);
}
