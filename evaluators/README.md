# Langfuse Code Evaluators

TypeScript sources for the deterministic [code evaluators](https://langfuse.com/docs/evaluation/evaluation-methods/code-evaluators)
this demo runs inside Langfuse. They are provisioned automatically by
`./setup.sh` (or `./scripts/seed-code-evaluators.sh`).

| File | Target | Default score |
|---|---|---|
| `sql-safety-guard.ts` | Live generations on `text-to-sql` traces | `sql-risk` |
| `credential-leak-guard.ts` | All live generations | `credential-leak` |
| `response-structure-check.ts` | Live generations on `text-to-sql`/`vector-rag` traces | `structure-clean` |
| `security-behavior-check.ts` | Experiments on `coding-assistant-security` | `security-compliant` |
| `quality-structure-check.ts` | Experiments on `coding-assistant-quality` | `keyword-coverage` |

Each file implements `evaluate(ctx) → { scores: [...] }` using only the
standard library (no network, 2s limit). Edit a file, then re-run
`./scripts/seed-code-evaluators.sh` to push the change.

Full walkthrough — why/when to use code evaluators vs LLM-as-a-Judge,
demo script, debugging: [docs/CODE_EVALUATORS.md](../docs/CODE_EVALUATORS.md).
