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
- [static/clickui-tokens.css](static/clickui-tokens.css): vendored ClickUI tokens.
- [static/app.css](static/app.css): styles, and the token mapping in `:root`.
- [static/brand/](static/brand): Langfuse brand assets.

### Visual Style

Two palettes, layered:

| Layer | Source | Used for |
|---|---|---|
| Langfuse brand | official brand assets | blue `#4E9CFF`, coral `#FF5D5F`, charcoal `#1B1917` — brand marks, focus rings, the dark navigation surface and console |
| ClickUI tokens | `static/clickui-tokens.css` | neutrals, surfaces, strokes, and the success / warning / danger pairs |

`static/clickui-tokens.css` is a curated subset of `@clickhouse/click-ui`'s own
`src/theme/styles/tokens-light.css` and `tokens-dark.css`, copied verbatim. The
portal is zero-build (plain HTML, CSS and JS served by `StaticFiles`), so it
cannot import the React package the ClickUI README installs; it styles against
the published token values instead. **To refresh:** re-copy the matching
declarations from those two files in `ClickHouse/click-ui@main` — do not
hand-edit the values. The sheet must stay linked *before* `app.css` in
[static/index.html](static/index.html), because `:root` in `app.css` maps the
`--click-*` tokens onto the local names (`--bg`, `--surface`, `--border`,
`--text`, `--muted`, …) that the rest of the sheet uses. Only the light theme is
active; the sheet's dark block is gated on `data-cui-theme="dark"`, which the
portal never sets.

Langfuse blue measures 2.8:1 on white, so it is an accent only — never text on a
light surface. Text and filled buttons use the darkened `#1F65BB` (5.8:1 on
white, and white on it) with `#194F91` for hover. The feedback inks clear 4.5:1
on their own tints; ClickUI's success ink is 4.3:1 on its tint, so success uses
that hue darkened. Chat and voice responses use 16px medium-weight text with
extra line spacing.

Type is Northwind's own — Plus Jakarta Sans and JetBrains Mono, not ClickUI's
Inter and Inconsolata — so the fictional bank's product chrome does not read as
ClickHouse's own UI. For the same reason Northwind keeps its name, mark and
favicon in the header; the "Langfuse by ClickHouse" wordmark appears only where
the portal credits observability: beside the chat's **Traced · PII-masked**
badge and in the presenter console's status strip.
