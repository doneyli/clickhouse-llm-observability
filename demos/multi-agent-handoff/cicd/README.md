# CI/CD: the prompt gate

## The gate

`scripts/prompt_gate.py` runs `handoff-routing` on the **a2a** topology, with every agent on the
label under review, and compares the run-level scores with [`thresholds.json`](thresholds.json):

- **hard** (fail the build): `routing-accuracy`, `loop-rate`, `resolution-rate`, `credit-accuracy`,
  `outage-verified-rate`, `context-complete-rate`, and `items-evaluated` (every item ran: a crashed
  task is dropped from the SDK's averages, so a run that lost items must not pass);
- **soft** (warn): `path-match-rate`, `mean-handoffs`, `p95-latency-s`;
- **compare** (with `--compare production`): how far `routing-accuracy` and `resolution-rate` may
  fall below the production label, measured in the same gate run.

All hard metrics are deterministic: routing and trajectory come from the run record, and outcomes
come from the back-office ledger, checked against the credit oracle. No LLM judge decides a release.

A label that does not exist on the gated prompt aborts the gate. Otherwise that agent would quietly
run its production prompt, and the gate would grade production. Any other agent that has no version
under the label does run its production prompt; that is how one label changes one agent.

The dataset grows as the loop runs. Handoffs a reviewer labelled in production are promoted into
`handoff-routing` (`scripts/promote_to_dataset.py`), so every bug the loop fixes stays tested.

Why `routing-accuracy` and not only `resolution-rate`: under bug F1 the concierge sends outage
credits straight to billing. Billing passes the request on to tech-support, and the customer gets
the right credit, so the outcome metrics stay at 1.0. Only the routing check sees the hop it
skipped.

## Measured

See [`../SPEC.md` §11](../SPEC.md) for the latest measured runs: production passes, `fault-f1` is
blocked on `routing-accuracy`, and the F1 fix (`candidate`) passes.

## Wiring

```
Langfuse: save a concierge / tech-support / billing version, or move a label
  └─► Prompts → Automations → GitHub Repository Dispatch (event handoff-prompt-update)
        └─► .github/workflows/handoff-prompt-gate.yml
              1. Decide: prompt name, version and labels from the payload; skip other prompts and unlabelled versions
              2. Install, write the demo .env from secrets, start billing + tech-support (./scripts/run_services.sh)
              3. Gate the label (scripts/prompt_gate.py), unique run name per CI run → job summary + run link
              4. "Ship it" only if the gate passed AND the version carries `production`
```

`scripts/loop.py` moves the labels in the demo (`ship-bug`, `fix`, `promote`), and each move is a
prompt update event, so with the automation in place every step of the loop runs the gate.

## Setup (once)

1. Repo secrets: `HANDOFF_LANGFUSE_BASE_URL`, `HANDOFF_LANGFUSE_PUBLIC_KEY`,
   `HANDOFF_LANGFUSE_SECRET_KEY` (the `multi-agent-handoff` project's keys), and `ANTHROPIC_API_KEY`.
2. In the Langfuse project, go to Prompts → Automations → Create:
   - events *Created* and *Updated*;
   - filter on `concierge`, `tech-support` and `billing` (or leave the filter empty: the workflow
     skips any other prompt);
   - action GitHub Repository Dispatch, with URL `https://api.github.com/repos/<owner>/<repo>/dispatches`,
     event type `handoff-prompt-update`, and a token with Actions and Contents read/write.

   The automation has no public API, so this is a UI step.
3. GitHub only runs a `repository_dispatch` or `workflow_dispatch` workflow from the default branch,
   so the workflow fires once it is merged.

Cost: one gate run is 40+ items at about $0.02 each, and takes about 3 minutes on a GitHub runner.
