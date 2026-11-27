# Langfuse Enterprise Operations — Spec

**Status:** built · **Written:** 2026-11-27, after the build, as a short index to the design record

This demo was built before the demo standard. Its design lives in two files that predate
this one, and this spec does not repeat them:

- [`design.md`](design.md): the portal's visual and interaction rules (layout, colours,
  what a button may do).
- [`docs/architecture.md`](docs/architecture.md): components, credential loaders, the
  supported-contract table, the resource lifecycle, reset rules and the verification
  contract.
- [`docs/future-addons.md`](docs/future-addons.md): Enterprise capabilities not built yet.

## 1. The question it answers

Can a platform team run Langfuse for many application teams (projects, people, keys,
prompt governance, audit and cleanup) as scripted, reviewable operations rather than
clicks, and which of those operations need the Enterprise edition?

## 2. Audience and setting

Platform, security and procurement stakeholders evaluating self-hosted Langfuse
Enterprise. 20 to 30 minutes, run live against a local licensed instance. Pick it over the
tracing and evaluation demos when the conversation is about administration, not app
quality.

## 3. What the demo must prove

| # | Claim | How it is shown | Acceptance |
|---|---|---|---|
| 1 | Projects, scoped keys and starter assets come from one form | Application workflow | Project, key, prompt and dataset read back in the same run |
| 2 | Project roles are granted, changed and removed by API | Team, Permissions, Offboarding | Membership read-back matches the request |
| 3 | A key is rotated without losing service | Keys workflow | Replacement verified before switching; old key gets HTTP 401 |
| 4 | Protected labels stop a Member, not an Admin | Protected labels workflow | Member gets HTTP 403 on `production`; production unchanged; Admin promotes |
| 5 | Role changes land in the native audit log | Audit evidence workflow | A native record matches user, before/after role and run time |
| 6 | Organizations can be provisioned from the instance | Organizations workflow | Org, org key and starter project read back; a rerun does not duplicate |
| 7 | Cleanup is reviewed and bounded | Reset workflow | Preview fingerprint and `RESET DEMO` required; unrecorded objects block it |

`scripts/verify_demo.py` checks the prerequisites for all seven and the seeded governance
project for claims 4 and 5. The claims themselves are proved by each workflow's own
read-backs, live.

## 4. The fictional company and data

No company. Application, team and organization names are typed by the presenter. People
are synthetic `example.invalid` identities. Prompts, datasets and the one dataset item are
synthetic text written by `workflows.py`.

## 5. Architecture

See [`docs/architecture.md`](docs/architecture.md#components). In short: browser → loopback
`server.py` (:8091) → named `scripts/*.sh` → `workflows.py` → Langfuse CLI wrapper or
`scripts/platform-api.mjs` → local Langfuse (:3001).

## 6. What lands in Langfuse

No traces. The demo writes administration objects; the README's
[What runs where](README.md#what-runs-where) lists them with their names and markers.

## 7. Act plan

The acts follow the portal's workflows in the order of the original presenter flow. See
[`DEMO_SCRIPT.md`](DEMO_SCRIPT.md#what-each-act-proves).

## 8. Measurements

None quoted. The act minutes in `DEMO_SCRIPT.md` are estimates until a rehearsal measures
them.

## 9. Risks and open questions

- The native UI RPC adapter is pinned to Langfuse 4.76.0; any upgrade needs the contract
  re-validated (`docs/architecture.md`).
- Signup policies, email verification, SSO enforcement or CAPTCHA block persona
  preparation; the portal does not bypass them.

## 10. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Credentials in Python | Never read; paths go to the CLI or Node loaders | Keeps secrets out of process output and agent context |
| D2 | What the seed prepares | `private/` and the Protected labels workflow only | Everything else is the live demo; governance is slow and policy-sensitive |
| D3 | `langfuse.project` in `demo.yaml` | Kept as `Operations Demo Organization`, the organization Act 6 provisions | The demo acts on organizations, not a fixed project. The org key's own organization is whichever the presenter uses; the declared one is owned by the demo, so verify reads its name and `metadata.demo` back once the manifest records it |
| D5 | Seed idempotence | Read governance back live; run the workflow only on drift (or `--reprove`) | An unchanged seed writes nothing; the workflow replaces a prompt version whose definition drifted |
| D4 | Existing file names | Kept (`docs/architecture.md`, kebab-case scripts) | Standard decision D8: no renames |
