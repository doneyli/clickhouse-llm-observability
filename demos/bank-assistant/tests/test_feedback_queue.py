"""Thumbs-down → review queue (northwind/feedback_queue.py), without a live Langfuse project."""
import contextlib
import io
import sys
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest import mock

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR))
from northwind import feedback_queue as fq  # noqa: E402

TRACE = "a" * 32
OTHER = "b" * 32


class FakeApi:
    """Paged queues / items / score configs; POST items fail `fail_posts` times first."""

    def __init__(self, queues=(), items=(), configs=(), fail_posts=0):
        self.queues = deepcopy(list(queues))
        self.items = deepcopy(list(items))
        self.configs = deepcopy(list(configs))
        self.fail_posts = fail_posts
        self.calls = []

    def _page(self, rows, params):
        return {"data": rows[(params["page"] - 1) * 100:params["page"] * 100],
                "meta": {"totalPages": max(1, (len(rows) + 99) // 100)}}

    def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, deepcopy(body)))
        if path == "/api/public/annotation-queues":
            if method == "GET":
                return self._page(self.queues, params)
            queue = {"id": "q-new", **body}
            self.queues.append(queue)
            return queue
        if path == "/api/public/score-configs":
            if method == "GET":
                return self._page(self.configs, params)
            config = {"id": "cfg-" + body["name"], **body}
            self.configs.append(config)
            return config
        if path.endswith("/items"):
            if method == "GET":
                return self._page(self.items, params)
            if self.fail_posts:
                self.fail_posts -= 1
                raise RuntimeError("POST → 503")
            self.items.append({"id": f"item-{len(self.items)}", **body})
            return self.items[-1]
        raise AssertionError(f"unexpected call {method} {path}")

    def posts(self, suffix):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]


class FeedbackQueueTest(unittest.TestCase):
    def test_first_use_before_seeding_creates_both_score_configs(self):
        api = FakeApi()
        fq.FeedbackQueue(api, retry_delay=0).add(TRACE)
        self.assertEqual({c["name"] for c in api.configs}, {"sme-failure-mode", "pm-action"})
        self.assertEqual(api.queues[0]["scoreConfigIds"],
                         ["cfg-sme-failure-mode", "cfg-pm-action"])
        fq.FeedbackQueue(api, retry_delay=0).add(TRACE)  # restart, no duplicate resources
        self.assertEqual(len(api.posts("/score-configs")), 2)
        self.assertEqual(len(api.posts("/annotation-queues")), 1)

    def test_creates_queue_with_failure_mode_and_pm_action(self):
        api = FakeApi(configs=[{"id": "cfg-fm", "name": "sme-failure-mode"}])
        res = fq.FeedbackQueue(api, retry_delay=0).add(TRACE)
        self.assertEqual(res, {"queue_id": "q-new", "added": True})
        [(_, _, queue)] = api.posts("/annotation-queues")
        self.assertEqual(queue["name"], fq.QUEUE_NAME)
        self.assertEqual(queue["scoreConfigIds"], ["cfg-fm", "cfg-pm-action"])
        self.assertEqual(api.posts("/items")[0][2],
                         {"objectId": TRACE, "objectType": "TRACE", "status": "PENDING"})

    def test_reuses_existing_queue_and_never_duplicates(self):
        api = FakeApi(queues=[{"id": "other", "name": "SME review — assistant answers"},
                              {"id": "q1", "name": fq.QUEUE_NAME}],
                      items=[{"id": "i0", "objectId": TRACE, "objectType": "TRACE", "status": "COMPLETED"}])
        queue = fq.FeedbackQueue(api, retry_delay=0)
        self.assertEqual(queue.add(TRACE), {"queue_id": "q1", "added": False})  # queued before a restart
        self.assertEqual(queue.add(OTHER), {"queue_id": "q1", "added": True})
        self.assertEqual(queue.add(OTHER), {"queue_id": "q1", "added": False})  # second click
        self.assertEqual(len(api.posts("/items")), 1)
        self.assertEqual(api.posts("/annotation-queues"), [])
        self.assertEqual(api.posts("/score-configs"), [])

    def test_retries_a_transient_failure(self):
        api = FakeApi(queues=[{"id": "q1", "name": fq.QUEUE_NAME}], fail_posts=2)
        self.assertTrue(fq.FeedbackQueue(api, retries=2, retry_delay=0).add(TRACE)["added"])
        self.assertEqual(len(api.posts("/items")), 3)

    def test_a_failed_add_can_be_retried_by_a_later_click(self):
        api = FakeApi(queues=[{"id": "q1", "name": fq.QUEUE_NAME}], fail_posts=3)
        queue = fq.FeedbackQueue(api, retries=2, retry_delay=0)
        with self.assertRaises(RuntimeError):
            queue.add(TRACE)
        self.assertTrue(queue.add(TRACE)["added"])

    def concurrent_add(self, *, fail):
        api = FakeApi(queues=[{"id": "q1", "name": fq.QUEUE_NAME}])
        started, release, waiting = threading.Event(), threading.Event(), threading.Event()

        class Pending(Future):
            def result(self, timeout=None):
                waiting.set()
                return super().result(timeout)

        def blocked_api(method, path, body=None, params=None):
            if method == "POST" and path.endswith("/items") and body["objectId"] == TRACE:
                started.set()
                if not release.wait(5):
                    raise AssertionError("test did not release the POST")
                if fail:
                    raise RuntimeError("POST failed")
            return api(method, path, body, params)

        queue = fq.FeedbackQueue(blocked_api, retries=0)
        pending = Pending()
        with (mock.patch.object(fq, "Future", return_value=pending, create=True),
              ThreadPoolExecutor(max_workers=2) as pool):
            first = pool.submit(queue.add, TRACE)
            try:
                self.assertTrue(started.wait(5))
                second = pool.submit(queue.add, TRACE)
                self.assertTrue(waiting.wait(5))
                self.assertFalse(second.done(), "a duplicate must wait for the POST result")
            finally:
                release.set()
            if fail:
                for call in (first, second):
                    with self.assertRaisesRegex(RuntimeError, "POST failed"):
                        call.result(timeout=5)
                self.assertEqual(api.items, [])
            else:
                self.assertTrue(first.result(timeout=5)["added"])
                self.assertFalse(second.result(timeout=5)["added"])
                self.assertEqual(len(api.items), 1)
        return queue, api

    def test_concurrent_duplicate_waits_for_confirmed_success(self):
        self.concurrent_add(fail=False)

    def test_concurrent_duplicate_shares_failure_and_can_retry(self):
        queue, api = self.concurrent_add(fail=True)
        queue.api = api
        self.assertTrue(queue.add(TRACE)["added"])


class FeedbackQueueSeedingTest(unittest.TestCase):
    def test_seeding_twice_before_or_after_first_feedback_keeps_both_dimensions(self):
        from scripts import seed_evals

        class Registry(FakeApi):
            def __init__(self):
                super().__init__()
                self.evaluators, self.rules = [], []

            def __call__(self, method, path, body=None, params=None):
                for suffix, rows in [("/v2/evaluators", self.evaluators),
                                     ("/v2/evaluation-rules", self.rules)]:
                    if path.endswith(suffix):
                        if method == "GET":
                            return {"data": rows, "meta": {}}
                        row = {"id": f"id-{len(rows)}", "status": "active", **body}
                        rows.append(row)
                        return row
                    if suffix + "/" in path and method == "PATCH":
                        return body
                return super().__call__(method, path, body, params)

        for portal_first in (False, True):
            with self.subTest(portal_first=portal_first):
                api = Registry()
                if portal_first:
                    fq.FeedbackQueue(api).add(TRACE)
                with (mock.patch.object(seed_evals.config, "api", api),
                      mock.patch.object(seed_evals.config, "ANTHROPIC_API_KEY", None),
                      mock.patch.object(seed_evals.config, "project_id", return_value="project"),
                      contextlib.redirect_stdout(io.StringIO())):
                    seed_evals.main()
                    seed_evals.main()
                [queue] = [q for q in api.queues if q["name"] == fq.QUEUE_NAME]
                self.assertEqual(queue["scoreConfigIds"],
                                 ["cfg-sme-failure-mode", "cfg-pm-action"])
                for name in (fq.FAILURE_MODE_CONFIG, fq.PM_ACTION_CONFIG["name"]):
                    self.assertEqual(sum(c["name"] == name for c in api.configs), 1)


if __name__ == "__main__":
    unittest.main()
