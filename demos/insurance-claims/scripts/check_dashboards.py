"""Run every dashboard widget's query through the Metrics API and report empty or rejected ones.
A widget can be created and still render nothing; this is the check.   .venv/bin/python scripts/check_dashboards.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import seed_dashboards as sd  # noqa: E402

from claims import config  # noqa: E402


def main() -> int:
    bad = 0
    for board in (sd.VALUE, sd.REPORT):
        print("==", board[0])
        for (title, _, view, chart, dims, metrics, filters, *_) in board[2]:
            q = {"view": view, "dimensions": dims, "filters": filters,
                 "metrics": [{"measure": m["measure"], "aggregation": m["agg"]} for m in metrics],
                 "fromTimestamp": "2026-10-01T00:00:00Z", "toTimestamp": "2026-12-31T00:00:00Z"}
            if chart.endswith("TIME_SERIES"):
                q["timeDimension"] = {"granularity": "hour"}
            try:
                rows = config.api("GET", "/api/public/v2/metrics", params={"query": json.dumps(q)}).get("data") or []
                bad += not rows
                print(f"  {'OK   ' if rows else 'EMPTY'} {title:<45} rows={len(rows):<3} {json.dumps(rows[0])[:120] if rows else ''}")
            except Exception as e:  # noqa: BLE001
                bad += 1
                print(f"  ERR   {title:<45} {str(e)[:200]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
