"""Thumbs-down → review queue (northwind/feedback_queue.py), without a live Langfuse project."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
