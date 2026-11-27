/**
 * src/readback.ts offline: cursor and page walks (empty pages, repeated cursors,
 * bounds), and the two observation shapes compare-traces and score-live-sessions
 * read — Observations v2 and the v1 list a 3.x server serves — with no legacy
 * /traces call on either. No network, no keys.
 */
import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  apiFlavourFor,
  cursorAll,
  pagesAll,
  recentTurns,
  sessionOf,
  traceObservations,
  type Get,
} from "../src/readback.js";

/** A `Get` that answers from a function of (path, params) and records every call. */
function getter(answer: (path: string, q: URLSearchParams) => unknown): { get: Get; calls: string[] } {
  const calls: string[] = [];
  const get: Get = async <T>(pathWithQuery: string) => {
    calls.push(pathWithQuery);
    const [path = "", qs = ""] = pathWithQuery.split("?", 2);
    return answer(path, new URLSearchParams(qs)) as T | undefined;
  };
  return { get, calls };
}

test("apiFlavourFor falls back to v1 on a 3.x server only", () => {
  assert.equal(apiFlavourFor("3.221.1"), "v1");
  assert.equal(apiFlavourFor("4.88.0"), "v2");
  assert.equal(apiFlavourFor(undefined), "v2");
});

test("sessionOf reads the v4 column first, then the v3 OTel attribute", () => {
  assert.equal(sessionOf({ sessionId: "s4" }), "s4");
  assert.equal(sessionOf({ metadata: { attributes: { "session.id": "s3" } } }), "s3");
  assert.equal(sessionOf({ sessionId: "", metadata: { sessionId: "free-form, defect 5" } }), undefined);
});

test("cursorAll keeps going through empty pages until the cursor is gone", async () => {
  const pages: Record<string, unknown> = {
    "": { data: [], meta: { cursor: "a" } },
    a: { data: [], meta: { cursor: "b" } },
    b: { data: [{ id: 1 }], meta: { cursor: "c" } },
    c: { data: [{ id: 2 }], meta: {} },
  };
  const { get, calls } = getter((_p, q) => pages[q.get("cursor") ?? ""]);
  assert.deepEqual(await cursorAll(get, "/x", { fields: "core" }), [{ id: 1 }, { id: 2 }]);
  assert.equal(calls.length, 4);
  assert.match(calls[0]!, /^\/x\?fields=core&limit=100$/);
});

test("cursorAll accepts meta.nextCursor too", async () => {
  const { get } = getter((_p, q) => (q.get("cursor") ? { data: [2] } : { data: [1], meta: { nextCursor: "n" } }));
  assert.deepEqual(await cursorAll(get, "/x", {}), [1, 2]);
});

test("cursorAll refuses a repeated cursor and a walk past its bound", async () => {
  const same = getter(() => ({ data: [1], meta: { cursor: "loop" } }));
  await assert.rejects(cursorAll(same.get, "/x", {}), /returned cursor loop twice/);
  let n = 0;
  const endless = getter(() => ({ data: [], meta: { cursor: `c${(n += 1)}` } }));
  await assert.rejects(cursorAll(endless.get, "/x", {}, { maxPages: 3 }), /more than 3 pages/);
  n = 0;
  assert.deepEqual(await cursorAll(endless.get, "/x", {}, { maxPages: 3, sample: true }), []);
});

test("pagesAll follows meta.totalPages and stops on an empty page", async () => {
  const { get, calls } = getter((_p, q) => {
    const page = Number(q.get("page"));
    return { data: page <= 2 ? [page] : [], meta: { totalPages: 5 } };
  });
  assert.deepEqual(await pagesAll(get, "/y", { traceId: "t" }), [1, 2]);
  assert.equal(calls.length, 3);
  assert.match(calls[0]!, /^\/y\?traceId=t&limit=100&page=1$/);
});

test("traceObservations reads Observations v2 with the caller's field groups", async () => {
  const { get, calls } = getter(() => ({ data: [{ id: "o1", type: "GENERATION", input: "null" }], meta: {} }));
  const rows = await traceObservations(get, "v2", "t1", "core,basic,io,metadata");
  assert.equal(rows.length, 1);
  const q = new URLSearchParams(calls[0]!.split("?")[1]);
  assert.equal(calls[0]!.split("?")[0], "/api/public/v2/observations");
  assert.deepEqual([q.get("traceId"), q.get("fields"), q.get("limit")], ["t1", "core,basic,io,metadata", "1000"]);
});

test("traceObservations on a 3.x server reads the v1 list, never /traces", async () => {
  const { get, calls } = getter((path) =>
    path === "/api/public/observations" ? { data: [{ id: "o1", type: "SPAN", input: { message: "hi" } }], meta: { totalPages: 1 } } : undefined,
  );
  const rows = await traceObservations(get, "v1", "t1");
  assert.deepEqual(rows.map((r) => r.input), [{ message: "hi" }]);
  assert.ok(calls.every((c) => c.startsWith("/api/public/observations?") && c.includes("traceId=t1")));
});

test("recentTurns on v2: roots with the session column, newest first", async () => {
  const { get, calls } = getter(() => ({
    data: [
      { traceId: "t1", sessionId: "s1", startTime: "2026-11-27T10:00:00Z", name: "handle-chat-message" },
      { traceId: "t2", sessionId: "s1", startTime: "2026-11-27T11:00:00Z", name: "handle-chat-message" },
      { traceId: "t3", sessionId: null, startTime: "2026-11-27T12:00:00Z", name: "chat: hi" },
    ],
    meta: {},
  }));
  const turns = await recentTurns(get, "v2", "handle-chat-message");
  assert.deepEqual(turns.map((t) => [t.traceId, t.sessionId]), [["t3", null], ["t2", "s1"], ["t1", "s1"]]);
  const q = new URLSearchParams(calls[0]!.split("?")[1]);
  assert.deepEqual([q.get("fields"), q.get("isRootObservation")], ["core,basic", "true"]);
});

test("recentTurns on a 3.x server: named spans, session from the OTel attribute, one row per trace", async () => {
  const { get, calls } = getter(() => ({
    data: [
      // A dataset-run trace holds several turns; it collapses to one row.
      { traceId: "exp", startTime: "2026-11-27T09:00:00Z", name: "handle-chat-message", metadata: { attributes: { "session.id": "exp-run-a" } } },
      { traceId: "exp", startTime: "2026-11-27T09:01:00Z", name: "handle-chat-message", metadata: { attributes: { "session.id": "exp-run-a" } } },
      { traceId: "t1", startTime: "2026-11-27T10:00:00Z", name: "handle-chat-message", metadata: { attributes: { "session.id": "cmp-good-1" } } },
    ],
    meta: { totalPages: 1 },
  }));
  const turns = await recentTurns(get, "v1", "handle-chat-message");
  assert.deepEqual(turns.map((t) => [t.traceId, t.sessionId]), [["t1", "cmp-good-1"], ["exp", "exp-run-a"]]);
  const [path, qs] = calls[0]!.split("?");
  assert.equal(path, "/api/public/observations");
  const q = new URLSearchParams(qs);
  assert.deepEqual([q.get("name"), q.get("type")], ["handle-chat-message", "SPAN"]);
});
