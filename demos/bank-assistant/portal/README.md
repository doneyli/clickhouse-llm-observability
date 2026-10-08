# Northwind Bank — presenter portal

One page, three tabs, for screen-sharing the Northwind Bank (fictional) Langfuse demo.

```bash
scripts/run_portal.sh            # start / restart in the background → http://localhost:8090
scripts/run_portal.sh --fg       # foreground (Ctrl-C to stop)
scripts/run_portal.sh --status   # is the portal / MCP server up?
scripts/run_portal.sh --stop     # stop the portal (the MCP server keeps running)
tail -f logs/portal.log          # portal errors (agent tracebacks land here)
```

`run_portal.sh` also starts the core-banking MCP server on `:8765` if it is not
running (`logs/mcp.log`), and prints which Langfuse target (`config.PROFILE`,
base URL), environment, prompt label and model are active. It never prints keys.
Shell-exported `LANGFUSE_*` variables are unset — the demo's `.env` (+ `.env.cloud`
overlay) decides the target.

## Tabs

| Tab | What it does |
|-----|--------------|
| **Customer assistant** | Chat as one of 4 customers (C-1001…C-1004) on web / app / WhatsApp. Each reply shows tool calls, cited `KB-xxx` sources, guardrail risks, a red *Blocked by guardrail* badge, latency, prompt version + model, and buttons to open the **same trace id** in Langfuse and in the APM (Jaeger). 👍/👎 writes a `user-feedback` BOOLEAN score (👎 takes an optional comment). History is kept server-side per session (in memory); *New conversation* = new Langfuse session id. The *Prompt label* selector (sidebar) runs a turn against `staging` / `development` — it resets to production on every page load. |
| **Voice** | Lists `data/voice/*.mp3|wav` (titles + caller from `data/voice/manifest.json`), plays them, and *Process call* runs `northwind.voice.run_voice_turn` → transcript, answer, spoken reply (auto-plays), STT / agent / TTS timings, Langfuse + APM links. Upload a `.wav`/`.mp3` too. If `northwind/voice.py` is missing or fails to import, the tab shows a placeholder; the module is re-imported when the file changes, no restart needed. |
| **Presenter console** | Header strip: environment, Langfuse target, **production prompt version** (live read, refresh button), served label, model, quick links (Langfuse project, Jaeger, n8n, self-hosted Langfuse). Act cards run a **fixed allowlist** of CLI scripts (below) and stream stdout into the console; URLs in the output become buttons (summary pages first, then the latest traces). One run at a time; *Stop* kills the process group. Reloading the page re-attaches to a running act. Missing scripts show "not available yet". |

## Presenter-console allowlist

The browser sends only an act id; the server maps it to one of these argv lists
(`ACTS` in `server.py`). There is no other way to execute a command.

| # | Act | Command |
|---|-----|---------|
| 0 | Pre-flight check | `.venv/bin/python portal/preflight.py` (read-only; checks MCP, Langfuse auth, prompt labels, datasets, model keys present, Jaeger, n8n, voice samples, scripts) |
| 1 | Generate production traffic | `.venv/bin/python scripts/generate_traffic.py --n 20` |
| 2 | Red-team attack suite | `.venv/bin/python scripts/generate_traffic.py --scenario security` |
| 3 | Run voice calls | `.venv/bin/python scripts/run_voice_calls.py` |
| 4 | Run n8n complaint workflow | `.venv/bin/python scripts/run_n8n_samples.py` |
| 5 | Experiment: prompt A/B | `.venv/bin/python scripts/run_experiment.py --prompt-label staging` |
| 6 | Experiment: model comparison | `.venv/bin/python scripts/run_experiment.py --model gpt-4.1` |
| 7 | CI quality gate | `.venv/bin/python scripts/prompt_gate.py --prompt-label development` |
| 8 | Promote / roll back prompt | `.venv/bin/python scripts/prompt_label.py --promote staging` · `… --rollback` |

After 8 finishes, the header re-reads the production prompt version.

## API (for scripting / smoke tests)

```bash
curl -s localhost:8090/api/info                                   # target, prompt version, links
curl -s -X POST localhost:8090/api/chat -H 'Content-Type: application/json' \
  -d '{"message":"What is the late payment fee on the Classic card?","customer_id":"C-1001","channel":"web","session_id":"nw-smoke-1"}'
curl -s -X POST localhost:8090/api/feedback -H 'Content-Type: application/json' \
  -d '{"trace_id":"<32-hex>","value":0,"comment":"too long"}'
curl -s -X POST localhost:8090/api/run/preflight                  # → {job_id}
curl -sN localhost:8090/api/run-stream/<job_id>                   # SSE: event line / done
curl -s -X POST localhost:8090/api/run-stop
```

## Files

- `portal/server.py` — FastAPI app (async; subprocesses via `asyncio.create_subprocess_exec`, sync Langfuse SDK calls in threads)
- `portal/preflight.py` — act 0
- `portal/static/index.html`, `app.css`, `app.js`, `favicon.svg` — vanilla JS, no CDN except Google Fonts (falls back to system fonts offline)

## Before the demo

- Run act **0 Pre-flight** — it must end with `READY`.
- The MCP server keeps banking state in memory (blocked cards, disputes). To start
  from a clean slate, restart it: `kill $(lsof -tiTCP:8765 -sTCP:LISTEN); scripts/run_portal.sh`.
- Traces are flushed in the background after each turn; on Langfuse Cloud a trace
  can take a few seconds to appear after you click *Open in Langfuse* — refresh once.
