"""Write the derived banking-compliance headline; optionally run as a worker.

Run after managed judges finish, or keep running with --watch. No model calls.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402
from northwind.compliance_sync import sync_recent  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=24, help="Replay window; increase for late/backfilled evaluations")
    ap.add_argument("--watch", action="store_true", help="Poll continuously; otherwise reconcile once")
    ap.add_argument("--interval", type=float, default=30)
    a = ap.parse_args()
    if a.hours <= 0 or a.interval < 5:
        ap.error("--hours must be positive and --interval at least 5 seconds")
    if not config.LANGFUSE_PUBLIC_KEY or not config.LANGFUSE_SECRET_KEY:
        ap.error("Configure the bank demo's .env/.env.cloud Langfuse keys first")
    while True:
        try:
            result = sync_recent(config.api, hours=a.hours)
            print(result, flush=True)
        except Exception as exc:
            if not a.watch:
                raise
            print(f"Compliance sync failed ({type(exc).__name__}); retrying next interval", file=sys.stderr, flush=True)
        if not a.watch:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
