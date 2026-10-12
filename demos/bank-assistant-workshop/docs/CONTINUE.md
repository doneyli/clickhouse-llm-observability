# Continue banking workshop PR #93

Continue [PR #93: Add standalone bank assistant workshop](https://github.com/doneyli/clickhouse-llm-observability/pull/93), targeting `main` from `codex/bank-assistant-workshop`. Verify the current PR head before working and preserve unrelated changes in the original banking demo.

Read repository `AGENTS.md` and `CLAUDE.md`, then the workshop `README.md`, `ASSESSMENT.md`, `VALIDATION.md`, `docs/MAINTAINER.md`, `docs/2026-10-11-adversarial-review.md` and `docs/2026-10-12-live-rehearsal.md`. Work only in `demos/bank-assistant-workshop`; preserve `demos/bank-assistant` and the immutable source snapshot.

## Completed live acceptance

- Dedicated Langfuse Cloud US project [banking-workshop](https://us.cloud.langfuse.com/project/cmv52b9ld00gtad0eksrj88pr), in the owner's `clickhouse` organization. Org authentication works after the format adapter; list projects with `api organizations list-projects`, not the project-scoped `api projects list`.
- Private, ignored mode-0600 workshop settings exist in `.env`; normalized org settings and a project-key recovery response are also ignored. Never print, commit or archive these files. Clear inherited Langfuse variables and use the repository CLI wrapper with `--env` and an explicit host. Verify the selected project before mutation.
- Checkpoint 00 produced a real untraced answer. Traced RAG/MCP/blocked/PII/capitals/complaint paths passed exported Cloud checks for one logical root, nested generations/tools/retrieval/guardrails, tokens/model, prompt linkage, sessions, masking and cross-process MCP children. All three application signals target the correct roots.
- Baseline prompt v1 and candidate v2 were published. Candidate serving, missing-label fallback, and restoration of `workshop-serving` to v1 passed. Ordinary app settings still select `workshop-baseline`.
- The eight owner-authorized synthetic draft references were checked against the copied KB/guardrails and uploaded with human-review markers preserved. Dataset snapshot: `2026-10-12T09:48:48.619694+00:00`.
- Actual baseline/candidate/no-judge experiments each completed eight ACTIVE items. Cloud exports verified all 24 item/run links, exact prompt versions and 30/30/22 applicable scores. Citation checks changed from 5/6 to 6/6 policy items; all other applicable checks passed. The report contains actual run and trace links. The native experiment-item version field is null with the pinned SDK; historical input/reference equality was independently verified.
- Fictional card blocking and reset after restarting both processes passed. Local tests include the four earlier experiment regressions and the live-audit trace fixes. See `VALIDATION.md` for final local/package checks.

## Remaining work and authorization boundary

1. **Platform judge execution.** Cloud accepted disabled evaluator `customer-disagreement` (`cmv52tbpn00m0ad0ei8u55gjp`) and disabled rule `banking-workshop-preview` (`cmv52tcec00n4ad0jf2exn97g`). The report records exact mappings and filters. Automatic approval review rejected transferring the existing Anthropic key into a Langfuse platform connection because that specific credential egress had not been authorized. The owner was asked to approve a connection named `bank-workshop-anthropic` in this dedicated project and a two-turn preview. Check whether that authorization has now been given; do not infer it from authorization for local application model calls.
2. After explicit approval, provision the dedicated project's LLM connection privately, configure the evaluator model, and preview one normal and one disagreement turn. A temporary rule may target only root name `northwind-assistant`, type AGENT, environment `workshop`, `isRootObservation=true`, and exact session `banking-workshop-preview-20261012`. Disable it afterward and verify actual score targets. Never enable broad scoring or change another project.
3. **Human instructor review and calibration.** All eight references remain `reference_review_required=true`. A person must review them before clearing those markers. The synthetic draft is not production ground truth. Calibrate the disagreement rubric with a person.
4. **Another machine.** Install the credential-free distributed archive on a separate machine and run its setup checkpoint. A local packaging pass does not establish this acceptance check.
5. For any resulting source/docs changes, run the tests and validator, regenerate checkpoints from the complete reference root, verify archive exclusion/parity, commit/push only workshop changes to this PR branch, and update the PR description with exact outcomes and remaining checks.

Proceed autonomously within the already authorized workshop scope. Ask only for missing credentials, the unresolved provider-transfer approval, or a material decision. Do not claim a disabled mapping draft is a completed platform preview or a local install is a second-machine test.
