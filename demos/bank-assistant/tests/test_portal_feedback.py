"""Feedback HTTP contract, without starting services or calling Langfuse."""
import unittest
from unittest import mock

import httpx
from portal import server

TRACE = "a" * 32


class PortalFeedbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                        base_url="http://portal.test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_failed_delivery_can_retry_without_another_score(self):
        lf = mock.Mock()
        queue = mock.Mock()
        queue.add.side_effect = [RuntimeError("queue unavailable"), {"queue_id": "q1", "added": True}]
        with (mock.patch.object(server.config, "get_langfuse", return_value=lf),
              mock.patch.object(server.config, "flush") as full_flush,
              mock.patch.object(server, "_FEEDBACK_QUEUE", queue),
              mock.patch.object(server, "_project_id_sync", return_value="project")):
            first = await self.client.post("/api/feedback", json={"trace_id": TRACE, "value": 0,
                                                                 "comment": "wrong fee"})
            self.assertEqual(first.status_code, 200)
            self.assertTrue(first.json()["ok"])
            self.assertFalse(first.json()["queue"]["ok"])
            retry = await self.client.post("/api/feedback/queue", json={"trace_id": TRACE})
            self.assertEqual(retry.status_code, 200)
            self.assertTrue(retry.json()["ok"])
            self.assertTrue(retry.json()["url"].endswith("/project/project/annotation-queues/q1"))
            lf.create_score.assert_called_once_with(trace_id=TRACE, name="user-feedback", value=0,
                                                     data_type="BOOLEAN", comment="wrong fee")
            lf.flush.assert_called_once()
            full_flush.assert_not_called()
            self.assertEqual(queue.add.call_count, 2)

    async def test_positive_feedback_does_not_enqueue(self):
        with (mock.patch.object(server.config, "get_langfuse") as lf,
              mock.patch.object(server, "_FEEDBACK_QUEUE") as queue):
            response = await self.client.post("/api/feedback", json={"trace_id": TRACE, "value": 1})
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()["queue"])
            queue.add.assert_not_called()
            lf.return_value.create_score.assert_called_once()

    async def test_retry_rejects_invalid_trace_ids_without_any_writes(self):
        with (mock.patch.object(server.config, "get_langfuse") as lf,
              mock.patch.object(server, "_FEEDBACK_QUEUE") as queue):
            response = await self.client.post("/api/feedback/queue", json={"trace_id": "invalid"})
            self.assertEqual(response.status_code, 400)
            lf.assert_not_called()
            queue.add.assert_not_called()

    async def test_retry_reports_queue_failure_without_scoring(self):
        with (mock.patch.object(server.config, "get_langfuse") as lf,
              mock.patch.object(server, "_FEEDBACK_QUEUE") as queue):
            queue.add.side_effect = RuntimeError("still unavailable")
            response = await self.client.post("/api/feedback/queue", json={"trace_id": TRACE})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["ok"])
            lf.assert_not_called()
