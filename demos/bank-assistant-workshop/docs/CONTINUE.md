# Continue banking workshop PR #93

Continue [PR #93: Add standalone bank assistant workshop](https://github.com/doneyli/clickhouse-llm-observability/pull/93), targeting `main` from `codex/bank-assistant-workshop`. The PR is open at this handoff.

Paste the prompt below into a new coding task opened in this repository. Review the credential authorization before using it.

```text
Continue PR #93, "Add standalone bank assistant workshop":
https://github.com/doneyli/clickhouse-llm-observability/pull/93

Repository: doneyli/clickhouse-llm-observability. Base: main. Head: codex/bank-assistant-workshop. Initial implementation commit: daaa9d27608f0e08321898ca5709c538cc31c484. Verify the current PR head and work on that branch, preserving any unrelated local changes.

Continue only demos/bank-assistant-workshop, the separate copied banking workshop. Preserve demos/bank-assistant and the immutable original-demo snapshot.

Read PR #93's description, latest diff, review comments and checks. Read repository AGENTS.md and CLAUDE.md, then the workshop README.md, ASSESSMENT.md, VALIDATION.md, docs/MAINTAINER.md and docs/2026-10-11-adversarial-review.md. Address relevant review findings while completing the dry run.

Goal: create a dedicated Langfuse Cloud US project named banking-workshop in my clickhouse organization, then complete the live workshop dry run. Use the Langfuse CLI and agent skill. Browser automation is unnecessary. Check the org project list before creation so retries do not create duplicates. Keep changes limited to the workshop.

Last recorded state at this handoff:
- Nine modules (00-08), separate learner/instructor notes, a Python/LangGraph banking agent, local MCP server, portal, prompt/dataset/experiment helpers and generated before-exercise checkpoints are implemented.
- 42 tests passed. Astra independently verified the fixes for missing dataset-run links, missing deterministic scores, archived-item execution and incorrect prompt labels. Checkpoint and ZIP parity passed locally.
- Git stores the complete reference solution and checkpoint generator. Generated checkpoints, private settings, caches and virtual environments are ignored. Generate checkpoints from the reference root when needed.
- No new Cloud project, keys, prompts, datasets, traces, scores or live experiments have been created for this workshop. Cloud acceptance and installation on a second machine remain unfinished.

Credential context on my current machine:
- The original bank demo worktree contains .claude/worktrees/bank-demo/demos/bank-assistant/.env.cloud and .env.cloud-org, relative to the repository root.
- .env.cloud works on Cloud US for northwind-bank-assistant (project cmuz1kt5z04uead0eyjm92c7f), in clickhouse (org cmlfwuz9w01c7ad07rxxb27x4). Leave that project unchanged.
- With inherited LANGFUSE_* variables cleared, the CLI reports missing canonical credentials for .env.cloud-org. Earlier HTTP 401 responses did not prove those org keys were invalid. CLI 1.2.4's --env parser does not support export prefixes. Inspect the credential loading mechanism before requesting new keys.
- scripts/normalize_org_env.py is a prepared private format adapter, tested only on synthetic fixtures. It has not read real keys. I authorize using this adapter and private application configuration loaders for this dry run. Never print credentials, paste them into chat, commit them or include them in archives. Store newly generated project keys in ignored mode-0600 settings files. Use an approved repository CLI wrapper for API calls; clear inherited Langfuse keys/hosts and verify which project the selected keys address.

Dry run:
1. Verify org-scoped authentication, create/reuse banking-workshop, generate dedicated project keys and configure the copied app using an existing provider key privately.
2. Follow VALIDATION.md: a real untraced answer; traced RAG and MCP turns; one root, nested generations/tools/retrieval/guardrails, token/model data, sessions, masking and MCP server children in the same trace. Fetch and audit the exported observations against fresh official Langfuse best-practice docs, then fix and rerun any gaps.
3. Publish baseline and candidate workshop prompts. Verify linked versions, missing-label fallback and restoration to baseline. Inspect the three application signal scores and preview the platform judge's structured field mapping without enabling broad scoring on other projects.
4. Review the eight synthetic dataset references against the copied KB. This prompt authorizes using that existing draft dataset for the dedicated dry run; keep instructor review pending and do not claim it is production ground truth. Seed it, freeze its version, then run actual baseline/candidate experiments using identical model, judge and dataset version.
5. Verify ACTIVE item coverage, dataset-run links, all applicable deterministic/judge scores, exact prompt versions, rollback and synthetic state reset. Record measured results and actual project/trace/experiment URLs. Do not invent improvement percentages.
6. Fix confirmed workshop defects and relevant PR review findings, run the appropriate tests and regenerate checkpoints. Commit and push only the workshop changes to codex/bank-assistant-workshop so they update PR #93. Update that PR's description with the final implementation and validation results. Report the PR URL, actual project/trace/run links, precisely which acceptance checks passed and which still need a human or another machine.

Proceed autonomously within this scope. Ask only for missing credentials or a material decision that cannot be resolved from the repository. Do not treat an offline pass as a successful live rehearsal.
```
