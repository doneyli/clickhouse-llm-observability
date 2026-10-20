"""
Examiner workbench — the human in the loop. Run: ./scripts/run_services.sh  →  http://127.0.0.1:8095

  GET  /api/claims                    the claim queue
  GET  /api/claims/{id}               claim header, documents, status, latest recommendation
  POST /api/claims/{id}/process       run the recommendation (LangGraph, stops at the examiner interrupt)
  POST /api/claims/{id}/review        approve / override → resume the graph → letter
  POST /api/claims/{id}/ask           ask the examiner assistant
  GET  /api/sops/{sop_id}             SOP text (citations click through)
  GET  /documents/{id}/{file}         the scanned documents
  GET  /api/presenter/{id}            the oracle's expected outcome (presenter console only)

State (thread ids, last results) is in memory: restarting the portal forgets paused claims.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from claims import claimsdb, config, graph, oracle, sops

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Northwind Life & Annuity — claims workbench")
STATE: dict[str, dict] = {}   # claim_id → {thread_id, recommendation result, review, letter, answers}
LOCKS: dict[str, asyncio.Lock] = {}


class ProcessIn(BaseModel):
    sop_label: str = "production"
    release: Optional[str] = None


class ReviewIn(BaseModel):
    action: Literal["approve", "override"]
    decision: Optional[Literal["PAY", "PEND", "DENY", "REFER_SIU"]] = None
    reason: Literal["missed-rule", "wrong-rule", "calc-error", "doc-misread", "policy-data-error", "other"] = "other"
    missed_rule_ids: list[str] = []
    note: str = ""
    examiner_id: str = "examiner.demo"


class AskIn(BaseModel):
    question: str
    examiner_id: str = "examiner.demo"


def _summary(c: dict) -> dict:
    s = STATE.get(c["claim_id"], {})
    status = "new"
    if s.get("recommendation"):
        status = "awaiting examiner" if not s.get("review") else "decided"
    return {"claim_id": c["claim_id"], "scenario": c["scenario"], "line": c["line"], "state": c["state"],
            "set": c["_set"], "insured": c["insured"]["name"], "tags": c.get("tags") or [], "status": status,
            "recommendation": (s.get("recommendation") or {}).get("recommendation"),
            "final": (s.get("review_result") or {}).get("final_decision")}


@app.get("/api/claims")
def list_claims():
    claims = sorted(claimsdb.load().values(), key=lambda c: (c["claim_id"] != "CLM-2026-00042", c["_set"] != "golden",
                                                             c["claim_id"]))
    return {"claims": [_summary(c) for c in claims], "release": config.RELEASE,
            "langfuse": config.LANGFUSE_BASE_URL, "project_id": config.project_id()}


@app.get("/api/claims/{claim_id}")
def get_claim(claim_id: str):
    try:
        c = claimsdb.get(claim_id)
    except KeyError:
        raise HTTPException(404, "claim not found")
    s = STATE.get(claim_id, {})
    docs = [{"doc_type": t, "url": f"/documents/{claim_id}/{p.name}"} for t, p in claimsdb.document_paths(c)]
    rec = s.get("recommendation")
    return {"summary": _summary(c), "header": claimsdb.claim_header(c), "policy": claimsdb.policy_record(c),
            "documents": docs, "session_url": config.session_url(claim_id),
            "recommendation": _public(rec) if rec else None, "review": s.get("review"),
            "review_result": _public(s.get("review_result")) if s.get("review_result") else None,
            "answers": s.get("answers", [])}


def _public(res: dict) -> dict:
    st = res.get("state") or {}
    return {k: res.get(k) for k in ("recommendation", "final_decision", "trace_url", "apm_url", "guard", "calculation",
                                    "prompt_versions", "cost_usd")} | {
        "rules": st.get("rules") or [], "note": (st.get("recommendation") or {}).get("examiner_note"),
        "next_actions": (st.get("recommendation") or {}).get("next_actions") or [],
        "approvals": (st.get("recommendation") or {}).get("approvals_required") or [],
        "claim_file": st.get("claim_file"), "letter": st.get("letter"),
        "citation_validity": st.get("citation_validity"), "retrieval_coverage": st.get("retrieval_coverage"),
        "retrieved": st.get("retrieved") or []}


@app.post("/api/claims/{claim_id}/process")
async def process(claim_id: str, body: ProcessIn):
    lock = LOCKS.setdefault(claim_id, asyncio.Lock())
    async with lock:
        res = await graph.run_claim(claim_id, mode="workbench", labels={"claims-sop-rules": body.sop_label},
                                    release=body.release or None, tags=["portal"])
        STATE[claim_id] = {"thread_id": res["thread_id"], "recommendation": res, "labels": {
            "claims-sop-rules": body.sop_label}, "answers": []}
        config.flush()
        return _public(res)


@app.post("/api/claims/{claim_id}/review")
async def review(claim_id: str, body: ReviewIn):
    s = STATE.get(claim_id)
    if not s or not s.get("recommendation"):
        raise HTTPException(409, "process the claim first")
    if body.action == "override" and not body.decision:
        raise HTTPException(422, "an override needs a decision")
    lock = LOCKS.setdefault(claim_id, asyncio.Lock())
    async with lock:  # one resume per paused graph: a double click must not resume (and score) twice
        if s.get("review") or s.get("reviewing"):
            raise HTTPException(409, "already reviewed — process the claim again to start over")
        s["reviewing"] = True
        try:
            rv = body.model_dump()
            res = await graph.resume_claim(claim_id, s["thread_id"], rv, labels=s.get("labels"))
            s["review"], s["review_result"] = rv, res
        finally:
            s["reviewing"] = False
    config.flush()
    return _public(res)


@app.post("/api/claims/{claim_id}/ask")
async def ask(claim_id: str, body: AskIn):
    s = STATE.setdefault(claim_id, {"answers": []})
    res = await graph.ask_assistant(claim_id, s.get("thread_id"), body.question, examiner_id=body.examiner_id,
                                    labels=s.get("labels"))
    s.setdefault("answers", []).append({"question": body.question, **res})
    config.flush()
    return res


@app.get("/api/sops/{sop_id}")
def sop(sop_id: str):
    d = sops.get(sop_id)
    if not d:
        raise HTTPException(404, "no such SOP")
    return d


@app.get("/api/presenter/{claim_id}")
def presenter(claim_id: str):
    c = claimsdb.get(claim_id)
    return {"expected": c["expected"], "scenario": c["scenario"], "candidate_sops": c["candidate_sops"],
            "critical": [{"rule_id": r, "title": (oracle.rule_by_id(r) or {}).get("title")}
                         for r in c["expected"]["critical_rule_ids"]]}


@app.get("/documents/{claim_id}/{name}")
def document(claim_id: str, name: str):
    root = claimsdb.DOCS_DIR.resolve()
    p = (root / claim_id / name).resolve()
    if not p.is_relative_to(root) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
