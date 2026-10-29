"""
Phase 0 spike (SPEC.md §14): one claim event, LangGraph-side root → A2A → Strands
doc-intake service, then READ IT BACK and assert the trace shape.

Needs the doc-intake service running:
    doc_intake/.venv/bin/python -m doc_intake.service
Then:
    .venv/bin/python scripts/spike_phase0.py

Checks (each prints PASS/FAIL; exit 1 on any FAIL):
  1. one root observation in the trace
  2. every observation carries the claim's session id
  3. the Strands spans are children in the same trace (doc-intake wrapper + Strands generation)
  4. the Strands generation is linked to the doc-intake prompt version
  5. the death certificate is attached to the doc-intake observation as media
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langfuse import propagate_attributes  # noqa: E402

from claims import a2a_client, config, documents  # noqa: E402

DEMO = Path(__file__).resolve().parent.parent
CERT = DEMO / "data" / "documents" / "spike" / "death-certificate.png"


async def run_event(claim_id: str) -> str:
    documents.render_death_certificate({
        "certificate_number": "SYN-NY-2026-004417", "decedent_name": "Harold J. Whitfield",
        "date_of_birth": "1961-02-03", "date_of_death": "2026-09-14", "place_of_death_state": "NY",
        "cause_of_death": "Acute myocardial infarction", "manner_of_death": "Natural",
        "certifier": "Dr. L. Okonjo, MD", "date_certified": "2026-09-15"}, CERT)
    lf = config.get_langfuse()
    version = f"{config.RELEASE} · spike"
    with propagate_attributes(session_id=claim_id, user_id="system", trace_name="claims-orchestrator",
                              version=version, tags=["claims", "spike", "line:individual_life", "state:NY"],
                              metadata={"claim_id": claim_id}):
        with lf.start_as_current_observation(as_type="agent", name="claims-orchestrator",
                                             input={"claim_id": claim_id, "event": "documents_received"},
                                             metadata={"role": "orchestrator"}) as root:
            trace_id = root.trace_id
            environment = config.ENVIRONMENT
            with propagate_attributes(session_id=claim_id, user_id="system", trace_name="claims-orchestrator",
                                      version=version, environment=environment, as_baggage=True), \
                    lf.start_as_current_observation(as_type="span", name="a2a-client: doc-intake",
                                                    input={"url": config.DOC_INTAKE_URL, "documents": [CERT.name]},
                                                    metadata={"protocol": "a2a", "a2a.method": "message/send"}) as s:
                res = await a2a_client.send_message(
                    f"Extract the fields from the documents of claim {claim_id}.",
                    [a2a_client.file_part(CERT, "death-certificate.png")],
                    context_id=claim_id, metadata={"claim_id": claim_id, "prompt_label": "production"})
                s.update(output={k: res[k] for k in ("task_id", "context_id", "state")})
            root.update(output={"extraction": res["text"][:2000]})
    config.flush()
    return trace_id


def read_back(trace_id: str, want_min: int = 4, timeout_s: int = 120) -> list[dict]:
    deadline = time.time() + timeout_s
    rows: list[dict] = []
    while time.time() < deadline:
        data = config.api("GET", "/api/public/v2/observations", params={
            "traceId": trace_id, "fields": "core,basic,io,metadata,prompt,trace_context", "limit": 100})
        rows = data.get("data", [])
        names = {r.get("name") for r in rows}
        if len(rows) >= want_min and "doc-intake" in names and any(r.get("type") == "GENERATION" for r in rows):
            time.sleep(8)  # let late spans land, then read once more
            return config.api("GET", "/api/public/v2/observations", params={
                "traceId": trace_id, "fields": "core,basic,io,metadata,prompt,trace_context", "limit": 100})["data"]
        time.sleep(5)
    return rows


def main() -> int:
    claim_id = f"CLM-SPIKE-{uuid.uuid4().hex[:6].upper()}"
    trace_id = asyncio.run(run_event(claim_id))
    print("trace:", config.trace_url(trace_id))
    rows = read_back(trace_id)
    print(f"{len(rows)} observations:")
    for r in sorted(rows, key=lambda r: r.get("startTime") or ""):
        print(f"  {r.get('type'):<11} {r.get('name')!s:<40} root={r.get('isRootObservation')} "
              f"session={r.get('sessionId')} prompt={r.get('promptName')}/{r.get('promptVersion')}")
    if rows:
        print("fields on an observation:", sorted(rows[0].keys()))
    roots = [r for r in rows if r.get("isRootObservation")]
    gens = [r for r in rows if r.get("type") == "GENERATION"]
    intake = [r for r in rows if r.get("name") == "doc-intake"]
    checks = {
        "1 one root observation": len(roots) == 1,
        "2 session on every observation": bool(rows) and all(r.get("sessionId") == claim_id for r in rows),
        "3 Strands spans in the same trace": bool(intake) and bool(gens),
        "4 Strands generation linked to prompt": any(g.get("promptName") == "doc-intake" for g in gens),
        "5 certificate attached as media": bool(intake) and "@@@langfuseMedia" in json.dumps(intake[0].get("input")),
    }
    if "--judge" in sys.argv and intake:
        score = wait_for_score(trace_id, "certificate-extraction")
        print("judge score:", score and {k: score.get(k) for k in ("value", "stringValue", "comment", "metadata")})
        target = ((score or {}).get("subject") or {}).get("id") or ((score or {}).get("metadata") or {}).get(
            "target_observation_id")
        checks["6 multi-modal judge scored the doc-intake observation"] = bool(score) and target == intake[0]["id"]
    for k, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + k)
    return 0 if all(checks.values()) else 1


def wait_for_score(trace_id: str, name: str, timeout_s: int = 240):
    """Managed judges run asynchronously: poll v3 scores for this trace."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        d = config.api("GET", "/api/public/v3/scores", params={
            "traceId": trace_id, "name": name, "fields": "core,subject", "limit": 10})
        rows = d.get("data") or []
        if rows:
            return rows[0]
        time.sleep(10)
    return None


if __name__ == "__main__":
    sys.exit(main())
