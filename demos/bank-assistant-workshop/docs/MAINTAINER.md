# Maintainer notes

The root is the finished reference solution. `scripts/checkpoint.py` assembles independent starting states from that tree and replaces only four lesson integration modules. Regenerate all checkpoints after any source, dependency or documentation change. Never patch generated folders as the source of truth.

Build all starting states with `.venv/bin/python scripts/checkpoint.py --build-all`. Existing output directories are refused. To rebuild, choose a fresh output directory. Checkpoints contain current docs, dependencies and source attribution; they never contain `.env`, virtual environments, caches, or the optional original demo. Learners use `--env-file` to point to their own private setup file without copying it.

Run `.venv/bin/python scripts/validate.py` and `.venv/bin/python -m unittest discover -s tests -t . -v`. For a live rehearsal, follow `VALIDATION.md`. Dependencies are fully pinned in `requirements.txt`; update through `uv pip compile requirements.in --python-version 3.12 --output-file requirements.txt`, then repeat the trace hierarchy rehearsal. The copied agent's `_nest` helper uses private callback state, so an SDK import test alone is insufficient.

The [2026-10-12 report](2026-10-12-live-rehearsal.md) contains verified Cloud evidence and remaining delivery checks. Use `api organizations list-projects` with normalized org settings; `api projects list` requires project credentials. CLI calls can use the repository wrapper's `--env` option with a canonical settings file and an explicit Cloud host after clearing inherited Langfuse variables. Never print or archive API-key responses. The private adapter has been exercised on the owner's authorized org settings. Provider-key transfer to a platform connection needs separate authorization from local application use.

The source snapshot is immutable. Changes belong in `src/` and new workshop documentation. `SOURCE_MANIFEST.json` records original file hashes. A release archive excludes `.env`, `.venv`, working exercises and Python caches. Neither assembly nor packaging creates prompts, datasets, traces or scores remotely.

Source revisions and the design decision are in [the assessment](../ASSESSMENT.md), [official checkpoint design](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/AGENTS.md) and [SDK documentation](https://langfuse.com/integrations/frameworks/langchain).

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
