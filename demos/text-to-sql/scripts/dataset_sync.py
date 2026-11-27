"""Dataset reconciliation shared by the text-to-sql seeders and the readiness check.

One rule decides whether a dataset item is in place, so `seed_all.py` and `verify_demo.py`
can never disagree: an item is in place when an ACTIVE row matches its identity and
carries the same input, expected output and metadata. Identity is the stable item id
when the definition has one, otherwise `input.question` (the per-step dataset's curated
items predate stable ids, and switching now would duplicate every existing row).

Standard library only. `lf` is any client with `get(path, **params)` and `post(path, body)`
(verify_demo.Langfuse in practice), so the tests drive it with a fake.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_PAGES = 50       # 50 pages of 100 items: far above any dataset these seeders own
PAGE_SIZE = 100


def find_dataset(lf: Any, name: str, max_pages: int = 20) -> Optional[dict]:
    """The dataset row from the paged v2 list, or None. Listing avoids putting a name such
    as `text-to-sql/critic-accuracy` into a URL path, where the slash would be ambiguous."""
    for page in range(1, max_pages + 1):
        d = lf.get("/api/public/v2/datasets", page=page, limit=PAGE_SIZE) or {}
        for row in d.get("data") or []:
            if row.get("name") == name:
                return row
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            return None
    raise RuntimeError(f"dataset list did not end within {max_pages} pages")


def list_items(lf: Any, dataset: str) -> List[dict]:
    """Every item of `dataset`, ACTIVE and ARCHIVED, following page numbers to the end."""
    rows: List[dict] = []
    for page in range(1, MAX_PAGES + 1):
        d = lf.get("/api/public/dataset-items", datasetName=dataset, page=page, limit=PAGE_SIZE) or {}
        rows += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            return rows
    raise RuntimeError(f"dataset {dataset}: items did not end within {MAX_PAGES} pages")


def _question(row: dict) -> Optional[str]:
    inp = row.get("input")
    return inp.get("question") if isinstance(inp, dict) else None


def find_row(spec: dict, rows: List[dict]) -> Optional[dict]:
    """The existing row that is this definition: by stable id, or else by question.

    Matching by question skips rows minted from traces (`metadata.linked_from`), which are
    not the curated definition. An ACTIVE match wins over an ARCHIVED one, so a seeded item
    that was archived is brought back rather than duplicated."""
    if spec.get("id"):
        return next((r for r in rows if r.get("id") == spec["id"]), None)
    q = spec["input"].get("question")
    candidates = [r for r in rows
                  if _question(r) == q and not ((r.get("metadata") or {}) if isinstance(r.get("metadata"), dict)
                                                else {}).get("linked_from")]
    active = [r for r in candidates if r.get("status", "ACTIVE") == "ACTIVE"]
    return (active or candidates or [None])[0]


def in_place(spec: dict, row: Optional[dict]) -> bool:
    """Whether `row` is this definition, ACTIVE and unchanged."""
    return (row is not None
            and row.get("status", "ACTIVE") == "ACTIVE"
            and row.get("input") == spec["input"]
            and row.get("expectedOutput") == spec["expectedOutput"]
            and (row.get("metadata") or {}) == (spec.get("metadata") or {})
            and all(row.get(k) == spec[k] for k in ("sourceTraceId", "sourceObservationId") if spec.get(k)))


def usable(specs: List[dict], rows: List[dict]) -> Tuple[int, List[str]]:
    """How many definitions are in place, and the labels of those that are not."""
    missing = [s["label"] for s in specs if not in_place(s, find_row(s, rows))]
    return len(specs) - len(missing), missing


def ensure_dataset(lf: Any, name: str, description: str, metadata: dict, dry_run: bool) -> str:
    """created / updated / unchanged for the dataset row itself (description and metadata)."""
    row = find_dataset(lf, name)
    if row is not None and row.get("description") == description and (row.get("metadata") or {}) == metadata:
        return "unchanged"
    if not dry_run:
        lf.post("/api/public/v2/datasets", {"name": name, "description": description, "metadata": metadata})
    return "created" if row is None else "updated"


Result = Tuple[str, str, str]  # (created | updated | unchanged | failed, label, error)


def reconcile_items(lf: Any, dataset: str, specs: List[dict], dry_run: bool,
                    rows: Optional[List[dict]] = None) -> List[Result]:
    """Write only what differs, one item at a time, so one failed write is reported as
    that item's failure and the rest still run. A changed row is upserted by its own id
    (keeping any trace link it has); a missing one is created with the definition's id."""
    rows = list_items(lf, dataset) if rows is None else rows
    out: List[Result] = []
    for spec in specs:
        row = find_row(spec, rows)
        if in_place(spec, row):
            out.append(("unchanged", spec["label"], ""))
            continue
        body: Dict[str, Any] = {"datasetName": dataset, "input": spec["input"],
                                "expectedOutput": spec["expectedOutput"],
                                "metadata": spec.get("metadata") or {}, "status": "ACTIVE"}
        item_id = (row or {}).get("id") or spec.get("id")
        if item_id:
            body["id"] = item_id
        for k in ("sourceTraceId", "sourceObservationId"):
            if spec.get(k):
                body[k] = spec[k]
        try:
            if not dry_run:
                lf.post("/api/public/dataset-items", body)
        except Exception as e:  # noqa: BLE001 - reported per item, counted as a failure
            out.append(("failed", spec["label"], str(e)))
            continue
        out.append(("created" if row is None else "updated", spec["label"], ""))
    return out


def sync(lf: Any, name: str, description: str, metadata: dict, specs: List[dict],
         dry_run: bool, report: Callable[[str, str], None]) -> int:
    """Reconcile one dataset and its items; print through `report`; return the failure count."""
    failures = 0
    try:
        result = ensure_dataset(lf, name, description, metadata, dry_run)
    except Exception as e:  # noqa: BLE001
        report("failed", f"dataset {name}: {e}")
        return 1 + len(specs)
    report(result, f"dataset {name}")
    try:
        rows = [] if (dry_run and result == "created") else list_items(lf, name)
        results = reconcile_items(lf, name, specs, dry_run, rows)
    except Exception as e:  # noqa: BLE001
        report("failed", f"items of {name}: {e}")
        return 1 + failures
    for status, label, err in results:
        report(status, f"item {label} in {name}" + (f": {err}" if err else ""))
        failures += status == "failed"
    return failures
