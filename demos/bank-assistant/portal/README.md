# Northwind Bank Presenter Portal

The portal provides customer chat, voice calls, and a presenter console in
English and Spanish. Complete the [quick start](../README.md#quick-start) first.

All commands below run from `demos/bank-assistant`, not `portal/`.

## Start and Check

```bash
./scripts/run_portal.sh
.venv/bin/python portal/preflight.py
```

Open [localhost:8090](http://localhost:8090). Pre-flight must end with `READY`;
review warnings for the features you plan to present. The launcher also starts
MCP on port 8765 and prints the selected Langfuse target, environment, prompt
label, and model.

| Action | Command |
|---|---|
| Start or restart in the background | `./scripts/run_portal.sh` |
| Run in the foreground | `./scripts/run_portal.sh --fg` |
| Check portal and MCP status | `./scripts/run_portal.sh --status` |
| Stop the portal (MCP stays running) | `./scripts/run_portal.sh --stop` |
| Read portal errors | `tail -f logs/portal.log` |
| Read MCP errors | `tail -f logs/mcp.log` |

## Use the Tabs

| Tab | Actions |
|---|---|
| **Customer assistant** | Select a customer and channel, then send a message. Replies show tools, sources, risks, latency, prompt version, and model. Open the same trace in Langfuse or Jaeger. Thumbs-up/down records a `user-feedback` score. |
| **Voice** | Play a sample, then choose **Process call**. Inspect the transcript, answer, spoken reply, timings, and trace links. You can also upload WAV or MP3 audio. Requires `OPENAI_API_KEY`. |
| **Presenter console** | Run pre-flight, traffic, experiments, gates, and label changes. Output includes evidence links. One action runs at a time; **Stop** ends it. Reloading reconnects to the current action. |

**New conversation** creates a new Langfuse session. Chat history is held in
portal memory. The prompt-label selector can serve `staging` or `development`;
it resets to `production` on page load.

Presenter actions use a fixed allowlist in [server.py](server.py) (`ACTS`).
Promotion and rollback change project prompt labels. Check their current state
before running either action:

```bash
.venv/bin/python scripts/prompt_label.py --show
```

For the complete presentation sequence, see the [demo script](../DEMO_SCRIPT.md).
For optional setup, see [evaluations](../README.md#prepare-evaluations),
[n8n](../n8n/README.md), and [CI](../cicd/README.md).

## Language

Use **EN | ES** in the header. The browser remembers the choice; `?lang=es`
or `?lang=en` overrides it.

The assistant answers in the language of the customer's message. The UI choice
is recorded as `ui_language` metadata and a `lang-ui:en` or `lang-ui:es` tag.
Voice samples in the selected language appear first; Spanish samples use an
`es` transcription hint.

## Restart Banking State

MCP holds blocked cards and disputes in memory. Restart it before rehearsing
those actions or after changing the Langfuse profile.

1. Find the MCP listener: `lsof -nP -iTCP:8765 -sTCP:LISTEN`.
2. Stop that process with `kill MCP_PID`, replacing `MCP_PID` with the listed PID.
3. Run `./scripts/run_portal.sh` to start MCP and restart the portal.
4. Choose **New conversation**.

The launcher reuses an existing MCP listener, so restarting only the portal
does not reset banking state or reload MCP configuration.

## API Examples

```bash
curl -fsS http://localhost:8090/api/info

curl -fsS -X POST http://localhost:8090/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the late payment fee on the Classic card?","customer_id":"C-1001","channel":"web","session_id":"nw-smoke-1"}'

curl -fsS -X POST http://localhost:8090/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"¿Cuánto cuesta una transferencia internacional?","customer_id":"C-1001","session_id":"nw-smoke-es-1","lang":"es"}'
```

Chat calls consume model tokens. Replies include trace links; if a trace is not
visible yet, wait a few seconds and refresh Langfuse.

## Implementation

- [server.py](server.py): FastAPI routes, sessions, and presenter actions.
- [preflight.py](preflight.py): readiness checks.
- [static/app.js](static/app.js): UI behavior and the `I18N` dictionary.
- [static/app.css](static/app.css): styles and design tokens.

### Visual Style

The portal uses the supplied Langfuse brand palette: blue `#4E9CFF` and coral
`#FF5D5F` for brand accents, and charcoal `#1B1917` for navigation and body text.
Tokens live in `static/app.css`; primary buttons and user messages use deeper
blue `#1F65BB` with white text (5.77:1 contrast), with `#194F91` for button hover.
Chat and voice responses use 16px medium-weight text with extra line spacing.
Darker secondary labels, links, and status text stay readable on light surfaces,
and success/warning indicators retain their semantic colors.
