"""Gates evaluate immutable prompt versions and never pass unavailable/non-finite evidence."""

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import prompt_gate
import run_experiment
from handoff import config, prompts
from handoff.agents import service
from handoff.agents import base as agent_base
from handoff.a2a_service import AgentRequest
from handoff.linkage import TurnContext
from handoff.transport import A2ATransport
from handoff.tools.crm import RunState


def test_dispatch_version_survives_a_moved_label_and_snapshots_production_fallback(monkeypatch):
    calls = []

    def api(method, path, *, params):
        calls.append((path, params["label"]))
        agent = path.rsplit("/", 1)[1]
        if agent == "concierge":
            pytest.fail("an explicit dispatch version must not resolve the mutable target label")
        if agent == "billing" and params["label"] == "candidate":
            raise prompt_gate.LangfuseAPIError(method, path, 404, "missing")
        return {"version": 7 if agent == "tech-support" else 2}

    monkeypatch.setattr(prompt_gate, "api", api)
    assert prompt_gate.snapshot_versions("concierge", "candidate", 3) == {
        "concierge": 3, "tech-support": 7, "billing": 2}
    assert calls[-1] == ("/api/public/v2/prompts/billing", "production")


def test_missing_candidate_of_the_prompt_under_review_fails_closed(monkeypatch):
    def missing(*args, **kwargs):
        raise prompt_gate.LangfuseAPIError("GET", "/prompt", 404, "missing")

    monkeypatch.setattr(prompt_gate, "api", missing)
    with pytest.raises(prompt_gate.LangfuseAPIError):
        prompt_gate.snapshot_versions("concierge", "candidate")


def test_pinned_prompt_is_fetched_by_version_without_label_or_code_fallback(monkeypatch):
    calls = []

    def fetch(name, **kwargs):
        calls.append((name, kwargs))
        return SimpleNamespace(prompt="version three", version=3, is_fallback=False)

    monkeypatch.setattr(config, "get_langfuse", lambda service: SimpleNamespace(get_prompt=fetch))
    template, client = prompts.get("concierge", "candidate", version=3)
    assert template == "version three" and client.version == 3
    assert calls == [("concierge", {"version": 3, "type": "text", "cache_ttl_seconds": 60})]

    def unavailable(*args, **kwargs):
        raise RuntimeError("version cannot be fetched")

    monkeypatch.setattr(config, "get_langfuse", lambda service: SimpleNamespace(get_prompt=unavailable))
    with pytest.raises(RuntimeError, match="cannot be fetched"):
        prompts.get("concierge", "candidate", version=3)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), float("-inf")])
def test_non_finite_or_missing_gate_metric_cannot_pass(value):
    assert prompt_gate.status({"min": 0.9}, value) == "MISSING"
    assert prompt_gate.status({"max": 0.1}, value) == "MISSING"


@pytest.mark.parametrize("baseline", [None, float("nan"), float("inf")])
def test_missing_or_non_finite_comparison_baseline_blocks_the_gate(monkeypatch, tmp_path, baseline):
    import json

    threshold_file = tmp_path / "thresholds.json"
    threshold_file.write_text(json.dumps({"hard": {}, "soft": {}, "compare": {"routing-accuracy": 0.1}}))
    candidate = SimpleNamespace(run_evaluations=[SimpleNamespace(name="routing-accuracy", value=1.0)],
                                item_results=[SimpleNamespace(output={})], items_requested=1, experiment_url="")
    base_evals = [] if baseline is None else [SimpleNamespace(name="routing-accuracy", value=baseline)]
    base = SimpleNamespace(run_evaluations=base_evals, item_results=[SimpleNamespace(output={})])
    runs = iter([candidate, base])
    monkeypatch.setattr(prompt_gate.run_experiment, "run", lambda *args, **kwargs: next(runs))
    monkeypatch.setattr(prompt_gate, "snapshot_versions", lambda *args: {"concierge": 3})
    monkeypatch.setattr(prompt_gate.prompts, "version_tag", lambda *args, **kwargs: "concierge v3")
    monkeypatch.setattr(sys, "argv", ["prompt_gate.py", "--compare", "production", "--thresholds", str(threshold_file)])
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert prompt_gate.main() == 1


@pytest.mark.asyncio
async def test_experiment_task_gives_the_same_snapshot_to_each_orchestrator(monkeypatch):
    seen = []
    versions = {"concierge": 3, "tech-support": 1, "billing": 2}

    class Orchestrator:
        def __init__(self, **kwargs):
            seen.append(kwargs["prompt_versions"])

        async def turn(self, conv, message):
            return SimpleNamespace(reply="done", replied_by="billing", agent_path=["concierge", "billing"],
                                   first_handoff="billing", handoffs=[], tools=[], effects={},
                                   owner_after="concierge")

        async def aclose(self):
            pass

    monkeypatch.setattr(run_experiment, "Orchestrator", Orchestrator)
    task = run_experiment.make_task("a2a", "baggage", "candidate", versions)
    item = SimpleNamespace(input={"customer_id": "cust-1042", "message": "bill?"}, metadata={})
    await task(item=item)
    await task(item=item)
    assert seen == [versions, versions]


@pytest.mark.asyncio
async def test_a2a_transports_snapshot_as_business_metadata_and_service_preserves_it(monkeypatch):
    import handoff.transport as transport_module

    versions = {"concierge": 3, "tech-support": 1, "billing": 2}
    sent = []
    observed = []

    async def send(url, **kwargs):
        sent.append(kwargs)
        return SimpleNamespace(state="completed", text="done", data={}, task_id="task",
                               context_id="context", agent_version="1")

    monkeypatch.setattr(transport_module, "_handoff_observation", lambda **kwargs: nullcontext(object()))
    monkeypatch.setattr(transport_module, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(transport_module, "outbound", lambda *args: nullcontext(({}, {})))
    transport = A2ATransport()
    monkeypatch.setattr(transport.pool, "send", send)
    turn = TurnContext(session_id="case-one", user_id="cust-1042")
    await transport.handoff(frm="concierge", to="tech-support", kind="transfer", reason="outage",
                            message="help", payload={"customer_id": "cust-1042"}, turn=turn,
                            prompt_label="candidate", prompt_versions=versions)
    assert sent[0]["metadata"]["prompt_versions"] == versions

    async def run_agent(name, inp, **kwargs):
        observed.append(inp)
        return SimpleNamespace(effects={}, path=[], prompt_version="1", tools=[], handoffs=[],
                               transfer=None, handback=None, text="done", state="completed", history=[])

    monkeypatch.setattr(service, "run_agent", run_agent)
    req = AgentRequest(text="help", data={"customer_id": "cust-1042"}, metadata=sent[0]["metadata"],
                       task_id="task", context_id="context", continuation=False, linkage="baggage",
                       turn=turn, linked_by="baggage")
    await service.make_handler("tech-support")(req)
    assert observed[0].prompt_versions == versions


@pytest.mark.asyncio
async def test_remote_peer_delegation_uses_the_same_snapshot(monkeypatch):
    versions = {"concierge": 3, "tech-support": 1, "billing": 2}
    fetched = []
    sent = []
    replies = iter([
        [{"type": "tool_use", "id": "ask-one", "name": "ask_billing", "input": {
            "reason": "outage credit", "question": "Apply it", "line_id": "L-1042-2"}}],
        [{"type": "text", "text": "Credit applied"}],
    ])

    class Observation:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def update(self, **kwargs):
            pass

    async def create(**kwargs):
        return SimpleNamespace(content=next(replies), usage=SimpleNamespace(input_tokens=10, output_tokens=10),
                               stop_reason="end_turn")

    async def handoff(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(text="Applied", state="completed", data={})

    def get(agent, label, *, version):
        fetched.append((agent, version))
        return "support", None

    monkeypatch.setattr(config, "get_langfuse", lambda service: SimpleNamespace(
        start_as_current_observation=lambda **kwargs: Observation()))
    monkeypatch.setattr(prompts, "get", get)
    monkeypatch.setattr(agent_base, "anthropic_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    inp = agent_base.AgentInput(message="outage", kind="transfer", customer_id="cust-1042",
                                turn=TurnContext("case-peer", "cust-1042"), prompt_versions=versions)
    await agent_base.run_agent("tech-support", inp, transport=SimpleNamespace(handoff=handoff), state=RunState())
    assert fetched == [("tech-support", 1)]
    assert sent[0]["to"] == "billing" and sent[0]["prompt_versions"] == versions
