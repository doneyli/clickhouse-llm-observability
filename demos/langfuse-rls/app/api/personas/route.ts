import { NextResponse } from "next/server";
import { PERSONAS } from "@/lib/personas";

export async function GET(): Promise<NextResponse> {
  return NextResponse.json(PERSONAS);
}
