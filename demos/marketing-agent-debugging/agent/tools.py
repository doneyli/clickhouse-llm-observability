"""
The seven tools the marketing agent can choose from, and their (synthetic) backends.

Each tool returns a `ToolOutcome`:
  result   — exactly what the MODEL sees as the tool result
  metadata — extra debugging context recorded on the Langfuse observation only
             (e.g. the retriever's full index records, which the model never sees)
  level / status_message — set when the tool fails, so the trace shows a red node

Fixes (`fixes` set, e.g. from `--fix exclude-archived`) switch a planted fault off so
the presenter can re-run the same task and show the trace after the fix:
  exclude-archived  — Context Manager stops serving archived documents
  clean-html        — fetch_page strips markup instead of returning the raw document
  parallel-tools    — the harness runs a turn's tool calls concurrently (see loop.py)
  terminal-pending  — snapshot tools say "unavailable, do not retry" instead of "retry"
"""

from __future__ import annotations

import json
import math
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .world import ENGINES, Customer, Page

FIXES = {"exclude-archived", "clean-html", "parallel-tools", "terminal-pending"}

# --------------------------------------------------------------------------------------
# Tool definitions (Anthropic format). loop.py converts them to the OpenAI shape for the
# generation input, which is what makes Langfuse list "available tools" on each step.
# --------------------------------------------------------------------------------------
TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "get_visibility_report",
        "description": "Share-of-voice report for a topic: which tracked prompts mention or cite "
                       "the brand on each AI answer engine, who gets cited instead, and which of "
                       "the brand's own pages are relevant.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "Topic or theme, e.g. 'pricing'."}},
            "required": ["topic"],
        },
    },
    {
        "name": "search_answer_engine",
        "description": "Latest captured answer (text + cited URLs) that one AI answer engine gives "
                       "for one tracked prompt.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The question a user asks the engine."},
                "engine": {"type": "string", "enum": ENGINES},
            },
            "required": ["prompt", "engine"],
        },
    },
    {
        "name": "retrieve_brand_context",
        "description": "Search the customer's Context Manager (the source of truth for brand facts, "
                       "pricing, products, voice and approved claims). Returns the top 3 passages.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": "Fetch the current text of a page on the customer's website from their CMS.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "check_claims",
        "description": "Fact-check factual claims against the Context Manager. Returns a verdict "
                       "(supported / contradicted / unsupported) and evidence for each claim.",
        "input_schema": {
            "type": "object",
            "properties": {"claims": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            "required": ["claims"],
        },
    },
    {
        "name": "queue_correction",
        "description": "Queue a correction request to an answer engine publisher for human approval "
                       "(nothing is sent until a marketer approves it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "engine": {"type": "string", "enum": ENGINES},
                "prompt": {"type": "string"},
                "inaccurate_statement": {"type": "string"},
                "corrected_statement": {"type": "string"},
                "source_url": {"type": "string"},
            },
            "required": ["engine", "prompt", "inaccurate_statement", "corrected_statement"],
        },
    },
    {
        "name": "stage_cms_draft",
        "description": "Stage a page draft in the customer's CMS for human review. Nothing is "
                       "published until a marketer approves it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_url": {"type": "string"},
                "title": {"type": "string"},
                "body_markdown": {"type": "string"},
                "change_summary": {"type": "string"},
            },
            "required": ["page_url", "title", "body_markdown"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_DEFINITIONS]
RETRIEVER_TOOLS = {"retrieve_brand_context"}


@dataclass
class ToolOutcome:
    result: Any
    metadata: Dict[str, Any] = field(default_factory=dict)
    level: Optional[str] = None            # "WARNING" | "ERROR"
    status_message: Optional[str] = None
    is_error: bool = False                 # sent to the model as tool_result.is_error


@dataclass
class RunState:
    """What the agent DID during one run — read by the reviewer and the policy guardrail."""
    calls: List[Dict[str, Any]] = field(default_factory=list)          # {"name", "input"} in order
    staged_drafts: List[Dict[str, Any]] = field(default_factory=list)
    corrections: List[Dict[str, Any]] = field(default_factory=list)
    checked_claims: List[Dict[str, Any]] = field(default_factory=list)  # verdicts returned
    polls: Dict[str, int] = field(default_factory=dict)                 # pending-snapshot polls per key


@dataclass
class ToolContext:
    customer: Customer
    fixes: set
    state: RunState
    claim_verifier: Optional[Callable[[List[str], List[List[Dict[str, Any]]]], List[Dict[str, Any]]]] = None
    rng: random.Random = field(default_factory=random.Random)
    simulate_latency: bool = True

    def sleep(self, lo: float, hi: float) -> float:
        secs = self.rng.uniform(lo, hi)
        if self.simulate_latency:
            time.sleep(secs)
        return secs


# --------------------------------------------------------------------------------------
# Text matching helpers (deterministic; no embeddings needed for a demo)
# --------------------------------------------------------------------------------------
_STOP = set("a an the of for to in on and or is are be do does i my our we you your it with "
            "how what which who can vs versus about this that per at by from as".split())


def tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9$]+", text.lower()) if t not in _STOP]


def _overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def bm25_rank(query: str, docs: List[Any], k1: float = 1.4, b: float = 0.75) -> List[tuple]:
    """Rank ContextDocs for a query with plain BM25 over title + text. Returns [(score, doc)]."""
    corpus = [tokenize(d.title + " " + d.text) for d in docs]
    n = len(corpus)
    avgdl = sum(len(c) for c in corpus) / max(n, 1)
    df: Dict[str, int] = {}
    for c in corpus:
        for t in set(c):
            df[t] = df.get(t, 0) + 1
    q = tokenize(query)
    scored = []
    for doc, toks in zip(docs, corpus):
        tf: Dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(toks) / avgdl))
        scored.append((s, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _index_docs(ctx: ToolContext):
    docs = ctx.customer.docs
    if "exclude-archived" in ctx.fixes:
        docs = [d for d in docs if d.status != "archived"]
    return docs


def search_context(ctx: ToolContext, query: str, top_k: int = 3) -> tuple[list, list]:
    """Returns (passages the model sees, full index records for the trace)."""
    ranked = bm25_rank(query, _index_docs(ctx))
    hits = [(s, d) for s, d in ranked if s > 0][:top_k]
    top = max((s for s, _ in hits), default=1.0) or 1.0
    passages = [{"title": d.title, "content": d.text, "relevance": round(s / top, 2)} for s, d in hits]
    records = [{"rank": i + 1, "doc_id": d.doc_id, "title": d.title, "bm25": round(s, 3),
                "status": d.status, "updated_at": d.updated_at, "kind": d.kind}
               for i, (s, d) in enumerate(hits)]
    return passages, records


# --------------------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------------------
def _match_prompt(customer: Customer, prompt: str):
    best = max(customer.prompts, key=lambda p: _overlap(prompt, p.prompt))
    return best if _overlap(prompt, best.prompt) >= 0.3 else None


def get_visibility_report(ctx: ToolContext, topic: str) -> ToolOutcome:
    ctx.sleep(0.4, 0.9)
    c = ctx.customer
    prompts = [p for p in c.prompts if _overlap(topic, p.topic) > 0 or _overlap(topic, p.prompt) >= 0.2]
    note = None
    if not prompts:
        prompts, note = c.prompts, f"No tracked prompts match '{topic}'; showing all tracked prompts."
    rows, mentions, cited = [], 0, 0
    competitor_cites: Dict[str, int] = {}
    for p in prompts:
        engines = {}
        for e in ENGINES:
            if e in c.pending_engines:
                engines[e] = {"status": "capture_in_progress"}
                continue
            s = p.snapshots[e]
            mentions += s.brand_mentioned
            cited += s.brand_cited
            for url in s.citations:
                dom = url.split("/")[2]
                if dom != c.domain:
                    competitor_cites[dom] = competitor_cites.get(dom, 0) + 1
            engines[e] = {"brand_mentioned": s.brand_mentioned, "brand_cited": s.brand_cited,
                          "cited_domains": sorted({u.split("/")[2] for u in s.citations})}
        rows.append({"prompt": p.prompt, "monthly_volume": p.monthly_volume, "engines": engines})
    measured = len(prompts) * (len(ENGINES) - len(c.pending_engines))
    pages = [pg.url for pg in c.pages if _overlap(topic, pg.title + " " + pg.url.replace("-", " ")) > 0] \
        or [pg.url for pg in c.pages]
    result = {
        "brand": c.name, "topic": topic,
        "summary": {"answers_measured": measured, "brand_mentioned": mentions, "brand_cited": cited,
                    "top_cited_other_domains": sorted(competitor_cites, key=competitor_cites.get, reverse=True)[:4]},
        "prompts": rows,
        "your_relevant_pages": pages,
    }
    if c.pending_engines:
        result["engines_pending"] = c.pending_engines
    if note:
        result["note"] = note
    return ToolOutcome(result, metadata={"prompts_matched": len(prompts)})


def search_answer_engine(ctx: ToolContext, prompt: str, engine: str) -> ToolOutcome:
    c = ctx.customer
    if engine not in ENGINES:
        return ToolOutcome({"error": f"unknown engine {engine!r}", "engines": ENGINES},
                           level="ERROR", status_message="unknown engine", is_error=True)
    if engine in c.pending_engines:
        ctx.sleep(1.2, 2.2)
        if "terminal-pending" in ctx.fixes:
            return ToolOutcome({"status": "unavailable", "engine": engine,
                                "message": f"No {engine} snapshot is available for this account this week. "
                                           "Do not retry; report the engine as unavailable."},
                               level="WARNING", status_message="snapshot unavailable")
        # The capture job reports progress that climbs and then sticks at 99% — it never
        # completes for this account, but every answer looks like "almost there".
        key = f"{engine}|{prompt.strip().lower()}"
        n = ctx.state.polls[key] = ctx.state.polls.get(key, 0) + 1
        progress = min(99, 35 + 16 * (n - 1))
        return ToolOutcome({"status": "capture_in_progress", "engine": engine, "progress_percent": progress,
                            "message": f"Snapshot capture for this prompt is {progress}% complete. "
                                       "Retry the same call to fetch the result."},
                           metadata={"poll": n, "capture_job": "stalled"},
                           level="WARNING", status_message=f"snapshot capture in progress ({progress}%)")
    ctx.sleep(0.8, 1.8)
    tp = _match_prompt(c, prompt)
    if tp is None:
        return ToolOutcome({"status": "not_tracked", "message": "This prompt is not tracked for this brand.",
                            "tracked_prompts": [p.prompt for p in c.prompts]},
                           level="WARNING", status_message="prompt not tracked")
    s = tp.snapshots[engine]
    return ToolOutcome({"engine": engine, "prompt": tp.prompt, "captured": "this week",
                        "answer": s.answer, "citations": s.citations},
                       metadata={"matched_prompt": tp.prompt, "match_score": round(_overlap(prompt, tp.prompt), 2)})


def retrieve_brand_context(ctx: ToolContext, query: str) -> ToolOutcome:
    ctx.sleep(0.15, 0.4)
    passages, records = search_context(ctx, query)
    return ToolOutcome(
        {"passages": passages},
        metadata={"index": f"context-manager/{ctx.customer.id}", "retrieval": "bm25", "top_k": 3,
                  "include_archived": "exclude-archived" not in ctx.fixes,
                  "documents_in_index": len(_index_docs(ctx)), "hits": records})


def _raw_html(page: Page) -> str:
    """What the customer's CMS returns when it ignores format=text: the whole document."""
    nav = "".join(f'<li class="nav-item"><a href="https://fabrikam.example/{s}" data-track="nav_{s}">{s.title()}</a></li>'
                  for s in ["road", "trail", "stability", "wide", "sale", "guides", "stores", "returns",
                            "gift-cards", "rewards", "about", "careers", "press", "help"] * 6)
    tracking = json.dumps({"dataLayer": [{"event": "pageview", "sku_impressions": [
        {"sku": f"FAB-{i:05d}", "name": f"Product {i}", "price": 90 + i % 70, "list": "related",
         "position": i, "variant": ["2E", "4E", "D", "B"][i % 4]} for i in range(260)]}]})
    css = "".join(f".c{i}{{margin:{i % 7}px;padding:{i % 5}px;color:#{i * 2654435761 % 0xFFFFFF:06x}}}" for i in range(700))
    return (f"<!DOCTYPE html><html><head><title>{page.title} | Fabrikam Running</title><style>{css}</style>"
            f"<script>window.__STATE__={tracking}</script></head><body><header><ul>{nav}</ul></header>"
            f"<main><article><h1>{page.title}</h1><p>{page.text}</p></article></main>"
            f"<footer><ul>{nav}</ul><script src=\"https://cdn.fabrikam.example/app.4f9c2.js\"></script></footer></body></html>")


def fetch_page(ctx: ToolContext, url: str) -> ToolOutcome:
    page = ctx.customer.page(url)
    if page is None or page.missing:
        ctx.sleep(0.3, 0.6) if page is None else ctx.sleep(4.0, 6.0)
        return ToolOutcome({"url": url, "status_code": 404, "error": "Not found"},
                           metadata={"cms": "slow-origin" if page else "default"},
                           level="ERROR", status_message="HTTP 404 from CMS", is_error=True)
    took = ctx.sleep(4.0, 6.0) if page.slow else ctx.sleep(0.3, 0.7)
    meta = {"cms_cache": "MISS" if page.slow else "HIT", "origin_seconds": round(took, 2)}
    if page.raw_html and "clean-html" not in ctx.fixes:
        body = _raw_html(page)
        meta["content_type"] = "text/html (format=text ignored)"
    else:
        body = page.text
        meta["content_type"] = "text/plain"
    meta["content_chars"] = len(body)
    return ToolOutcome({"url": page.url, "status_code": 200, "title": page.title, "content": body}, metadata=meta)


def check_claims(ctx: ToolContext, claims: List[str]) -> ToolOutcome:
    claims = [c for c in claims if isinstance(c, str) and c.strip()][:12]
    if not claims:
        return ToolOutcome({"error": "no claims given"}, level="ERROR", status_message="empty input", is_error=True)
    evidence = []
    records = []
    for claim in claims:
        passages, recs = search_context(ctx, claim, top_k=2)
        evidence.append(passages)
        records.append({"claim": claim, "evidence_docs": recs})
    if ctx.claim_verifier is None:
        raise RuntimeError("check_claims needs a claim_verifier (set by loop.py)")
    verdicts = ctx.claim_verifier(claims, evidence)
    ctx.state.checked_claims.extend(verdicts)
    return ToolOutcome({"results": verdicts},
                       metadata={"index": f"context-manager/{ctx.customer.id}",
                                 "include_archived": "exclude-archived" not in ctx.fixes,
                                 "evidence": records})


def queue_correction(ctx: ToolContext, engine: str, prompt: str, inaccurate_statement: str,
                     corrected_statement: str, source_url: str = "") -> ToolOutcome:
    ctx.sleep(0.2, 0.4)
    item = {"correction_id": "cor_" + uuid.uuid4().hex[:8], "engine": engine, "prompt": prompt,
            "inaccurate_statement": inaccurate_statement, "corrected_statement": corrected_statement,
            "source_url": source_url, "status": "awaiting_approval"}
    ctx.state.corrections.append(item)
    return ToolOutcome({"correction_id": item["correction_id"], "status": "awaiting_approval"})


def stage_cms_draft(ctx: ToolContext, page_url: str, title: str, body_markdown: str,
                    change_summary: str = "") -> ToolOutcome:
    ctx.sleep(0.3, 0.6)
    draft_id = "drf_" + uuid.uuid4().hex[:8]
    ctx.state.staged_drafts.append({"draft_id": draft_id, "page_url": page_url, "title": title,
                                    "body_markdown": body_markdown, "change_summary": change_summary})
    return ToolOutcome({"draft_id": draft_id, "status": "pending_review",
                        "review_url": f"https://cms.{ctx.customer.domain}/drafts/{draft_id}"})


_IMPLS: Dict[str, Callable[..., ToolOutcome]] = {
    "get_visibility_report": get_visibility_report,
    "search_answer_engine": search_answer_engine,
    "retrieve_brand_context": retrieve_brand_context,
    "fetch_page": fetch_page,
    "check_claims": check_claims,
    "queue_correction": queue_correction,
    "stage_cms_draft": stage_cms_draft,
}


def run_tool(ctx: ToolContext, name: str, args: Dict[str, Any]) -> ToolOutcome:
    ctx.state.calls.append({"name": name, "input": args})
    impl = _IMPLS.get(name)
    if impl is None:
        return ToolOutcome({"error": f"unknown tool {name!r}"}, level="ERROR",
                           status_message="unknown tool", is_error=True)
    try:
        return impl(ctx, **args)
    except TypeError as e:  # wrong/missing arguments from the model
        return ToolOutcome({"error": f"invalid arguments: {e}"}, level="ERROR",
                           status_message="invalid arguments", is_error=True)
