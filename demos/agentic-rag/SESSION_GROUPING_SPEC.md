# Spec: Session grouping for agentic-rag via LibreChat

**Status:** Implemented (this branch — the recommended approach in §4 was built as-is)
**Related:** PR #50 (real-estate traces→sessions fix); fleet audit finding #1
**Owner:** doneyli
**Scope size:** Small (≈4 files, no schema/infra change)

---

## 1. Problem

When the agentic-rag demo is driven through **LibreChat** (its primary multi-turn
surface, via the `agentic_rag_answer` MCP tool), every turn lands in Langfuse as
its **own randomly-named session**, so a multi-turn conversation does **not** group
into a single Langfuse **Session**.

This is *not* the PR #50 anti-pattern — each turn is correctly its own trace, fully
scored (`retrieval_relevance`, `groundedness`, …). The gap is purely the missing
**session correlation** across turns of one conversation.

### Evidence (current behavior)

- `mcp-rag-retriever/server.py:72-98` — the tool defaults `session_id=""` and
  forwards `session_id or None`:
  ```python
  def agentic_rag_answer(question: str, session_id: str = "") -> dict:
      ...
      payload = json.dumps({"question": question, "session_id": session_id or None})
  ```
- The MCP tool calls the graph over HTTP, not in-process:
  `agentic_rag_answer` → `POST http://agentic-rag:8000/query`
  (`demos/agentic-rag/server.py:46-48`) → `AgenticRAG.run(session_id=req.session_id)`.
- `demos/agentic-rag/graph.py:286-287` — with no session id, a **fresh random** one
  is minted per call:
  ```python
  def run(self, question, session_id=None):
      session_id = session_id or lf.new_session_id()   # f"agentic-rag-{uuid4hex8}"
  ```
- `librechat.yaml:16-18` — the `rag-retriever` MCP block has **no headers** and no
  placeholder, so nothing conversation-stable reaches the tool.
- `scripts/seed-librechat-agents.sh` (~L503-511) — the agent is told to call the tool
  "passing the question verbatim"; `session_id` is never mentioned, so the LLM never
  supplies one and the empty-string default always fires.
- **CLI path is already correct:** `demos/agentic-rag/main.py:38,54` mints one
  session per run and reuses it across turns → the CLI groups; only the LibreChat
  path is affected.

---

## 2. Goal / Non-goals

**Goal:** A multi-turn agentic-rag conversation in LibreChat produces one Langfuse
**Session** (one per LibreChat conversation) containing one trace per turn — matching
the model PR #50 established for real-estate and LibreChat's own native behavior.

**Non-goals:**
- **Not** adding conversational *memory* to the graph. `AgentState` is intentionally
  stateless across turns; this spec only fixes trace **grouping**, not context carryover.
- **Not** changing the CLI or direct-HTTP behavior (both already pass/derive a session).
- **Not** touching the text-to-sql / vector-rag session gap (audit finding #2 — separate).

---

## 3. Key technical finding (verified)

LibreChat v0.8.7 **can** hand a stable per-conversation id to an MCP server via a
**header placeholder**. Confirmed end-to-end against the running `librechat-api`
container and the MCP SDKs:

- LibreChat supports `{{LIBRECHAT_BODY_CONVERSATIONID}}` in an MCP server's
  `headers`/`url` (`packages/api/src/mcp/utils.ts`, `utils/env.ts:125` —
  `ALLOWED_BODY_FIELDS = ['conversationId','parentMessageId','messageId']`).
- It is resolved **per tool call** (`MCPManager.callTool()` → `processMCPEnv({body: requestBody})`),
  and configs using a `LIBRECHAT_BODY_*` placeholder are marked
  `requiresEphemeralUserConnection` (fresh connect per call), so the header always
  reflects the current turn.
- `conversationId` is a **server-assigned, stable UUID from turn 1**
  (`api/server/controllers/agents/request.js:227-232` replaces `"new"` with a real
  UUID before any tool dispatch); LibreChat itself uses it as the LangGraph `thread_id`.
- The header reaches the tool-call POST (SSE transport `requestInit.headers`) and is
  readable server-side: the Python `mcp` SDK (FastMCP, `mcp==1.27.2`) attaches the
  Starlette `Request` per call → `ctx.request_context.request.headers`.
  - Caveat: this `mcp` SDK has **no** `get_http_headers()` helper (that's the separate
    `fastmcp` package); access is via `ctx.request_context.request.headers` directly.

Baseline (no placeholder) MCP connections are pooled **per `userId:serverName`**
(`UserConnectionManager.ts:55`), i.e. per-user, never per-conversation — which is why
the transport-session approach can't give conversation granularity.

---

## 4. Proposed approach (recommended)

Resolve the session id **on the MCP server** from the LibreChat conversation header,
with a clear precedence, and keep everything else unchanged.

### 4.1 `librechat.yaml` — pass the conversation id as a header
```yaml
  rag-retriever:
    type: sse
    url: http://mcp-rag-retriever:8000/sse
    headers:
      X-LibreChat-Conversation-Id: "{{LIBRECHAT_BODY_CONVERSATIONID}}"
```
`mcp-rag-retriever:8000` is already in `mcpSettings.allowedDomains`, so no allowlist
change is needed. (Trade-off: the placeholder forces an ephemeral MCP connect per tool
call — acceptable for a demo; see Risks.)

### 4.2 `mcp-rag-retriever/server.py` — read the header, derive the session id
Give the tool a `Context`, and choose the session id by this precedence:

1. an explicit non-empty `session_id` **argument** (lets direct callers / tests override);
2. else the `X-LibreChat-Conversation-Id` **header**, namespaced for readability
   (e.g. `session_id = f"librechat-{conversation_id}"`);
3. else `""` → `None` → graph mints a random session (unchanged for non-LibreChat callers).

```python
from mcp.server.fastmcp import Context

@mcp.tool()
def agentic_rag_answer(question: str, session_id: str = "", ctx: Context = None) -> dict:
    resolved = session_id.strip()
    if not resolved and ctx is not None:
        try:
            conv = ctx.request_context.request.headers.get("x-librechat-conversation-id")
            if conv:
                resolved = f"librechat-{conv}"
        except Exception:
            pass  # never fail the tool over an optional grouping id
    payload = json.dumps({"question": question, "session_id": resolved or None})
    ...
```
No change to `graph.py` / `server.py` — they already thread `session_id` through to
`propagate_attributes(session_id=...)`.

### 4.3 `scripts/seed-librechat-agents.sh` — (optional) leave the arg unmentioned
The header path is header-driven and does **not** rely on the LLM passing `session_id`,
so the agent instructions can stay as-is. Optionally add one line documenting that
session grouping is automatic.

### 4.4 Session-id shape
Recommend `librechat-<conversationId>` (namespaced) so LibreChat-originated sessions are
visually distinct from CLI sessions (`agentic-rag-<hex8>`) in the Langfuse Sessions list,
and don't collide.

---

## 5. Alternatives considered

| # | Approach | Why not |
|---|---|---|
| 2 | Instruct the LLM to pass `session_id` as a tool arg | Relies on per-turn model compliance; the model has no access to a stable conversationId to pass — fragile. |
| 3 | Group by user via `{{LIBRECHAT_USER_ID}}` header | Simpler, but merges *all* of a user's conversations into one session — wrong granularity for the demo. Usable only as a degraded fallback. |
| 4 | Use the MCP transport/session id | Connections are pooled per `userId:serverName`, so this collapses to per-user (≈ app-wide here) — strictly worse than #3. |

---

## 6. Scope & files to change (for the implementation PR)

**In scope**
- `librechat.yaml` — add the `headers` block (§4.1).
- `mcp-rag-retriever/server.py` — `ctx: Context` + header-derived session id (§4.2).
- (optional) `scripts/seed-librechat-agents.sh` — one clarifying line.
- `demos/agentic-rag/DEMO_SCRIPT.md` — note the Sessions grouping in the LibreChat path.
- Pin/verify `mcp` SDK version in `mcp-rag-retriever/requirements.txt` (relies on
  `ctx.request_context.request`).

**Out of scope**
- Graph memory / cross-turn context.
- text-to-sql & vector-rag session wiring (finding #2).
- Any Langfuse platform/SDK upgrade (#51, #52).

---

## 7. Verification plan

1. **LibreChat multi-turn (primary):** ask 2–3 questions in one LibreChat conversation
   → Langfuse **Sessions** shows a single `librechat-<uuid>` session with one
   agentic-rag trace per turn, each fully scored. Start a **new conversation** →
   a new session.
2. **CLI unchanged:** `main.py` interactive run → still one `agentic-rag-<hex8>` session
   for the run.
3. **Direct HTTP override:** `POST /query` with an explicit `session_id` → honored;
   without a session id and no header → random fallback (unchanged).
4. **Robustness:** tool call with no `ctx`/header available must not raise (falls back to
   random session).
5. Confirm via `GET /api/public/sessions/librechat-<uuid>` that the turns are grouped.

**Definition of done:** two consecutive LibreChat turns in one conversation appear as
two traces under one Langfuse session; the trace shape/scores from
[[librechat-agentic-rag-full-graph]] are unchanged.

---

## 8. Risks / open questions

- **Header trust:** `mcp-rag-retriever` is internal-only (Docker network; in
  `allowedDomains`, not publicly exposed), and the header is set by LibreChat, so it is
  a trusted same-origin value — not attacker-controlled. No auth change needed.
- **SDK internal surface:** `ctx.request_context.request` is stable across the examined
  `mcp` 1.x line but is not part of FastMCP's minimal public API — wrap the read in
  `try/except` (done in §4.2) and pin the `mcp` version so an upgrade can't silently
  break it.
- **Latency:** `LIBRECHAT_BODY_*` placeholders trigger an ephemeral MCP connect per tool
  call. Measure; expected to be small and acceptable for a demo. If it matters, revisit.
- **Open:** confirm the header name casing is preserved (Starlette headers are
  case-insensitive — read lower-cased, as in §4.2).
