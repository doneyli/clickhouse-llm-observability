"""
Seed the evaluation datasets (idempotent: item ids are stable, re-running upserts).

  claims-golden-v1       92 claims, stratified line × state × rule type. input = claim id + the claim file a
                         perfect intake would produce; expected_output = the oracle (rule ids, critical rule ids,
                         decision, amounts, interest rule, letter requirements, commission) + the true extraction
  claims-adversarial-v1  12 claims: instructions embedded in documents, disagreeing dates, missing certificate,
                         sanctions hit, pressure — expected PEND or REFER_SIU
  claims-intake-v1       24 claims with their scanned documents attached as Langfuse media (multi-modal items);
                         expected_output = the fields actually drawn on the documents

    .venv/bin/python scripts/seed_datasets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langfuse.media import LangfuseMedia  # noqa: E402

from claims import claimsdb, config  # noqa: E402

GOLDEN, ADVERSARIAL, INTAKE = "claims-golden-v1", "claims-adversarial-v1", "claims-intake-v1"
SERVICE = "annuity-service-golden-v1"


def item_for(c: dict) -> tuple[dict, dict, dict]:
    claim_file, _ = claimsdb.ground_truth_inputs(c)
    inp = {"claim_id": c["claim_id"], "line": c["line"], "state": c["state"], "claim_file": claim_file}
    exp = {**c["expected"], "extraction": claimsdb.true_extraction(c)}
    meta = {"scenario": c["scenario"], "line": c["line"], "state": c["state"], "tags": c.get("tags") or [],
            "n_rules": len(c["expected"]["rule_ids"]), "has_critical": bool(c["expected"]["critical_rule_ids"]),
            "decision": c["expected"]["decision"]}
    return inp, exp, meta


def main() -> int:
    lf = config.get_langfuse()
    claims = list(claimsdb.load().values())
    specs = [
        (GOLDEN, "Death claims with SME-codified expected outcomes (oracle). Gate dataset for claims-sop-rules.",
         [c for c in claims if c["_set"] == "golden"]),
        (ADVERSARIAL, "Manipulation and fraud-indicator claims: embedded instructions, disagreeing dates, "
                      "missing proof, sanctions. Expected PEND or REFER_SIU.",
         [c for c in claims if c["_set"] == "adversarial"]),
    ]
    for name, desc, items in specs:
        lf.create_dataset(name=name, description=desc, metadata={"demo": "insurance-claims", "source": "oracle"})
        for c in items:
            inp, exp, meta = item_for(c)
            lf.create_dataset_item(dataset_name=name, id=f"{name}-{c['claim_id']}", input=inp,
                                   expected_output=exp, metadata=meta)
        print(f"✓ {name}: {len(items)} items")

    # Retirement / annuities: in-force service requests (same oracle, same evaluators).
    lf.create_dataset(name=SERVICE, description="Annuity service requests (withdrawals, surrenders, replacements) "
                                                "with SME-codified expected outcomes. Gate dataset for "
                                                "annuity-service-rules.", metadata={"demo": "insurance-claims",
                                                                                     "use_case": "annuities"})
    svc = list(claimsdb.load_service().values())
    for r in svc:
        rf, _ = claimsdb.service_request_file(r)
        lf.create_dataset_item(dataset_name=SERVICE, id=f"{SERVICE}-{r['request_id']}",
                               input={"request_id": r["request_id"], "request_file": rf}, expected_output=r["expected"],
                               metadata={"scenario": r["scenario"], "request_type": r["request_type"],
                                         "state": r["state"], "tags": r.get("tags") or []})
    print(f"✓ {SERVICE}: {len(svc)} items")

    # Multi-modal: the scanned documents themselves, as media on the dataset item.
    lf.create_dataset(name=INTAKE, description="Scanned claim documents (images) with the field values drawn on "
                                               "them. Experiments: doc-intake field accuracy.",
                      metadata={"demo": "insurance-claims", "multimodal": "true"})
    picked = [c for c in claims if c["_set"] == "golden"][::4][:20] + \
             [c for c in claims if c["_set"] == "adversarial"][:4]
    for c in picked:
        docs = [{"doc_type": t, "name": p.name, "content": LangfuseMedia(content_bytes=p.read_bytes(),
                                                                         content_type="image/png")}
                for t, p in claimsdb.document_paths(c)]
        lf.create_dataset_item(dataset_name=INTAKE, id=f"{INTAKE}-{c['claim_id']}",
                               input={"claim_id": c["claim_id"], "documents": docs},
                               expected_output={"extraction": claimsdb.true_extraction(c)},
                               metadata={"scenario": c["scenario"], "tags": c.get("tags") or []})
    print(f"✓ {INTAKE}: {len(picked)} items (documents as media)")
    config.flush()
    print(f"Datasets: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/datasets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
