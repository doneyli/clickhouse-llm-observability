"""Publishing needs a completed fact-check, including under the parallel-tools fix."""

import threading
from types import SimpleNamespace

import pytest

from agent.loop import _run_tools, _traced_tool
from agent.review import policy_check, review
from agent.tools import RunState, ToolContext, run_tool
from agent.world import CUSTOMERS


class Observation:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, **kwargs):
        pass


class Langfuse:
    def start_as_current_observation(self, **kwargs):
        return Observation()


def context(verifier):
    return ToolContext(customer=CUSTOMERS["tailspin"], fixes=set(), state=RunState(),
                       claim_verifier=verifier, simulate_latency=False)


def supported(claims, evidence):
    return [{"claim": c, "verdict": "supported", "evidence": "synthetic evidence"} for c in claims]


def publish(ctx):
    return run_tool(ctx, "stage_cms_draft", {"page_url": "https://tailspin.example/faq",
                                           "title": "FAQ", "body_markdown": "Flex fares are refundable."})


@pytest.mark.parametrize("arguments", [{}, {"claims": []}, {"claims": ["Flex fares are refundable."]}])
def test_failed_or_incomplete_check_does_not_authorize_publish(arguments):
    ctx = context(lambda claims, evidence: [])
    result = run_tool(ctx, "check_claims", arguments)
    assert result.is_error
    publish(ctx)
    assert not policy_check(ctx.state, False)["passed"]
    assert policy_check(ctx.state, False)["fact_checks_run"] == 0
    assert review("tailspin", ctx.state, "Draft staged.", step_limit_hit=False, duration_s=1)[0] == "rejected"


def test_verifier_exception_does_not_authorize_publish():
    def fail(claims, evidence):
        raise RuntimeError("fact-check backend unavailable")

    ctx = context(fail)
    use = SimpleNamespace(id="check", name="check_claims", input={"claims": ["Flex fares are refundable."]})
    assert _traced_tool(Langfuse(), ctx, use).is_error
    publish(ctx)
    assert not policy_check(ctx.state, False)["passed"]


def test_completed_check_before_publish_passes():
    ctx = context(supported)
    assert not run_tool(ctx, "check_claims", {"claims": ["Flex fares are refundable."]}).is_error
    publish(ctx)
    assert policy_check(ctx.state, False)["passed"]
    assert policy_check(ctx.state, False)["fact_checks_run"] == 1


def test_parallel_publish_while_check_pending_is_rejected(monkeypatch):
    from agent import tools

    checking, published = threading.Event(), threading.Event()

    def verify(claims, evidence):
        checking.set()
        assert published.wait(timeout=5), "publisher did not run concurrently"
        return supported(claims, evidence)

    original_publish = tools._IMPLS["stage_cms_draft"]

    def stage(ctx, **kwargs):
        assert checking.wait(timeout=5), "check did not start"
        result = original_publish(ctx, **kwargs)
        published.set()
        return result

    monkeypatch.setitem(tools._IMPLS, "stage_cms_draft", stage)
    ctx = context(verify)
    ctx.fixes.add("parallel-tools")
    uses = [SimpleNamespace(id="check", name="check_claims", input={"claims": ["Flex fares are refundable."]}),
            SimpleNamespace(id="draft", name="stage_cms_draft", input={
                "page_url": "https://tailspin.example/faq", "title": "FAQ", "body_markdown": "Flex fares are refundable."})]
    assert all(not result.is_error for result in _run_tools(Langfuse(), ctx, uses))
    assert len(ctx.state.checked_claims) == len(ctx.state.staged_drafts) == 1
    assert not policy_check(ctx.state, False)["passed"]
    assert review("tailspin", ctx.state, "Draft staged.", step_limit_hit=False, duration_s=1)[0] == "rejected"
