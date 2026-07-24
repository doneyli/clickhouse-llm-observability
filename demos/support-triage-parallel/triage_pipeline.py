"""
Stage 1 — Sectioning fan-out + synthesis aggregator.

Four *different* questions about the *same* ticket run concurrently
(``asyncio.gather``), each a narrow prompt on a cheap model (Haiku): summary,
sentiment/urgency, technical category, and a policy/PII guardrail. A meta-agent
(Sonnet) then synthesizes the labeled branch outputs into one triage brief.

Why this is the Sectioning sub-variant:
- Truly independent branches, isolated inputs, no cross-branch state.
- The **guardrail split** is its own branch (typed ``guardrail``) rather than
  folded into the main analysis call — LLMs handle a narrow consideration better
  than an omnibus prompt, and a policy screen shouldn't slow the answer.
- Partial-failure tolerant: a timed-out branch is dropped (``level=WARNING``) and
  the aggregator proceeds with N-1, recording ``failed_branches``.

Instrumentation: the fan-out parent is a ``span`` (``analyze-sections``); opening
the branches *inside* it and gathering them makes them auto-nest as siblings
with overlapping Timeline spans (parallel) or a staircase (``--sequential``).
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import langfuse_config as lf
from llm import anthropic_call

BRANCH_MODEL = os.getenv("BRANCH_MODEL", "claude-haiku-4-5")
SYNTH_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
SYNTH_TEMPERATURE = float(os.getenv("SYNTH_TEMPERATURE", "0.5"))
BRANCH_TIMEOUT_S = float(os.getenv("BRANCH_TIMEOUT_S", "30"))

CATEGORY_TAXONOMY = [
    "query-performance", "ingestion", "replication", "billing",
    "schema-migration", "connectivity", "other",
]

# --- Local fallback prompt templates -----------------------------------------
# These MIRROR the managed prompts in scripts/seed_prompts.py (Langfuse {{var}}
# syntax). Keep them in sync by hand — when managed and fallback match, enabling
# prompt management changes nothing until you edit the prompt in Langfuse.
SUMMARY_FALLBACK = (
    "Summarize the following ClickHouse support ticket in exactly two short, "
    "factual sentences. Do not speculate.\n\nTicket:\n{{ticket_body}}\n\nSummary:"
)
SENTIMENT_FALLBACK = (
    "Analyze the customer's tone in this support ticket. Reply with ONLY a JSON "
    'object: {"sentiment": "positive|neutral|negative", '
    '"urgency": "low|medium|high"}.\n\nTicket:\n{{ticket_body}}\n\nJSON:'
)
CATEGORY_FALLBACK = (
    "Classify this ClickHouse support ticket into exactly ONE category from this "
    "list: query-performance, ingestion, replication, billing, schema-migration, "
    "connectivity, other. Reply with ONLY the category label.\n\n"
    "Ticket:\n{{ticket_body}}\n\nCategory:"
)
POLICY_GUARD_FALLBACK = (
    "You are a policy/PII guardrail. Screen the ticket for personal data "
    "(emails, phone numbers), leaked credentials/API keys, or abusive content. "
    'Reply with ONLY JSON: {"flagged": true|false, "reasons": ["..."]}.\n\n'
    "Ticket:\n{{ticket_body}}\n\nJSON:"
)
SYNTHESIS_FALLBACK = (
    "You are a support triage lead. Merge the labeled analysis branches below "
    "into a concise triage brief (owner-ready): one-line summary, "
    "sentiment/urgency, category, and any policy flags. If a branch reads "
    "'insufficient data', you MUST say so explicitly for that dimension and do "
    "not invent it.\n\nBranch outputs (JSON):\n{{branch_outputs}}\n\nTriage brief:"
)

# (name, short_key, as_type, temperature, prompt_name, fallback)
BRANCHES = [
    ("branch-summary", "summary", "generation", 0.3, "support-triage-summary", SUMMARY_FALLBACK),
    ("branch-sentiment-urgency", "sentiment", "generation", 0.3, "support-triage-sentiment", SENTIMENT_FALLBACK),
    ("branch-category", "category", "generation", 0.0, "support-triage-category", CATEGORY_FALLBACK),
    ("branch-policy-guard", "policy", "guardrail", 0.0, "support-triage-policy-guard", POLICY_GUARD_FALLBACK),
]


def _fault() -> str:
    """Deterministic fault injection (repo convention). FAULT=slow-branch makes
    branch-sentiment-urgency exceed the timeout so it is dropped."""
    return os.getenv("FAULT", "").strip().lower()


def _parse_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from a model reply (tolerates prose/fences)."""
    if not text:
        return None
    s = text.strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return None


async def run_branch(name: str, short_key: str, as_type: str, temperature: float,
                     prompt_name: str, fallback: str, ticket: dict,
                     fault: str = "") -> dict:
    """Run one sectioning branch as a typed observation. On timeout/error the
    branch is dropped (``level=WARNING``) and the aggregator proceeds with N-1."""
    started = time.perf_counter()
    with lf.observe(name, as_type=as_type, input=ticket["body"],
                    metadata={"branch": short_key}) as obs:
        text, prompt_obj = lf.render_prompt(prompt_name, fallback, ticket_body=ticket["body"])
        if prompt_obj is not None:
            obs.update(prompt=prompt_obj)  # link managed prompt version to the generation
        try:
            # Fault injection: force the sentiment branch to blow past the timeout.
            if fault == "slow-branch" and short_key == "sentiment":
                await asyncio.sleep(BRANCH_TIMEOUT_S + 1)
            out = await asyncio.wait_for(
                anthropic_call(model=BRANCH_MODEL, prompt=text,
                               temperature=temperature, max_tokens=400, obs=obs),
                timeout=BRANCH_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, Exception) as e:  # noqa: B014 - deliberate broad catch
            kind = "timeout" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
            obs.update(level="WARNING", status_message=f"branch dropped: {kind}: {e}")
            return {"branch": name, "key": short_key, "ok": False, "output": None,
                    "elapsed": time.perf_counter() - started}

        # The guardrail branch scores its OWN span (span-level, because a guard
        # can fire on any ticket and repeat across a trace).
        if short_key == "policy":
            parsed = _parse_json(out) or {}
            flagged = bool(parsed.get("flagged"))
            lf.score_current_span(
                "policy_flagged", 1.0 if flagged else 0.0, data_type="BOOLEAN",
                comment="; ".join(parsed.get("reasons", []))[:300] or "no policy issues",
            )
        obs.update(output=out)
        return {"branch": name, "key": short_key, "ok": True, "output": out,
                "elapsed": time.perf_counter() - started}


def build_synthesis_input(results: List[dict]) -> Tuple[Dict[str, Any], int, bool]:
    """Pure aggregator input builder — no I/O, unit-testable offline.

    Maps branch results to ``{short_key: output}``, substituting
    ``"insufficient data"`` for any missing/failed branch. Returns
    ``(branch_outputs, failed_count, degraded)``.
    """
    outputs: Dict[str, Any] = {}
    failed = 0
    for r in results:
        key = r.get("key") or r.get("branch", "").removeprefix("branch-")
        if r.get("ok") and r.get("output") is not None:
            outputs[key] = r["output"]
        else:
            outputs[key] = "insufficient data"
            failed += 1
    return outputs, failed, failed > 0


async def synthesize(branch_outputs: Dict[str, Any], degraded: bool) -> str:
    """Meta-agent synthesis (Sonnet) over labeled branch outputs -> triage brief."""
    text, prompt_obj = lf.render_prompt(
        "support-triage-synthesis", SYNTHESIS_FALLBACK,
        branch_outputs=json.dumps(branch_outputs, indent=2),
    )
    gen_kwargs = {"model": SYNTH_MODEL}
    if prompt_obj is not None:
        gen_kwargs["prompt"] = prompt_obj
    with lf.observe("synthesis-llm", as_type="generation", **gen_kwargs) as gen:
        gen.update(input={"branch_outputs": branch_outputs, "degraded": degraded})
        brief = await anthropic_call(model=SYNTH_MODEL, prompt=text,
                                     temperature=SYNTH_TEMPERATURE, max_tokens=600, obs=gen)
    return brief


async def analyze_sections(ticket: dict, sequential: bool = False,
                           fault: str = "") -> dict:
    """Sectioning fan-out + synthesis. Returns
    ``{brief, branch_outputs, failed_branches, degraded}``."""
    fault = fault or _fault()
    with lf.observe("analyze-sections",
                    metadata={"branch_count": len(BRANCHES),
                              "mode": "sequential" if sequential else "parallel"}) as parent:
        coros = [run_branch(*b, ticket, fault=fault) for b in BRANCHES]
        wall_start = time.perf_counter()
        if sequential:
            results = [await c for c in coros]        # baseline for the latency A/B
        else:
            results = await asyncio.gather(*coros)     # concurrent siblings
        wall = time.perf_counter() - wall_start
        branch_sum = sum(r.get("elapsed", 0.0) for r in results)

        branch_outputs, failed, degraded = build_synthesis_input(results)
        parent.update(metadata={"branch_count": len(BRANCHES),
                                "mode": "sequential" if sequential else "parallel",
                                "failed_branches": failed})

        with lf.observe("synthesize-triage-brief",
                        input={"branch_outputs": branch_outputs},
                        metadata={"failed_branches": failed, "degraded": degraded}) as agg:
            brief = await synthesize(branch_outputs, degraded)
            agg.update(output=brief)

    return {"brief": brief, "branch_outputs": branch_outputs,
            "failed_branches": failed, "degraded": degraded,
            "wall_s": wall, "sum_s": branch_sum}
