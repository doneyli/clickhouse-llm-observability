"""Regression tests for pending SME work, without a live Langfuse project."""
import contextlib
import io
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR / "scripts"))
sys.path.insert(0, str(DEMO_DIR))
import fill_annotation_queue as fill  # noqa: E402


def item(oid, status="COMPLETED"):
    return {"id": f"item-{oid}", "objectId": oid, "objectType": "OBSERVATION", "status": status}


class QueueApi:
    """Model pagination and unique queue items; reject all score/review writes."""
    def __init__(self, items=(), observations=(), scores=()):
        self.items = deepcopy(list(items))
        self.observations = list(observations)
        self.scores = list(scores)
        self.calls = []

    def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, deepcopy(body), deepcopy(params)))
        if path == "/api/public/annotation-queues":
            return {"data": ([{"id": "other", "name": "Other queue"}] if params["page"] == 1
                             else [{"id": "sme", "name": fill.QUEUE}]), "meta": {"totalPages": 2}}
        if path == "/api/public/annotation-queues/sme/items":
            if method == "GET":
                start = (params["page"] - 1) * params["limit"]
                return {"data": deepcopy(self.items[start:start + params["limit"]]),
                        "meta": {"totalPages": max(1, (len(self.items) + 99) // 100)}}
            if method == "POST":
                assert body["status"] == "PENDING"
                assert body["objectId"] not in {i["objectId"] for i in self.items}
                created = item(body["objectId"], body["status"])
                self.items.append(created)
                return deepcopy(created)
        if method == "GET" and path in ("/api/public/v2/observations", "/api/public/v3/scores"):
            rows = self.observations if "observations" in path else [
                s for s in self.scores if "name" not in params or s.get("name") in params["name"].split(",")]
            start = int(params.get("cursor", 0))
            end = start + params["limit"]
            return {"data": rows[start:end], "meta": {"cursor": str(end) if end < len(rows) else None}}
        raise AssertionError(f"Unexpected API call: {method} {path}")


class PendingBacklog(unittest.TestCase):
    def run_fill(self, api, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with (mock.patch.object(fill.config, "api", api),
              mock.patch.object(fill.config, "project_id", return_value="project"),
              contextlib.redirect_stdout(out), contextlib.redirect_stderr(err)):
            result = fill.main(list(argv))
        return result, out.getvalue(), err.getvalue()

    def test_all_completed_gets_twenty_pending_and_rerun_preserves_reviews(self):
        completed = [item(f"reviewed-{i}") for i in range(10)]
        api = QueueApi(completed, [{"id": f"fresh-{i}"} for i in range(30)])
        result, out, err = self.run_fill(api)
        self.assertEqual((result, err), (0, ""))
        self.assertIn("20 pending / 10 completed / 30 total", out)
        self.assertEqual(api.items[:10], completed)
        api.calls.clear()
        self.assertEqual(self.run_fill(api)[0], 0)
        self.assertTrue(all(method == "GET" for method, *_ in api.calls))
        self.assertFalse(any("observations" in path or "scores" in path for _, path, *_ in api.calls))
        api.items[10]["status"] = "COMPLETED"
        api.calls.clear()
        self.assertEqual(self.run_fill(api)[0], 0)
        self.assertEqual(sum(method == "POST" for method, *_ in api.calls), 1)
        self.assertEqual(sum(i["status"] == "COMPLETED" for i in api.items), 11)

    def test_follows_pages_and_cursors_past_reviewed_answers(self):
        completed = [item(f"reviewed-{i}") for i in range(101)]
        pending = [item(f"waiting-{i}", "PENDING") for i in range(2)]
        observations = [{"id": i["objectId"]} for i in completed + pending] + [{"id": "fresh"}]
        api = QueueApi(completed + pending, observations)
        self.assertEqual(self.run_fill(api, ["3"])[0], 0)
        self.assertEqual(api.items[:-1], completed + pending)
        self.assertEqual(api.items[-1], item("fresh", "PENDING"))
        self.assertTrue(any(params.get("cursor") == "100" for _, _, _, params in api.calls if params))

    def test_prioritizes_low_judge_scores_and_excludes_child_observations(self):
        scores = [{"name": "other", "value": 1.0} for _ in range(100)] + [
            {"name": "faithfulness", "value": 0.0, "subject": {"kind": "observation", "id": "bad"}},
            {"name": "banking-compliance", "value": 0.5, "subject": {"kind": "observation", "id": "bad"}}]
        api = QueueApi(observations=[{"id": "good"}, {"id": "bad"},
                                     {"id": "child", "parentObservationId": "bad"}], scores=scores)
        self.assertEqual(self.run_fill(api, ["1"])[0], 0)
        self.assertEqual(api.items, [item("bad", "PENDING")])

    def test_thumbs_down_answer_is_queued_first(self):
        # user-feedback is a TRACE score; the queue item is that trace's assistant observation.
        scores = [{"name": "user-feedback", "value": 1, "subject": {"kind": "trace", "id": "t-up"}},
                  {"name": "user-feedback", "value": 0, "subject": {"kind": "trace", "id": "t-down"}},
                  {"name": "faithfulness", "value": 1.0, "subject": {"kind": "observation", "id": "down"}}]
        api = QueueApi(observations=[{"id": "up", "traceId": "t-up"}, {"id": "plain", "traceId": "t-plain"},
                                     {"id": "down", "traceId": "t-down"}], scores=scores)
        self.assertEqual(self.run_fill(api, ["1"])[0], 0)
        self.assertEqual(api.items, [item("down", "PENDING")])

    def test_flagged_answer_older_than_the_newest_page_is_found(self):
        observations = [{"id": f"fresh-{i}", "traceId": f"t{i}"} for i in range(150)]
        observations.insert(140, {"id": "old-bad", "traceId": "t-old"})
        scores = [{"name": "faithfulness", "value": 0.0, "subject": {"kind": "observation", "id": "old-bad"}}]
        api = QueueApi(observations=observations, scores=scores)
        self.assertEqual(self.run_fill(api, ["1"])[0], 0)
        self.assertEqual(api.items, [item("old-bad", "PENDING")])
        self.assertTrue(any(params.get("cursor") == "100" for _, path, _, params in api.calls
                            if params and "observations" in path))

    def test_reads_only_the_scores_that_rank(self):
        api = QueueApi(observations=[{"id": "a", "traceId": "t"}],
                       scores=[{"name": "turn-cost-usd", "value": 0.01} for _ in range(300)])
        self.assertEqual(self.run_fill(api, ["1"])[0], 0)
        names = {n for _, path, _, params in api.calls if "scores" in path for n in params["name"].split(",")}
        self.assertEqual(names, {"faithfulness", "banking-compliance", "banking-compliance-status", "user-feedback",
                                 *[p.name for p in fill.POLICIES]})

    def test_exhaustion_fails_with_fresh_traffic_instructions_without_reopening(self):
        completed = [item("reviewed")]
        api = QueueApi(completed, [{"id": "reviewed"}, {"id": "fresh"}])
        result, out, err = self.run_fill(api, ["2"])
        self.assertEqual(result, 1)
        self.assertIn("1 pending / 1 completed", out)
        self.assertIn("scripts/generate_traffic.py --scenario all", err)
        self.assertEqual(api.items[0], completed[0])

    def test_missing_queue_has_setup_instructions(self):
        with mock.patch.object(fill, "page_all", return_value=[]):
            result, _, err = self.run_fill(QueueApi())
        self.assertEqual(result, 1)
        self.assertIn("scripts/seed_evals.py", err)

    def test_invalid_count_makes_no_api_calls(self):
        for count in ("0", "-1", "invalid"):
            api = QueueApi()
            with self.assertRaises(SystemExit) as raised:
                self.run_fill(api, [count])
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(api.calls, [])


if __name__ == "__main__":
    unittest.main()
