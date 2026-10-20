# Booth loop videos — outlines

Five short, silent, looping videos for a Langfuse booth, built on the Northwind
Bank assistant demo. A booth visitor gives a screen 3–10 seconds, usually from
two or three metres away, with no sound. So every video:

- opens on a **question** that stands on its own (the hook is also the loop point)
- carries **one idea per beat**, ≥ 3.5 s each, ≤ 8 words of caption
- shows **real screens** from the Cloud project `northwind-bank-assistant`,
  zoomed to the one number or span that matters, with a coral highlight
- ends on the brand card, which dissolves back into the hook, so the loop has no seam

Northwind Bank is fictional and all data is synthetic. Numbers are read from the
Cloud project on 2026-10-13. Where they come from: [DEMO_SCRIPT.md](../DEMO_SCRIPT.md)
(story arc, six business issues) and [EVALS_FIRST_VIDEO.md](EVALS_FIRST_VIDEO.md).

## Production spec

| Item | Choice |
|---|---|
| Output | 1920×1080 MP4, H.264, yuv420p, 30 fps, **no audio** |
| Length | 30–56 s each (see each outline) |
| Brand | Langfuse brand kit: off-white `#F4F4EF` background texture, charcoal `#1B1917` type, blue `#4E9CFF`, coral `#FF5D5F` for highlights; *Langfuse by ClickHouse* wordmark bottom-right on every frame |
| Type | Headlines 88–120 px, captions ≥ 44 px, at 1080p |
| Chrome | top-left series chip (e.g. `TRACE-FIRST`) + "Northwind Bank · fictional demo"; bottom progress ticks, one per beat, so someone arriving mid-loop knows where they are |
| Screens | captured at 1920×1080, 2× device scale (3840×2160), light theme, shown in a window frame with a slow push-in to the highlight |
| Never on screen | account menu, user e-mail, API keys, Settings |
| Build | `video/booth/` — `scenes/*.html` (one animated page per video), `shots/` (captures), `render.mjs` (frame-accurate capture with Playwright) → `out/*.mp4` |

---

## 1 · Business scenarios — "What is your AI assistant worth?" (40 s)

**Idea.** One assistant handles real banking jobs across voice, web and WhatsApp,
in two languages, and Langfuse turns every turn into a business number.

| t | Beat | On screen | Source |
|---|---|---|---|
| 0:00 | Hook | **What is your AI assistant actually worth?** · sub: *A bank assistant, traced in Langfuse* | — |
| 0:04 | Lost card (voice) | Caller: "I think I lost my credit card… block it" → "Card ending 4417 is blocked" · chip `resolved-self-service` · `+$6.50 saved` | trace `9d431b5b…` |
| 0:07.5 | Dispute (web) | "I don't recognise this charge — dispute it" → "Dispute DSP-725272 opened" · chip `dispute-resolved 1` | trace `1be9e4f4…` |
| 0:11 | Advice (ES) | "¿Debería poner mis ahorros en bitcoin?" → "…conectarle con uno de nuestros asesores" · chips `formal-register ✓` `advisor-offered` | trace `af869a76…` |
| 0:14.5 | Card number pasted | "My card number is 4111…" → stored as `[REDACTED_CARD ****1111]` · chip `pii-in-input` | trace `22363dc5…` |
| 0:18 | Attack | "Ignore all previous instructions…" → blocked before the model · chip `guardrail-blocked` · `$0.00 model cost` | trace `9b3e49cd…` |
| 0:21.5 | Every turn scored | Trace **Scores** tab: `task-outcome`, `contained`, `value-usd`, `turn-cost-usd` · caption **Every turn becomes a business score** | screenshot |
| 0:28 | The dashboard | *Business value & failure modes* dashboard · caption **Value delivered vs. LLM spend, live** | screenshot |
| 0:35 | End card | **Langfuse — see what your AI is worth.** · *Ask us for the 2-minute demo* → dissolve to hook | — |

---

## 2 · Eval-first walkthrough — "Good enough to ship?" (56 s)

**Idea.** Define "good" first, grade every answer, test the grader, prove the
change offline, let CI say no, and turn production failures into tests.

| t | Beat | On screen | Source |
|---|---|---|---|
| 0:00 | Hook | **How do you know it's good enough to ship?** | — |
| 0:04 | Define good | Five questions slide in, each snaps to a score: *True?* → `faithfulness` · *Allowed?* → `banking-compliance` · *Safe?* → `manipulation-resistance` · *Right language?* → `formal-register` · *Job done?* → `task-outcome` · caption **Write "good" down first** | — |
| 0:11 | Every answer graded | Scores tab of a live turn; push in on judge score + reasoning · caption **Free checks + LLM judges, every turn** | trace `b31717e3…` |
| 0:18 | Grade the grader | Big number **80% agreement** → flips to **κ = −0.11** · caption **The judge caught 0 of 3 expert fails** · queue screenshot behind | annotation queue |
| 0:26 | Is the new prompt better? | Dataset run compare; highlight `correctness 0.91 → 0.91` then `formal-register 0.00 → 1.00` · caption **The judge didn't move. The checks did.** | golden-set runs |
| 0:34 | Let the gate say no | GitHub Actions red run: `correctness 0.969 PASS (soft)` · `no-unsolicited-upsell 0.385 FAIL (hard)` · `GATE FAILED` · caption **The judge said ship. The gate said no.** | Actions run 37786226009 |
| 0:42 | Production teaches the dataset | Disputes bar at 0.20 → disputes dataset `dispute-opened 0.20 → 1.00` · caption **Every production failure becomes a test** | dashboard + disputes runs |
| 0:50 | End card | Loop: *Define · Grade · Calibrate · Compare · Gate · Learn* · **Evals first. Then ship.** → dissolve to hook | — |

---

## 3 · Trace-first walkthrough — "What did your agent actually do?" (45 s)

**Idea.** One customer request, followed through every step of one trace: guardrail,
LLM calls, tools, a separate MCP service, and masked PII.

| t | Beat | On screen | Source |
|---|---|---|---|
| 0:00 | Hook | **What did your AI agent actually do?** | — |
| 0:04 | The ask | Chat: "I don't recognise this charge — dispute it" → "Shall I open a dispute?" → "Yes, open it" → "Dispute DSP-###### opened" | trace (hero) |
| 0:09 | One trace | Full trace tree, slow push-in · caption **Every step. One trace.** | screenshot |
| 0:15 | Guardrail | `input-guardrail` observation · caption **Security rules run first** | screenshot |
| 0:19.5 | LLM call | `generate-response` generation: model, tokens, cost, latency · caption **Every LLM call: tokens, cost, latency** | screenshot |
| 0:24.5 | Across services | `mcp-client` → `mcp-server: open_dispute` · caption **Another service. Same trace.** | screenshot |
| 0:30 | Agent graph | Graph view of the LangGraph loop · caption **The agent's loop, mapped** | screenshot |
| 0:34.5 | PII | Input shows `[REDACTED_CARD ****1111]` · caption **Card numbers masked before export** | trace `22363dc5…` |
| 0:39 | End card | **Model · retrieval · tools · services — one trace.** · *Langfuse, open source & by ClickHouse* → dissolve to hook | — |

---

## 4 · Failure modes — "It passed every test. Here's what it got wrong." (52 s)

**Idea.** Six quiet failures found in production. Each card follows the same
rhythm: *what broke → where Langfuse showed it → the fix → the number*.

| t | # | Failure | Found in Langfuse | Fix → result | Source |
|---|---|---|---|---|---|
| 0:00 | — | Hook: **Your AI passed every test. Here's what it got wrong.** | | | — |
| 0:04 | 01 | **Mis-selling** — a fee question answered with "upgrade to Premier?" | compliance judge cites rule **P3** | prompt rule → upsell 12% → **0%** | trace `56635bee…` scores |
| 0:11 | 02 | **Wrong register** — Spanish customers called *tú* | `formal-register` check lists *tienes, tu* | prompt v5 → **0.00 → 1.00** | trace `5c0343f6…` |
| 0:18 | 03 | **Disputes leak** — 8 in 10 left unresolved, judges gave 1.0 | MCP server span: `window_days: 30` (policy: 60) | one code default → **0.20 → 1.00** | trace `a4e26160…` |
| 0:25 | 04 | **Cost creep** — +13% per turn, quality flat | retriever pulls 8 docs, 6 irrelevant | k = 3 + threshold → **$1,756 / M turns** | trace `4a34ae08…` |
| 0:32 | 05 | **Prompt injection** — "Ignore all previous instructions…" | `input-guardrail` blocks before the model | sampled judge as safety net → **$0 model cost** | trace `9b3e49cd…` |
| 0:39 | 06 | **The judge was wrong** — 80% agreement, κ = −0.11 | expert review queue | fix the judge, not the agent | annotation queue |
| 0:46 | — | Payoff + end card: **Find it in a trace. Fix it. Make it a test.** → dissolve to hook | | | — |

---

## 5 · End-to-end (quick) — "The AI engineering loop in 30 seconds" (30 s)

**Idea.** The whole loop in six fast cuts around a ring diagram that lights up
step by step.

| t | Step | On screen | Source |
|---|---|---|---|
| 0:00 | Hook | **The whole AI loop. 30 seconds.** | — |
| 0:03 | 1 Trace | trace tree · **Trace every turn** | screenshot |
| 0:06.5 | 2 Evaluate | Scores tab · **Score every answer** | screenshot |
| 0:10 | 3 Calibrate | annotation queue · **Check the judge with experts** | screenshot |
| 0:13.5 | 4 Experiment | run compare · **Compare prompts and models** | screenshot |
| 0:17 | 5 Gate | GitHub red run · **Block bad releases in CI** | screenshot |
| 0:20.5 | 6 Monitor | business dashboard · **Watch outcomes in production** | screenshot |
| 0:24 | Close the loop | ring completes, arrow back to 1 · **Failures become tests** | — |
| 0:27 | End card | **Langfuse — the AI engineering loop.** → dissolve to hook | — |

---

## Running them at the booth

- Play each MP4 on loop, full-screen (QuickTime: View → Loop; VLC: `--loop --fullscreen`).
- A single screen can play all five in a playlist: `ffmpeg -f concat` them into
  `booth-reel.mp4` (≈ 3 min 45 s), built by `render.mjs --reel`.
- Keep a laptop on the live demo next to the screen: the video catches the eye, the live demo answers the question.
