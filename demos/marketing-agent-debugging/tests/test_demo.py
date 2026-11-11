"""
Offline tests for the planted faults, the fixes, the reviewer and the trace shape.
No network, no model calls:

    cd demos/marketing-agent-debugging && .venv/bin/python -m pytest -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import instructions  # noqa: E402
from agent.review import policy_check, review  # noqa: E402
from agent.tools import (TOOL_DEFINITIONS, RunState, ToolContext, fetch_page, run_tool,  # noqa: E402
                         search_answer_engine, search_context)
from agent.world import CUSTOMERS  # noqa: E402

PRICING_QUERIES = ["Contoso pricing", "how much does Contoso cost", "Contoso pricing per user",
                   "pricing plans per user cost", "Contoso Team plan cost"]


def ctx(customer, *fixes):
    return ToolContext(customer=CUSTOMERS[customer], fixes=set(fixes), state=RunState(),
                       claim_verifier=lambda claims, ev: [{"claim": c, "verdict": "supported", "evidence": ""}
                                                          for c in claims],
                       simulate_latency=False)


# ----------------------------------------------------------------- Case 1: stale context
@pytest.mark.parametrize("query", PRICING_QUERIES)
def test_archived_faq_wins_pricing_queries(query):
    _, records = search_context(ctx("contoso"), query)
    assert records[0]["status"] == "archived"
    assert records[0]["doc_id"].startswith("ctx-contoso-pricing-faq")


@pytest.mark.parametrize("query", ["Contoso pricing", "Contoso pricing per user", "pricing plans per user cost"])
def test_current_plans_doc_is_crowded_out(query):
    _, records = search_context(ctx("contoso"), query)
    assert "ctx-contoso-plans-2025" not in [r["doc_id"] for r in records]


@pytest.mark.parametrize("query", ["Contoso pricing", "Contoso pricing per user", "Contoso Business plan price"])
def test_fix_exclude_archived_surfaces_current_doc(query):
    _, records = search_context(ctx("contoso", "exclude-archived"), query)
    assert records[0]["doc_id"] == "ctx-contoso-plans-2025"
    assert all(r["status"] == "current" for r in records)


def test_model_never_sees_dates_or_status():
    passages, records = search_context(ctx("contoso"), "Contoso pricing")
    assert set(passages[0]) == {"title", "content", "relevance"}
    assert {"status", "updated_at"} <= set(records[0])


# ----------------------------------------------------------------- Case 2: latency
def test_fabrikam_raw_html_page_is_bloated_and_fix_cleans_it():
    url = "https://fabrikam.example/guides/running-shoes-for-flat-feet"
    raw = fetch_page(ctx("fabrikam"), url)
    clean = fetch_page(ctx("fabrikam", "clean-html"), url)
    assert raw.result["content"].startswith("<!DOCTYPE html>") and len(raw.result["content"]) > 50_000
    assert len(clean.result["content"]) < 1_000
    assert raw.metadata["cms_cache"] == "MISS"


def test_fabrikam_compare_page_404s_as_an_error_node():
    out = fetch_page(ctx("fabrikam"), "https://fabrikam.example/compare/stability-shoes")
    assert out.level == "ERROR" and out.is_error and out.result["status_code"] == 404


# ----------------------------------------------------------------- Case 3: loop
def test_copilot_capture_climbs_then_sticks_at_99():
    c = ctx("tailspin")
    seen = [search_answer_engine(c, "last minute flight deals", "copilot").result["progress_percent"]
            for _ in range(8)]
    assert seen == sorted(seen) and seen[-1] == 99 and seen[-2] == 99
    assert c.state.polls["copilot|last minute flight deals"] == 8


def test_terminal_pending_fix_says_do_not_retry():
    out = search_answer_engine(ctx("tailspin", "terminal-pending"), "last minute flight deals", "copilot")
    assert out.result["status"] == "unavailable" and "Do not retry" in out.result["message"]


def test_other_engines_answer_normally():
    out = search_answer_engine(ctx("tailspin"), "last minute flight deals", "perplexity")
    assert out.result["citations"] and out.level is None


# ----------------------------------------------------------------- Case 4: instructions
def test_tailspin_v3_dropped_the_fact_check_rule():
    texts = [v[0] for v in instructions.VERSIONS["tailspin"]]
    assert "check_claims on every" in texts[1]
    assert "check_claims on every" not in texts[2]
    assert "as few tool calls as" in texts[2]
    assert instructions.fallback_text("tailspin") == texts[2]


def test_only_one_production_label_per_prompt():
    for history in instructions.VERSIONS.values():
        assert sum("production" in labels for _, labels, _ in history) == 1


# ----------------------------------------------------------------- reviewer + guardrail
def _state(*calls, **kw):
    s = RunState(calls=[{"name": n, "input": {}} for n in calls])
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_policy_flags_publish_before_fact_check():
    assert not policy_check(_state("retrieve_brand_context", "stage_cms_draft"), False)["passed"]
    assert policy_check(_state("check_claims", "stage_cms_draft"), False)["passed"]


def test_reviewer_rejects_stale_price_corrections():
    s = _state("check_claims", "queue_correction", corrections=[
        {"engine": "perplexity", "corrected_statement": "Team is $12 per user per month."}])
    assert review("contoso", s, "", step_limit_hit=False, duration_s=30)[0] == "rejected"


def test_reviewer_approves_correct_corrections():
    s = _state("check_claims", "queue_correction", corrections=[
        {"engine": "chatgpt", "corrected_statement": "Team is $15 per seat per month billed annually."}])
    assert review("contoso", s, "", step_limit_hit=False, duration_s=30)[0] == "approved"


def test_reviewer_rejects_stalled_polling():
    s = _state(*["search_answer_engine"] * 5, polls={"copilot|x": 5})
    decision, comment = review("tailspin", s, "", step_limit_hit=False, duration_s=50)
    assert decision == "rejected" and "copilot" in comment


def test_reviewer_rejects_pricing_check_that_queues_nothing():
    s = _state("retrieve_brand_context", "search_answer_engine", "check_claims")
    assert review("contoso", s, "Could you confirm which pricing is current?", step_limit_hit=False,
                  duration_s=40, task_kind="pricing-check")[0] == "rejected"
    # same behaviour on another task is not a pricing failure
    assert review("contoso", s, "Please confirm the angle.", step_limit_hit=False,
                  duration_s=40, task_kind="content-brief")[0] == "approved"


@pytest.mark.parametrize("text,claim", [
    ("Guide rails that correct overpronation on every stride.", True),
    ("The Stride Stability 4 prevents injuries.", True),
    ("Find the correct size with our fit guide.", False),
    ("Shoes don't fix flat feet, but support helps.", False),
    ("It will never cure plantar fasciitis.", False),
])
def test_medical_claim_detection(text, claim):
    from agent.review import medical_claim
    assert bool(medical_claim(text)) is claim


def test_reviewer_flags_slow_fabrikam_refresh_but_approves():
    s = _state("check_claims", "stage_cms_draft", staged_drafts=[{"body_markdown": "Guide rails help."}])
    decision, comment = review("fabrikam", s, "", step_limit_hit=False, duration_s=75)
    assert decision == "approved" and "too slow" in comment


# ----------------------------------------------------------------- harness
class _Obs:
    id = "obs"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, **kw):
        self.kw = kw


class _StubLangfuse:
    def start_as_current_observation(self, **kw):
        return _Obs()


@pytest.mark.parametrize("fixes", [(), ("parallel-tools",)])
def test_run_tools_keeps_order_sequential_and_parallel(fixes):
    from agent.loop import _run_tools
    c = ctx("fabrikam", *fixes)
    urls = ["https://fabrikam.example/shoes/stride-stability-4",
            "https://fabrikam.example/compare/stability-shoes",
            "https://fabrikam.example/guides/shoe-fit"]
    uses = [SimpleNamespace(id=f"t{i}", name="fetch_page", input={"url": u}) for i, u in enumerate(urls)]
    outcomes = _run_tools(_StubLangfuse(), c, uses)
    assert [o.result.get("url") for o in outcomes] == urls
    assert [o.is_error for o in outcomes] == [False, True, False]
    assert len(c.state.calls) == 3


# ----------------------------------------------------------------- trace shape
def test_tool_call_message_uses_openai_shape_with_thinking():
    from agent.loop import _assistant_trace_message
    blocks = [SimpleNamespace(type="thinking", thinking="Need pricing facts first."),
              SimpleNamespace(type="text", text=""),
              SimpleNamespace(type="tool_use", id="toolu_1", name="retrieve_brand_context",
                              input={"query": "pricing"})]
    msg = _assistant_trace_message(blocks)
    assert msg["tool_calls"][0]["function"]["name"] == "retrieve_brand_context"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"query": "pricing"}
    assert msg["thinking"] == [{"type": "thinking", "content": "Need pricing facts first."}]


def test_every_tool_has_an_implementation():
    c = ctx("contoso")
    for t in TOOL_DEFINITIONS:
        assert "unknown tool" not in json.dumps(run_tool(c, t["name"], {}).result)
