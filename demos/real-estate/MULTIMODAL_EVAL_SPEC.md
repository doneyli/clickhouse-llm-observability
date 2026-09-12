# Spec — Multi-Modal Traces → Multi-Modal Evals (Property Photo Audit)

**Status:** proposed, not implemented — **§4 spike run and PASSED 2026-08-12** (see §1a).
**Target:** `demos/real-estate`, Langfuse Cloud. **Written:** 2026-08-12.

Extends the existing property-concierge demo with a second, genuinely multi-modal
task so we can answer a customer question the product does not answer on its own:
*"can Langfuse run LLM-as-a-judge on images?"*

---

## 1. The finding this spec is built on

Langfuse's **built-in** evaluators are text-only. Its **SDK experiment runner** is
multi-modal. Those two facts together decide the whole design.

| Surface | Multi-modal? | Source |
|---|---|---|
| Tracing (image/audio/video/PDF on `input`/`output`/`metadata`) | ✅ | [multi-modality](https://langfuse.com/docs/observability/features/multi-modality) |
| Dataset items holding media | ✅ SDK `LangfuseMedia` + UI attach/drag/paste | [datasets](https://langfuse.com/docs/evaluation/experiments/datasets) |
| **Experiments via SDK** on media datasets | ✅ Python `>= 4.10.0`, `@langfuse/client >= 5.6.0` | [#multimodal-experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk#multimodal-experiments) |
| Experiments via **UI** on media datasets | ❌ "do not yet support dataset items with media attachments" | same |
| **Managed LLM-as-a-Judge** | ❌ text-only | see below |
| **Code evaluators** | ❌ text-only, structurally | "run without network egress", stdlib only, 2 s |

### Why the managed judge is text-only

The judge's variable substitution (`worker/src/features/evaluation/evalService.ts`)
passes mapped values through `parseUnknownToString` — objects become
`JSON.stringify(value)` — into a plain-text message builder. There is no
`resolveMediaReferences` call, no `@@@langfuseMedia:...@@@` token handling, and no
construction of image content-parts for the judge call.

**The failure mode is silent.** Point a managed judge at an observation holding an
image and the judge receives the literal string:

```
@@@langfuseMedia:type=image/jpeg|id=<uuid>|source=base64_data_uri@@@
```

It does not error. It returns a confident, meaningless score. A customer who wires
this up in the UI gets a green dashboard measuring nothing. **Demonstrating this
deliberately is the highest-value two minutes of the demo** — it reframes the
conversation from "feature gap" to "eval design", which is the conversation we
actually want.

Code evaluators cannot close the gap either: no network egress means they cannot
fetch media by id, so they see the same token string.

### What therefore works today

A real vision judge, authored in the SDK. `get_dataset()` hydrates each media token
into a signed `LangfuseMediaReference` exposing `fetch_bytes()` / `fetch_base64()` /
`fetch_data_uri()`. Your evaluator function fetches the image and calls a vision
model itself; the score lands on the dataset run like any other. Langfuse is the
store, the orchestrator and the comparison UI — the vision call is ours.

For **online** (production traffic) scoring, the same shape as an external pipeline:
fetch traces → `resolve_media_references(obj=..., resolve_with="base64_data_uri")` →
vision model → `create_score()`. Managed real-time judges cannot do this for images.

---

## 1a. Spike results — go/no-go: **GO**

Run 2026-08-12 against the live `real-estate` project on US Cloud
(`cmrtgeanp0303ad0d6bjhud35`), in a throwaway Python 3.11 venv on
**langfuse 4.14.4**. The repo was not modified. Everything below is measured, not
inferred.

| # | Check | Result |
|---|---|---|
| 1 | `LangfuseMedia` in a dataset item → `get_dataset()` hydration | **PASS** — `langfuse.media.LangfuseMediaReference`; `fetch_bytes()` sha256 round-trips (3732 B); `fetch_data_uri()` → `data:image/png;base64,…` |
| 2 | SDK evaluator receives pixels and lands a score | **PASS** — judge reported `circles=2 squares=1` on a fixture of exactly 2 circles + 1 square, so pixels demonstrably arrived. `photo-verdict-correct: 1` confirmed server-side over REST |
| 3 | Trace → dataset promotion hydrates (§3.5's open question) | **PASS** — a raw token copied verbatim out of an observation into a dataset item hydrates and its bytes resolve; `source_trace_id`/`source_observation_id` preserved |
| 4 | `propagate_attributes` reaches LangGraph node observations | **PASS** — `userId`/`sessionId` on all 8 observations; observations queryable by propagated `userId`; 3 taken nodes traced, untaken branch correctly absent |
| 5 | v3.15 → v4.14 migration scope | **PASS** — exactly one break (below) |

### The anti-pattern is now confirmed against the API, not just the source

The stored dataset-item field a managed judge would map is, verbatim:

```
'@@@langfuseMedia:type=image/png|id=EBMA1E_YO8O8kymIxRT-Wy|source=bytes@@@'
```

and the whole mapped input field stringifies to:

```json
{"claim": "The image shows three red circles and no square.",
 "photo": "@@@langfuseMedia:type=image/png|id=EBMA1E_YO8O8kymIxRT-Wy|source=bytes@@@",
 "question": "Does the photo support the claim?"}
```

That is what layer D puts on screen. No source-code reading required to make the point.

### Design finding that reverses §3.1

**Keep the manual wrapper span.** The trace shape came out as:

```
spike-photo-audit-graph   SPAN        isRootObservation=True    <- the wrapper
  photo-audit-graph       CHAIN       false                     <- LangGraph's own root
    vision_extract        CHAIN       false
      ChatAnthropic       GENERATION  false
      RunnableCallable    CHAIN       false
    audit_claims          CHAIN       false
      ChatAnthropic       GENERATION  false
    compose_answer        CHAIN       false
```

Exactly one logical root, and it is **our wrapper span**, carrying clean
`input`/`output`. LangGraph's own root (`photo-audit-graph`, type `CHAIN`) is *not*
flagged. So a managed judge filtered on **Is Root Observation** hits the wrapper —
which is precisely where we want to write the judge-facing summary. Dropping the
wrapper would hand the judge raw LangGraph state instead. This contradicts my
initial reading that the wrapper was redundant; it is load-bearing.

Also note the framework noise: **8 observations for a 3-node path** (`RunnableCallable`,
two `ChatAnthropic` generations). Fine for a debugging view, but name the nodes
deliberately or the trace reads as clutter on a call.

### New constraints the spike surfaced

1. **Python ≥ 3.10 is required** — langfuse ≥ 4.0 refuses to install on 3.9. This
   Mac's system `python3` is **3.9.6**; `python3.11` (3.11.14) exists and the
   existing `demos/real-estate/.venv` is already 3.11, so the demo is fine — but a
   fresh clone on system Python will fail confusingly.
2. **`langfuse.langchain.CallbackHandler` needs the `langchain` meta-package**, not
   just `langchain-core`. `langgraph` + `langchain-anthropic` alone raises
   `ModuleNotFoundError: No module named 'langchain'` at import.
3. **`run_experiment(name=...)` auto-suffixes the run name** with ` - <iso timestamp>`
   when `run_name` is omitted, so `GET /datasets/{n}/runs/{name}` 404s on the bare
   name. Pass `run_name` explicitly — the CI gate depends on looking a run up.
4. **Media dedup is real and visible**: the same fixture bytes produced the same
   `mediaId` across two independent uploads (dedup key = project + content type +
   sha256). Handy — the same photo across many dataset items costs one object.
5. **`langfuse 4.14.4`** was what `>=4.10,<5.0` actually resolved to.

### The v4 bump — APPLIED and verified 2026-08-12

`Langfuse.update_current_trace()` is **gone**. Used at two call sites, both in
[agent/concierge.py](agent/concierge.py) (~L170, ~L317). It decomposed into three
methods: `propagate_attributes()` (the correlating attributes), `set_current_trace_io()`
(input/output — deprecated, see below) and `set_current_trace_as_public()`.

⚠️ **`set_current_trace_io` takes `input`/`output` only — no `metadata`.** The L170
call also passed `metadata={"agent_model": model}`, which moved into the
`propagate_attributes(...)` call already present at ~L150 — a bonus, since
propagating it also makes `agent_model` filterable on child observations, which is
what observation-level evaluators match on. So: 2 call sites, 3 edits, one a move
rather than a rename. Everything else the demo touches survives v4 unchanged.

⚠️ **`set_current_trace_io` is itself deprecated** — its docstring calls it "a legacy
method for backward compatibility with Langfuse platform features that still rely on
trace-level input/output (e.g. legacy LLM-as-a-judge evaluators)" and says it will be
removed in a future major.

**CORRECTION (2026-08-12, full v4 migration pass): both calls have been REMOVED.**
The reason given above for keeping them — that the Traces-table column is what the
demo reads from on stage — is wrong, and was verified wrong two ways:

1. `scripts/smoke_test.py` never called `set_current_trace_io`, yet its trace's
   `input` came back identical to its root observation's `input`.
2. A live concierge turn run *after* removing both calls
   (trace `7482452505a91636cafcb06beec4f11c`, tag `v4-migration-verify`) has
   `trace.input` and `trace.output` fully populated.

v4 is observations-first: the trace's input/output **derive from the root
observation**, which `root.update()` already sets. The docs agree — *"For new code,
set input/output on the root observation directly."* The escape hatch is only for
legacy **trace**-target judge rules, and this project has none (both rules target
observations). Nothing on stage changes.

**Verified green after the bump** (langfuse 4.14.4, Python 3.11.14):

| Path | Result |
|---|---|
| `scripts/smoke_test.py` | PASS — 2 observations, observation-level + trace-level scores |
| one live `run_turn` | PASS — trace input, trace output, `metadata.agent_model`, sessionId, userId, tags, 7 nested observations, 5 code scores |
| demo experiment path (`run_turn(is_experiment=True)` + `ALL_EVALUATORS`/`RUN_EVALUATORS`, 2 real dataset items) | PASS — all 9 item-level scores (incl. categorical `tone`) and all 8 run-level means |
| `scripts/verify_multimodal.py` | PASS — 11/11 |

Two incidental findings from doing it:

- **The demo venv's `pip` shebang is stale** — it still points at
  `…/real-estate-demo/.venv/bin/python3.11`, from before the `demos/` reorg, so
  `./.venv/bin/pip` fails with `bad interpreter`. `./.venv/bin/python -m pip` works.
  Worth recreating the venv at some point.
- **`property-concierge-eval` now has 19 items**, but `cicd/thresholds.json`'s
  measured table is captioned "18 items". A 19th item shifts every mean, so re-measure
  before trusting those numbers as a baseline again. Not touched here.
- Task functions receive a **`DatasetItem`** (`item.input`) for Langfuse datasets but a
  plain **dict** (`item["input"]`) for local data. `make_task` uses attribute access,
  so it only works with Langfuse datasets — the multimodal task will inherit the same
  constraint.

### Still unverified (deliberately)

- The literal **"+ Add to dataset" UI button**. I tested the mechanism it relies on
  (token → hydration), which is the part that could have failed. The button's only
  job is to copy the field.
- A **managed judge actually firing** on a media token end-to-end. The stored-value
  evidence above plus the worker source make the outcome certain enough to build on;
  provisioning a live rule is a phase-4 task.
- **Self-hosted parity** and **vision cost at scale**.

### The spike is now a committed regression check

Everything above is codified in **[scripts/verify_multimodal.py](scripts/verify_multimodal.py)** —
11 assertions over the 6 mechanisms, exit-non-zero, so it can run in CI. It builds
its own fixture (2 red circles + 1 blue square) as a **stdlib-only PNG** — no Pillow
dependency, and the vision judge is asserted against an unambiguous ground truth
(`n_circles == 2 and n_squares == 1`), so "the model actually saw pixels" is a real
assertion rather than a vibe.

```bash
./.venv/bin/python scripts/verify_multimodal.py                # all 6
./.venv/bin/python scripts/verify_multimodal.py --skip-vision   # no vision spend
./.venv/bin/python scripts/verify_multimodal.py --cleanup       # remove probe items
```

The LangGraph check **auto-skips** when `langgraph` is absent (it arrives in phase 2),
so the script is green on today's dependency set and gains a check for free later.
Verified both ways: 11/11 with langgraph present, 8/8 without.

⚠️ **Dataset deletion is not in the public API.**
`DELETE /api/public/datasets/{name}` → **405 Method Not Allowed**, but
`DELETE /api/public/dataset-items/{id}` → **200**. So `--cleanup` deletes the probe
*items* and leaves the empty dataset shell, which has to go from the UI. Same shape
as the [[langfuse-project-deletion-recipe]] problem — plan any teardown story around
per-item deletes.

Artifacts currently in the project: dataset `spike/multimodal-probe` plus traces
tagged `verify:multimodal` and `spike:multimodal`. Left in place — the anti-pattern
item and the LangGraph traces are worth looking at before building.

---

## 1b. Phases 1-3 BUILT and verified — 2026-08-27

`scripts/verify_photo_audit.py` **21/21 PASS** against the live `real-estate`
Cloud project (langfuse 4.15.1, claude-sonnet-4-6). Four gates: fixtures,
dataset, trajectory, evaluation.

| Layer | Delivered | Status |
|---|---|---|
| Contract | `agent/photo_contract.py` | single source of truth for vocabulary, schema, 10 score names |
| Fixtures | `data/photo_scenes.py` | 21 scenes, stdlib-only PNG renderer, self-checking |
| Seeder | `scripts/seed_photo_dataset.py` | idempotent, `--dry-run`, `--limit` |
| Trajectory | `agent/photo_audit_graph.py` | 5-node LangGraph + conditional self-correct branch |
| Layer C (code) | `agent/photo_scoring.py` | 7 deterministic evaluators |
| Layer A (vision) | `evaluators/vision_judges.py` | 2 SDK judges that fetch pixels |
| Tests | `scripts/test_photo_scoring.py`, `scripts/verify_photo_audit.py` | 22 offline + 21 live |

Measured trace shape on a full audit — **10 observations, exactly one logical
root, propagated attributes on all 10**:

```
audit-listing-photo          SPAN        isRootObservation=True   <- judges target this
  photo-audit-graph          CHAIN
    vision_extract           CHAIN
      vision:extract-attributes  GENERATION   <- carries the PHOTO
    route_after_extract      CHAIN
    retrieve_listing         CHAIN
      tool:get_listing       TOOL
    audit_claims             CHAIN
      llm:adjudicate-claims  GENERATION       <- carries the attribute TEXT
    compose_answer           CHAIN
```

All 9 item-level scores and all 9 `avg-*` run-level means land and are queryable
server-side.

### The calibration bug the first run found

The first live run **declined a perfectly readable scene** — `extraction_confidence`
0.45 against a 0.55 floor — so the audit never ran. Cause: the extractor prompt
said "property **photograph**" and tied confidence to photographic quality
(dark / blurred / *low-resolution*). Our fixtures are schematic renders, so the
model correctly observed "this is not a photograph" and marked itself unsure even
though every attribute was plainly legible.

**This is a direct cost of the rendered-fixture decision**, and worth stating
plainly: a licensing-safe choice moved a failure into the prompt. Fixed by
redefining confidence as *legibility of the listed attributes*, explicitly not
realism, and telling the extractor a clean diagram is high-confidence evidence.
Re-measured across scene classes:

| scene class | confidence | branch |
|---|---|---|
| supported | 0.92 | audited |
| contradicted_visible | 0.85 | audited |
| contradicted_subtle | 0.90 | audited |
| unverifiable | 0.92 | audited |
| low_quality (dark+blurred) | 0.20 | **declined** |
| low_quality (underexposed) | 0.10 | **declined** |

Floor 0.55, so the separation is wide rather than knife-edge. **Re-run this probe
after any fixture or prompt change** — the self-correct branch depends on a
self-reported number, which is the softest link in the chain.

### Two bugs fixed in pre-existing code

- **`_mean_evaluator` returned `Evaluation(value=None)`** when a score had no
  values — the shape the Langfuse docs example uses. SDK v4 rejects it
  (`ScoreBody.value` is `Union[float, str]`), so `create_score` raised, caught and
  logged a ValidationError traceback, and emitted no score. Now returns `None`,
  which the SDK filters out cleanly, so the mean is honestly ABSENT. Do not
  "fix" it to 0.0: a mean over zero values is not zero, and `thresholds.json`
  gates on these numbers.
- **`NotApplicable.score_name` vs `Score.name`** — the asymmetry broke
  `{r.name: r for r in results}` the first time the module was used from outside.
  Added a `name` property so callers can treat either uniformly.

### Design decisions worth knowing

- **The photo is deliberately NOT in the LangGraph state.** The callback handler
  logs each node's state as its observation input, so a base64 URI in state would
  be copied onto every node observation — poisoning the very text the layer-B
  text-only judge reads. It is bound into node closures instead, which also
  forces a media re-fetch per run (signed URLs expire).
- **`compose_answer` makes no LLM call.** The verdict is `overall_verdict()` and
  `corrected_copy` is assembled deterministically, so a rewrite cannot re-assert
  a claim the audit just contradicted.
- **`AUDIT_CLAIMS_SEES_PIXELS = True`** is a real fork left as one documented
  constant. True = strongest agent, so any judge delta is a judge property.
  False = the agent is blind downstream of extraction and the proxy judge
  rubber-stamps. Flipping it changes which story a run tells — measure before
  quoting numbers.
- **`listing-cited` is traceability, not grounding.** `cited_listing_id` can only
  echo the audited id. The honest grounding signal is `listing_found`, on the tool
  observation but not in the §4 schema.
- **`closed-vocabulary` has no unique catch** — `validate_attributes()` already
  covers unknown keys, bad enums and unknown appliances, so it and
  `attributes-schema-valid` will move together in the Runs tab. Don't present
  them as independent evidence.
- **No `avg-proxy-photo-consistency` will ever appear in the Runs tab.** Layers B
  and D score server-side after ingestion, so `_mean_evaluator` structurally
  cannot see them. Compare in the UI or via the scores API, and never read the
  absence as zero.

---

## 2. The task: Listing Photo Audit

> Given a property photo and the listing's marketing copy, decide whether each claim
> in the copy is supported by the photo, and produce a corrected description.

Chosen because the image is **load-bearing**. A demo where the photo is decorative
proves nothing — a text-only judge would score it fine and nobody learns anything.
Here, when the copy says *"recently renovated kitchen, floods with natural light"*
and the photo shows a dated galley kitchen with one small window, **no text-only
evaluator can catch it.** That asymmetry is the entire pedagogical payload.

It also sits naturally beside the existing concierge: same catalog, same 14 EU
markets, same listing-id grammar (`[A-Z]{2,4}-\d{3}`), same honest-measurement
culture as `cicd/thresholds.json`.

---

## 3. Architecture

### 3.1 LangGraph trajectory

New graph, `agent/photo_audit_graph.py`. We **do not** rewrite `run_turn()` — the
existing concierge loop stays exactly as it is. This keeps a working demo working
and gives us a clean trajectory to trace.

**IMPLEMENTED** — `agent/photo_audit_graph.py`, entry point `run_photo_audit()`.
Five nodes, as below. (An earlier draft of this diagram also showed an
`ingest_photo` node while the prose said five; the photo is attached by the
wrapper span instead, so there is no such node.)

```
                    ┌─────────────────┐
      START ───────▶│ vision_extract  │  photo → closed-vocabulary attributes
                    └────────┬────────┘  (writes structured TEXT to observation)
                             │
                  confidence │ low
                    ┌────────┴─────────┐
                    ▼                  ▼
        ┌──────────────────┐   ┌─────────────────┐
        │ request_better_   │   │ retrieve_listing│  catalog tool call
        │ photo (terminal)  │   └────────┬────────┘
        └──────────────────┘            ▼
                                ┌─────────────────┐
                                │  audit_claims   │  claim-by-claim adjudication
                                └────────┬────────┘
                                         ▼
                                ┌─────────────────┐
                                │ compose_answer  │──▶ END
                                └─────────────────┘
```

Traced with `langfuse.langchain.CallbackHandler` inside
`propagate_attributes(trace_name="audit-listing-photo", session_id=..., tags=[...])`.

Five nodes, one tool call, one conditional self-correct branch — enough trajectory
to be worth looking at, small enough to fit on a screen during a call.

⚠️ **`propagate_attributes()` is not optional here.** Observation-level evaluators
can only filter on trace attributes (`userId`, `sessionId`, `tags`, `traceName`,
`version`, `metadata`) if those attributes were propagated onto the observations.
Without it the managed judges match nothing and fail silently-empty. This is called
out explicitly in the [LLM-as-a-Judge docs](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)
and it is the most likely way this build wastes an afternoon.

⚠️ **Managed judges see one observation only** — no siblings, no children. So
`vision_extract`'s attribute text and `compose_answer`'s verdict must each be written
onto the observation the judge targets. Design the graph's span I/O for the judge,
not just for humans reading the trace.

### 3.2 Dataset

`multimodal/property-photo-audit` — the `/` puts it in a Langfuse dataset folder,
which also demos folders for free.

**IMPLEMENTED** — `data/photo_scenes.py` (scenes + renderer) and
`scripts/seed_photo_dataset.py` (seeder). `agent/photo_contract.py` §7 is the
authoritative item shape; the example below is kept in sync with it.

Nothing is hand-labelled. `expected_output` is **computed** by
`photo_contract.build_expected_output()` from the same attributes the renderer
drew, so ground truth cannot drift from the fixture. If you ever want to correct
an expected verdict, the scene is wrong, not the label.

```python
scene = {                                    # verbatim from data/photo_scenes.py
    "scene_id": "con-vis-kitchen-dated-dark",
    "listing_id": "BCN-202",                 # a REAL id from agent/catalog.py
    "scene_class": "contradicted_visible",
    "attributes": {                          # COUNTABLE keys only — the render
        "room_type": "kitchen",              # draws exactly these
        "cabinetry": "dark_dated",
        "countertop": "laminate",
        "flooring": "tile",
        "clutter": "moderate",
        "window_count": 1,
        "appliances": ["oven", "fridge"],
    },
    "claims": ["recently renovated", "floods with natural light",
               "stone countertops"],
}

langfuse.create_dataset_item(
    dataset_name="multimodal/property-photo-audit",
    id=scene["scene_id"],                    # stable id -> re-runs UPSERT
    input={
        "listing_id": scene["listing_id"],
        "marketing_copy": marketing_copy(scene["claims"]),
        "claims": scene["claims"],           # the LIST too: the graph adjudicates
                                             # claim-by-claim, and re-parsing them
                                             # out of the prose would make
                                             # claim-coverage a test of the parser
        "photo": LangfuseMedia(content_bytes=render(scene),
                               content_type="image/png"),
    },
    # -> {"verdict": "contradicted",
    #     "claim_verdicts": {"recently renovated": "contradicted",
    #                        "floods with natural light": "contradicted",
    #                        "stone countertops": "contradicted"},
    #     "claim_verdicts_apply": True,
    #     "true_attributes": {...attributes, plus DERIVED
    #                         "condition": "dated",          # not light_modern+stone
    #                         "natural_light": "moderate"}}   # 1 window, not >=2
    expected_output=build_expected_output(
        scene["claims"], scene["attributes"],
        # low_quality scenes only: expected verdict becomes needs_better_photo
        # and claim_verdicts_apply goes False. Without this, an agent that
        # CORRECTLY abstains on an unreadable photo is graded against claim
        # verdicts it had no fair way to see, and marked wrong.
        unreadable=scene["scene_class"] == "low_quality"),
    metadata={"scene_class": scene["scene_class"],
              "scene_id": scene["scene_id"],
              "photo_provenance": PROVENANCE_RENDERED},
)
```

~20 items. Composition is the experiment design, not filler:

| Class | n | Purpose |
|---|---|---|
| `supported` — copy matches photo | 6 | control; catches over-eager auditing |
| `contradicted-visible` — contradiction plainly in frame | 6 | every layer should catch these |
| `contradicted-subtle` — contradiction needs attention to detail | 4 | **separates vision judge from proxy judge** |
| `unverifiable` — copy claims something the photo can't show (e.g. "quiet street") | 3 | correct answer is abstention, not a verdict |
| `low-quality photo` — dark/blurred | 2 | exercises the `request_better_photo` branch |

⚠️ **Photo licensing.** This is a public repo. Do not commit scraped listing photos.
Options, in order of preference: (a) generate the set with an image model and commit
with a `data/photos/PROVENANCE.md` recording prompt + model + date; (b) CC0 sources
with per-file attribution. Decide before building — retrofitting provenance is worse
than choosing now.

⚠️ **CSV import is text/JSON only.** Media items must come from the SDK seeder or the
UI item editor.

### 3.3 The four eval layers

All four run on the same dataset, so the comparison is apples to apples.

**A. Vision judge — SDK, sees pixels.** `evaluators/vision_judges.py`

```python
def judge_photo_copy_consistency(*, input, output, expected_output, **kwargs):
    photo = input["photo"]                    # LangfuseMediaReference
    data_uri = photo.fetch_data_uri()
    verdict = call_vision_model(data_uri, output["verdict"], input["marketing_copy"])
    return Evaluation(name="photo-copy-consistency", value=verdict.score,
                      comment=verdict.reasoning)
```

Two of these: `photo-copy-consistency` (is the verdict right, given the photo) and
`extraction-fidelity` (do `vision_extract`'s attributes actually describe the photo).

**B. Metadata-proxy judge — managed, text-only.** Provisioned in Langfuse via the
unstable evaluators API (extending `scripts/seed_managed_evaluators.sh`, which
already does exactly this on the Cloud branch). Targets the `audit_claims`
observation and maps `vision_extract`'s **attribute text** — never the image — into
`{{context}}`. This is the path a customer's non-engineer can configure in the UI,
so it must be in the demo.

**C. Code evaluators — deterministic.** Extends `agent/scoring.py`:
`attributes-schema-valid`, `claim-coverage` (every claim in the copy adjudicated),
`listing-cited`, `closed-vocabulary` (no invented attribute keys), `abstains-when-
unverifiable`.

**D. The anti-pattern exhibit.** One managed judge named
`ANTIPATTERN-photo-judge-raw-media` mapped straight at the observation input holding
the media token. It exists to be opened on stage and shown returning a score on
`@@@langfuseMedia:...@@@`. Name it so nobody mistakes it for a working evaluator, and
say so in `README.md` — an unlabelled broken judge in a public repo is a liability.

### 3.4 The claim we are actually testing

Stated up front so the result can falsify it, per the discipline already in
`cicd/thresholds.json`:

> **Hypothesis.** On `contradicted-visible`, the vision judge and the metadata-proxy
> judge agree. On `contradicted-subtle`, the proxy judge degrades — and it degrades
> *specifically* where `vision_extract` failed to surface the relevant attribute.
> The anti-pattern judge is uncorrelated with ground truth throughout.

If that holds, the finding is sharper than "use the proxy":

> **The metadata-proxy approach does not degrade gracefully. It degrades exactly at
> the extractor's blind spots — and those blind spots are invisible in the score.**
> The proxy judge cannot report what the extractor never wrote down. So gate on the
> code evaluators and the vision judge; treat the proxy judge as a smoke alarm.

That is a reusable argument for any customer who wants to caption-then-judge their
way around a multimodal eval gap. **Run the control** — same prompt, twice — before
citing any judge delta. The existing demo's own measurement log is a record of that
lesson being learned the hard way; do not re-learn it.

### 3.5 Annotations → dataset

1. Reviewer works an annotation queue over `audit-listing-photo` traces (queues accept
   traces, observations **and** sessions), scoring a `photo-audit-verdict` score config.
2. On disagreements they add a **Correction** — the copy the agent should have
   produced. Corrections are scores with `dataType: "CORRECTION"`, `name: "output"`.
3. `scripts/promote_corrections.py` pulls them and writes new dataset items with
   `source_trace_id` / `source_observation_id` for lineage.

⚠️ **Corrections have no SDK read path yet** — the docs say "Coming soon" for both
Python and TS. Only `GET /api/public/v3/scores?dataType=CORRECTION&fields=subject,details`
works. Use `agent/config.py`'s existing `langfuse_api` helper rather than the SDK.

✅ **Verified (§1a).** A media token copied verbatim out of a production observation
into a dataset item **does** hydrate into a `LangfuseMediaReference`, and its bytes
resolve. `promote_corrections.py` can therefore pass the observation's stored `photo`
field straight through — no re-upload, and dedup means no extra storage. Trace
lineage (`source_trace_id` / `source_observation_id`) survives the round trip.

### 3.6 Experiment comparison + CI gate

`scripts/run_photo_experiment.py --model {claude-sonnet-4-6,gpt-4o}` — two runs, one
dataset, side-by-side in the compare view. Mirrors the existing
`scripts/run_experiment.py`; reuse `make_task`'s shape.

Gate: extend `cicd/thresholds.json` with a `photo_audit` block. Same philosophy —
**hard on code evaluators, loose on judges.** Thresholds must be *measured*, not
guessed: run each arm twice before writing a number, and record the repeat columns in
the comment block the way the existing file does.

⚠️ **Cost.** Every vision judge call sends an image. Two judges × 20 items × 2 arms =
80 image-bearing calls per full sweep, and the gate re-runs on every dispatch. Cap the
CI gate at the 8-item `contradicted-*` subset and note the omission in the run log —
silent truncation reads as full coverage.

---

## 4. Prerequisites and risks

| # | Item | Note |
|---|---|---|
| 1 | ~~**SDK bump** `langfuse>=3.0,<4.0` → `>=4.10,<5.0`~~ | ✅ **DONE** — resolved to 4.14.4. 3 edits in `agent/concierge.py`; `config.py`/`prompts.py` untouched. Smoke test, a live turn, and the experiment path all verified green (§1a). |
| 1b | **Python ≥ 3.10** | langfuse ≥ 4.0 will not install on 3.9. Existing venv is 3.11 ✅; system `python3` is 3.9.6 ⚠️ — pin the interpreter in setup docs. |
| 2 | New deps | `langgraph`, `langchain-anthropic`, **and the `langchain` meta-package** — `CallbackHandler` imports `langchain` directly (§1a). |
| 3 | **UI experiments won't work** on this dataset | Every run must be SDK-driven. Say so in `README.md` before someone clicks *Start Experiment* on a customer call. |
| 4 | **Signed URLs expire** | Long runs can outlive them; re-fetch the dataset. Relevant at 20 items × vision latency. |
| 5 | Cloud media storage | Free "currently"; Langfuse reserves the right to introduce a pricing metric. Don't promise a customer it's free forever. |
| 6 | Self-hosted parity | [docker-compose.yaml:18](../../docker-compose.yaml:18) already wires `LANGFUSE_S3_MEDIA_UPLOAD_*` to MinIO on the host-reachable `http://localhost:9190`, so tracing should work on-prem. Code evaluators additionally need a dispatcher. Untested for this task. |
| 7 | Judge debug traces | Filter environment `langfuse-llm-as-a-judge` / `langfuse-code-eval` — hidden from the default view. |
| 8 | `gpt-4o` cost | Not in the price list; `agent/llm.py` already emits explicit `cost_details`. Extend for vision-token pricing or cost shows $0. |

### Spike — ✅ done, all passed (§1a)

1. ~~Media dataset item hydrates to `LangfuseMediaReference`~~ **PASS**
2. ~~Vision evaluator sees pixels; score lands on the dataset run~~ **PASS**
3. ~~Trace → dataset promotion hydrates~~ **PASS**
4. ~~`propagate_attributes` reaches LangGraph node observations~~ **PASS**
5. ~~v3→v4 migration scope~~ **PASS (audited, not applied — repo untouched)**

The go/no-go was steps 1–3 and all three passed, so the full four-layer demo is on —
no fallback to metadata-proxy-only. Remaining risk is execution, not feasibility.

✅ **Ported** — the spike is now [scripts/verify_multimodal.py](scripts/verify_multimodal.py),
11 assertions, CI-ready. Run it before any multimodal demo.

---

## 5. Build order

| Phase | Deliverable |
|---|---|
| 0 | ✅ **Complete** — spike passed (§1a), SDK bumped to v4 and demo verified green, `scripts/verify_multimodal.py` committed |
| 1 | ✅ **Done** — `data/photo_scenes.py` (21 self-checking scenes, stdlib renderer) + `scripts/seed_photo_dataset.py` |
| 2 | ✅ **Done** — `agent/photo_audit_graph.py`; verified trace shape (§1b) |
| 3 | ✅ **Done** — layer C (7 code evaluators) + layer A (2 vision judges); experiment run lands all 9 scores + 9 means |
| 4 | Managed proxy judge + anti-pattern exhibit (layers B, D) |
| 5 | Measurement pass — both arms **twice**; write real numbers into `thresholds.json` |
| 6 | Annotation queue + `promote_corrections.py` |
| 7 | CI gate block; `DEMO_SCRIPT.md` act + `AI_ENGINEERING_LOOP.md` update |

## 6. Demo beat (≈8 min)

1. **Trace** — open an `audit-listing-photo` trace: photo renders inline, five graph
   nodes, the conditional branch. *"This is the trajectory, and the image is in it."*
2. **The trap** — open `ANTIPATTERN-photo-judge-raw-media`. It scored
   `@@@langfuseMedia:...@@@` with confidence. *"Multi-modal tracing does not give you
   multi-modal evals. Nothing errored."*
3. **The fix** — the SDK vision judge on the same item, with reasoning that cites what
   is actually in the photo.
4. **The pragmatic middle** — the managed proxy judge scoring extracted attributes.
   Works in the UI, no code. Then show a `contradicted-subtle` item where it misses,
   and *why*: the extractor never wrote the attribute down.
5. **Close the loop** — reviewer disagrees → correction → new dataset item → gate goes
   red on the next run.
