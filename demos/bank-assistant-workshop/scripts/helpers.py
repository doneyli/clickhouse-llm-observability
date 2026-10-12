"""Cloud actions are explicit presenter/learner commands, never build side effects."""
import hashlib
import json
import math
from datetime import datetime, timezone
from northwind import config, prompts

DATASET_NAME = f"bank-workshop/{config.WORKSHOP_ID}/regression-v1"


def publish_prompt(args):
    if not args.label.startswith("workshop-"):
        raise ValueError("Use a workshop- label; this helper cannot move production/staging labels.")
    text = prompts.V1_BASELINE if args.variant == "baseline" else prompts.WORKSHOP_CANDIDATE
    prompt = config.require_langfuse().create_prompt(name=prompts.PROMPT_NAME, type="text", prompt=text,
                                                    labels=[args.label], config={"variant": args.variant})
    print(f"Published {prompts.PROMPT_NAME} v{prompt.version}; label={args.label}. Repeating creates a new version.")


def load_data():
    return json.loads((config.ROOT / "data/seed-dataset.json").read_text())


def seed_dataset(args):
    allow_draft = getattr(args, "allow_draft", False)
    if not args.reviewed and not allow_draft:
        raise ValueError("Review data/seed-dataset.json references against the synthetic KB, then pass --reviewed.")
    data = load_data()
    if allow_draft and any(item["metadata"].get("synthetic") is not True for item in data):
        raise ValueError("--allow-draft is limited to synthetic rehearsal examples.")
    if not allow_draft and any(item["metadata"].get("reference_review_required", True) for item in data):
        raise ValueError("Mark each item's reference_review_required=false only after reviewing its expected behavior.")
    lf = config.require_langfuse()
    try:
        lf.get_dataset(DATASET_NAME)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        lf.create_dataset(name=DATASET_NAME, description="Synthetic banking policy and refusal regression examples.",
                          metadata={"workshop": config.WORKSHOP_ID, "synthetic": True,
                                    "reference_review_required": any(item["metadata"].get("reference_review_required", True) for item in data)})
    for item in data:
        identifier = hashlib.sha256(f"{DATASET_NAME}/{item['id']}".encode()).hexdigest()[:32]
        lf.create_dataset_item(dataset_name=DATASET_NAME, id=identifier, input=item["input"],
                               expected_output=item["expected_output"], metadata=item["metadata"])
    print(f"Upserted {len(data)} items into {DATASET_NAME}. Unlisted remote items are not deleted.")
    if allow_draft:
        print("Synthetic rehearsal only. Human-review markers are preserved; these references are not production ground truth.")


def required_scores(item, *, include_judge):
    """The teaching contract, including callbacks that legitimately skip an item."""
    metadata = item.metadata if item.metadata is not None else {}
    if not isinstance(metadata, dict):
        raise ValueError(f"Item {item.id}: metadata must be an object.")
    for field in ("must_include", "expected_sources"):
        values = metadata.get(field)
        values = [] if values is None else values
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(f"Item {item.id}: {field} must be a list of non-empty strings.")
    if "expect_blocked" in metadata and not isinstance(metadata["expect_blocked"], bool):
        raise ValueError(f"Item {item.id}: expect_blocked must be boolean.")
    names = {"language-match"}
    if metadata.get("must_include"):
        names.add("must-include")
    if metadata.get("expected_sources"):
        names.add("cites-expected-source")
    if metadata.get("expect_blocked"):
        names.add("refused-safely")
    if include_judge and item.expected_output:
        names.add("correctness")
    return names


def validate_experiment(result, required):
    """SDK task success alone does not establish linkage or score coverage."""
    completed = [item.item.id for item in result.item_results]
    failed = set(required) - set(completed)
    unexpected = set(completed) - set(required)
    duplicates = len(completed) - len(set(completed))
    missing_linkage = [item.item.id for item in result.item_results
        if not result.dataset_run_id or item.dataset_run_id != result.dataset_run_id]
    missing_scores = []
    for item in result.item_results:
        for name in required.get(item.item.id, set()):
            matches = [score for score in item.evaluations if score.name == name]
            if (len(matches) != 1 or not isinstance(matches[0].value, (bool, int, float))
                    or not math.isfinite(matches[0].value)):
                missing_scores.append(f"{item.item.id}:{name}")
    if failed or unexpected or duplicates or missing_linkage or missing_scores or not result.dataset_run_id:
        raise RuntimeError(
            f"Incomplete experiment: {len(failed)} task errors, {len(unexpected)} unexpected items, "
            f"{duplicates} duplicate results, {len(missing_linkage)} missing/inconsistent dataset-run links, "
            f"{len(missing_scores)} missing/invalid scores ({', '.join(sorted(missing_scores)) or 'none'}). "
            "Inspect and rerun.")


def run_experiment(args):
    from northwind import experiment_hooks
    if args.max_concurrency < 1:
        raise ValueError("--max-concurrency must be at least 1.")
    lf = config.require_langfuse()
    # Strict resolution, then freeze this exact version for every task. App
    # fallback is useful for serving, but invalidates a prompt comparison.
    prompt = lf.get_prompt(prompts.PROMPT_NAME, label=args.prompt_label, type="text", cache_ttl_seconds=0)
    if prompt.is_fallback:
        raise RuntimeError("Refusing to experiment on a fallback prompt.")
    version = datetime.fromisoformat(args.dataset_version.replace("Z", "+00:00")) if args.dataset_version else None
    if version is not None and version.tzinfo is None:
        raise ValueError("--dataset-version must include a timezone, for example 2026-10-11T15:00:00Z.")
    options = {"version": version} if version else {}
    dataset = lf.get_dataset(DATASET_NAME, **options)
    # The pinned SDK forwards every fetched item, including ARCHIVED ones.
    # Filter this local snapshot before execution and validate the same set.
    dataset.items = [item for item in dataset.items if getattr(item, "status", "ACTIVE") == "ACTIVE"]
    if not dataset.items:
        raise ValueError("The dataset has no ACTIVE items; review and seed it before experimenting.")
    required = {item.id: required_scores(item, include_judge=not args.no_judge) for item in dataset.items}
    model = args.model or config.AGENT_MODEL
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_name = args.run_name or f"{args.prompt_label}-v{prompt.version}-{now}"
    result = dataset.run_experiment(
        name="Bank assistant workshop", run_name=run_name,
        task=experiment_hooks.make_task(prompt_version=prompt.version, prompt_label=args.prompt_label, model=model),
        evaluators=experiment_hooks.evaluators(include_judge=not args.no_judge),
        max_concurrency=args.max_concurrency,
        metadata={"prompt_version": str(prompt.version), "prompt_label": args.prompt_label,
                  "model": model, "judge_model": config.JUDGE_MODEL, "release": config.RELEASE,
                  "dataset_version": args.dataset_version or "latest at fetch"},
    )
    print(result.format())
    if getattr(result, "dataset_run_url", None):
        print(result.dataset_run_url)
    validate_experiment(result, required)
    return result
