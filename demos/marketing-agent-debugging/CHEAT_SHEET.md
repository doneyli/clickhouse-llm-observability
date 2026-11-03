# Debugging an agent in Langfuse: cheat sheet

## Start from the complaint, not the trace

| You know… | Type in the search bar |
|---|---|
| the customer | `metadata.customer:contoso` |
| the person | `user:dana` (pick from autocomplete) |
| it was rejected | `scores.reviewer-decision:rejected` |
| it was slow | `latency:>60` (seconds) |
| a tool misbehaved | `type:TOOL name:fetch_page level:(ERROR OR WARNING)` |
| it ran too long | `scores.agent-steps:>=8` |
| a policy was broken | `scores.publishing-policy:false` |
| some words in the answer | `output:"which price"` |

Don't know the field names? Click **Ask AI** and describe it. Use something every day?
**Save the view.** Spike on the chart above the table? **Drag across it** to zoom in.

## Read a run: see → think → do → got back

1. **Tree / Graph / Timeline / Log.** Graph (Aggregated) shows the shape and repeats
   (`tool (9/9)`); Timeline shows where the time went; Log view + Ctrl-F searches
   the whole run.
2. **A generation (`plan-next-step`)** is one decision:
   - **Prompt link:** which instructions version was live.
   - **Available tools:** the menu it chose from.
   - **Thinking:** *why* it chose.
   - **Tool calls:** *what* it chose, with arguments.
3. **A tool or retriever** shows what came back. For the retriever, **output** is what
   the model saw and **metadata** is what it didn't (dates, status, scores).
4. **The next generation's input** is everything the model knew before its next choice.

## The four questions

| Question | Look at |
|---|---|
| Why did it do that? | thinking + tool calls on the generation, and the prompt version |
| Did it retrieve the right info? | retriever output vs. metadata; does the fact-checker see the same thing? |
| Why is it slow or expensive? | Timeline (long bars, back-to-back calls), tokens per generation, cache reads |
| Why did it loop, stop or skip? | Graph counters/cycles, guardrail output, instructions diff between versions |

## Who fixes it

- **Context or data** (stale, missing, wrong document): hand off with the retriever link.
- **Instructions** (missing rule, bad rule): **you can fix it.** In Prompt Management,
  create a new version, then move the `production` label. Label = deploy.
- **Tool contract or infrastructure** (never finishes, slow, returns junk): hand off
  with the tool outputs and the timeline.
- **Model judgment** (inconsistent choices on the same input): compare two runs side
  by side, then turn it into a dataset item and an eval.

## Hand it off well

Comment on the exact observation (and @mention someone) · label `root-cause` in the
annotation queue · add the run to the `regression-cases` dataset · share the trace link.
