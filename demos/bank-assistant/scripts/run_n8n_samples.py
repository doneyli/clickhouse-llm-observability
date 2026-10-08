#!/usr/bin/env python
"""OBS-02 — send sample complaints through the n8n workflow and show the Langfuse traces.

Plays the role of the bank's channel gateway (web, app, e-mail, branch): it starts
a W3C trace for each complaint and passes it to n8n in the `traceparent` header,
the way any OpenTelemetry-instrumented caller would. n8n's built-in OTel spans
and the workflow's own LLM generations then share that trace id in Langfuse.

    ./.venv/bin/python scripts/run_n8n_samples.py            # 6 samples + verify in Langfuse
    ./.venv/bin/python scripts/run_n8n_samples.py --only 5   # just the card-number sample
    ./.venv/bin/python scripts/run_n8n_samples.py --no-verify

Run scripts/setup_n8n.sh first.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402

WEBHOOK = "http://localhost:5678/webhook/complaint-triage"

SAMPLES = [
    {"customer_id": "cust-1001", "channel": "web",
     "text": "There are two card payments of 249.99 USD at an electronics store in Lisbon that I never "
             "made. My card is still in my wallet. I want this stopped and the money back."},
    {"customer_id": "cust-2044", "channel": "email", "session_id": "case-2044-overdraft",
     "text": "You charged me a 35 USD overdraft fee three times this month even though my salary arrived "
             "the same morning. This feels like a cash grab."},
    {"customer_id": "cust-2044", "channel": "email", "session_id": "case-2044-overdraft",
     "text": "Following up on my overdraft complaint: nobody has answered in a week. If this is not "
             "fixed I will take it to the financial ombudsman."},
    {"customer_id": "cust-3150", "channel": "app",
     "text": "My international transfer of 3,200 USD to my daughter's university was sent 6 days ago and "
             "still has not arrived. The app just says 'processing'. Tuition is due Friday."},
    {"customer_id": "cust-4207", "channel": "branch",
     "text": "Hi, this is Maria Gonzalez. My card 4111 1111 1111 1111 was charged twice on 2026-10-01 for "
             "the same hotel booking (1,249.00 USD each). Please refund to IBAN GB29 NWBK 6016 1331 9268 19, "
             "or call me on +1 (415) 555-0134 / maria.g@example.com. Account 12345678."},
    {"customer_id": "cust-5318", "channel": "app",
     "text": "The new mobile app logs me out every two minutes and the chat assistant keeps answering "
             "a different question. Very frustrating, but nothing urgent."},
]


def send(sample: dict) -> dict:
    trace_id, span_id = secrets.token_hex(16), secrets.token_hex(8)
    headers = {
        "traceparent": f"00-{trace_id}-{span_id}-01",       # W3C trace context
        "x-request-start": f"t={int(time.time() * 1000)}",   # root observation start
    }
    t0 = time.time()
    r = httpx.post(WEBHOOK, json=sample, headers=headers, timeout=120)
    r.raise_for_status()
    out = r.json()
    out["_latency_s"] = round(time.time() - t0, 1)
    out["_sent_trace_id"] = trace_id
    return out


def verify(trace_ids: list[str], timeout_s: int = 90) -> None:
    """Wait until each trace has n8n's spans AND the generations; print what landed."""
    print("\nVerifying in Langfuse (n8n exports its spans in ~5 s batches) ...")
    pending, deadline = set(trace_ids), time.time() + timeout_s
    seen: dict[str, dict] = {}
    while pending and time.time() < deadline:
        for tid in sorted(pending):
            obs = config.api("GET", "/api/public/v2/observations", params={
                "traceId": tid, "limit": 100, "fields": "core,basic,usage,model"})["data"]
            gens = [o for o in obs if o["type"] == "GENERATION"]
            native = [o for o in obs if o["name"] in ("workflow.execute", "node.execute")]
            seen[tid] = {"observations": len(obs), "generations": len(gens), "n8n_spans": len(native),
                         "cost": sum(o.get("totalCost") or 0 for o in gens),
                         "tokens": sum((o.get("usageDetails") or {}).get("total", 0) for o in gens)}
            # workflow.execute ends last (after the reporting node), so it marks completeness
            if len(gens) >= 2 and any(o["name"] == "workflow.execute" for o in native):
                pending.discard(tid)
        if pending:
            time.sleep(5)
    for tid in trace_ids:
        s = seen.get(tid, {})
        ok = "✓" if tid not in pending else "…"
        print(f" {ok} {tid}  obs={s.get('observations', 0):>2}  n8n spans={s.get('n8n_spans', 0):>2}  "
              f"generations={s.get('generations', 0)}  tokens={s.get('tokens', 0):>4}  "
              f"cost=${s.get('cost', 0):.5f}")
    if pending:
        print(" (… = not fully ingested yet; re-check the URLs in a minute)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", type=int, nargs="*", help="1-based sample numbers to send")
    ap.add_argument("--no-verify", action="store_true", help="skip the Langfuse read-back")
    args = ap.parse_args()

    picked = [(i, s) for i, s in enumerate(SAMPLES, 1) if not args.only or i in args.only]
    trace_ids = []
    for i, sample in picked:
        print(f"\n[{i}] {sample['channel']:<6} {sample['customer_id']}: {sample['text'][:90]}...")
        try:
            out = send(sample)
        except httpx.HTTPError as e:
            print(f"    ✗ webhook call failed: {e} — is the workflow published? run scripts/setup_n8n.sh")
            return 1
        masked = {k: v for k, v in (out.get("pii_masked") or {}).items() if v}
        print(f"    → {out['category']} / {out['severity']}"
              f"{' / REGULATORY ESCALATION' if out['regulatory_escalation'] else ''}"
              f"  ({out['_latency_s']} s, n8n execution {out['n8n_execution_id']})")
        if masked:
            print(f"    masked before the LLM: {masked}")
        print(f"    reply: {out['reply'][:160].replace(chr(10), ' ')}...")
        print(f"    trace: {config.trace_url(out['trace_id'])}")
        trace_ids.append(out["trace_id"])

    if not args.no_verify and trace_ids:
        verify(trace_ids)
    print(f"\nAll n8n traces: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/traces"
          f"  (filter: name = n8n-complaint-triage, or tag channel:n8n)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
