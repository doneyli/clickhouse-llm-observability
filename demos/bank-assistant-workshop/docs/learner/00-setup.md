**DRAFT teaching material.**

# 00 Setup

**Goal:** start the same untraced app used in module 01. No traces are expected yet.

From the package root:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env
```

Edit your private `.env`. Use a dedicated Cloud project and a unique `WORKSHOP_ID`. Set your provider key, Langfuse public/secret keys and the correct region URL. `LANGFUSE_PROJECT_ID` is optional but produces direct trace links. Never paste keys into chat or screenshots.

```bash
.venv/bin/python scripts/checkpoint.py 00-setup --output work/00-setup
cd work/00-setup
../../.venv/bin/python workshop.py --env-file ../../.env preflight
../../.venv/bin/python workshop.py --env-file ../../.env serve
```

Open `http://127.0.0.1:8090`. Ask “How much does an outgoing international wire cost?” Expect a banking answer with a policy lookup. The status bar must say tracing disabled. Provider presence is checked by preflight; the first question verifies the model key actually works. Langfuse authentication is deliberately checked only after tracing is enabled.

**Verify:** the portal loads; the model answers; tools include `search_knowledge_base`; no traces are emitted by this checkpoint.

If the server exits, check the terminal for the missing key or model access error. If a port is occupied, stop the other workshop or change `WORKSHOP_PORT` and `NORTHWIND_MCP_PORT` in your setup file. The launcher refuses to attach to an existing process.

The environment file controls this workshop. The app does not search a parent checkout for keys. To stop, press Ctrl-C once; both services shut down.

Source: [official setup structure](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/docs/learner/00-setup.md), [MCP SDK configuration](https://langfuse.com/docs/observability/sdk/overview). HIGH confidence, source level 1. These are Zone A setup patterns.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.
