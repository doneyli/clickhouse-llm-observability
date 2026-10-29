"""scripts/verify_demo.py — the pure helpers behind the hygiene checks (no network).

Run: .venv/bin/python -m unittest discover -s tests -t . -v
"""
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR / "scripts"))
sys.path.insert(0, str(DEMO_DIR))

import verify_demo as vd  # noqa: E402
from northwind import prompts  # noqa: E402


def obs(trace, **fields):
    return {"traceId": trace, "id": f"o-{trace}", **fields}


class FindPii(unittest.TestCase):
    def test_every_needle_is_found_in_input_output_or_metadata(self):
        cases = [
            ("4111 1111 1111 1111", {"input": "my card is 4111 1111 1111 1111 ok"}),
            ("4111111111111111", {"output": "4111111111111111"}),
            ("4111-1111-1111-1111", {"metadata": {"note": "pan 4111-1111-1111-1111"}}),
            ("1020304050", {"input": "national id 1020304050."}),
            ("0012345678", {"metadata": {"account": "0012345678"}}),
            ("998877", {"input": "my PIN is 998877"}),
            ("ana.torres@example.com", {"output": "write to Ana.Torres@Example.com"}),
            ("ben.okafor@example.com", {"input": "ben.okafor@example.com"}),
            ("maria.g@example.com", {"metadata": {"email": "maria.g@example.com"}}),
        ]
        self.assertEqual({n for n, _ in cases}, set(vd.PII_NEEDLES))  # the list in the code is the list tested
        for needle, fields in cases:
            with self.subTest(needle=needle):
                self.assertEqual(vd.find_pii([obs("t1", **fields)]), {needle: ["t1"]})

    def test_masked_text_is_clean(self):
        o = obs("t1", input="card [CARD] id [NATIONAL_ID]", output="mail [EMAIL]", metadata={"pin": "[REDACTED]"})
        self.assertEqual(vd.find_pii([o]), {})

    def test_digit_needles_do_not_match_inside_longer_tokens(self):
        # a hex trace id or a longer number that merely CONTAINS 998877 is not the PIN
        o = obs("t1", input="trace a998877b and ref 19988771 and 99887700")
        self.assertEqual(vd.find_pii([o]), {})

    def test_distinct_trace_ids_in_order_without_duplicates(self):
        os_ = [obs("ta", input="1020304050"), obs("tb", output="1020304050"), obs("ta", metadata={"x": "1020304050"})]
        self.assertEqual(vd.find_pii(os_), {"1020304050": ["ta", "tb"]})

    def test_non_ascii_context_is_searched_both_ways(self):
        o = obs("t1", input="tarjeta 4111111111111111 — señora Peña ¿ok?")
        self.assertEqual(vd.find_pii([o]), {"4111111111111111": ["t1"]})

    def test_missing_fields_are_fine(self):
        self.assertEqual(vd.find_pii([{"traceId": "t1"}]), {})

    def test_fmt_pii_caps_trace_ids_per_needle(self):
        text = vd.fmt_pii({"998877": [f"t{i}" for i in range(7)], "0012345678": ["x"]}, per_needle=5)
        self.assertIn("998877 → t0, t1, t2, t3, t4 (+2 more)", text)
        self.assertIn("0012345678 → x", text)


class UnscoredRoots(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def root(self, oid, ended_min_ago, **kw):
        end = self.NOW - timedelta(minutes=ended_min_ago)
        base = {"id": oid, "traceId": f"t-{oid}", "name": "northwind-assistant", "type": "AGENT",
                "environment": "production", "startTime": (end - timedelta(seconds=5)).isoformat(),
                "endTime": end.isoformat().replace("+00:00", "Z")}
        return {**base, **kw}

    def test_counts_only_settled_production_roots(self):
        observations = [
            self.root("scored", 30),
            self.root("missing-old", 120),
            self.root("missing-new", 11),
            self.root("too-young", 3),                                  # inside the 10 min grace
            self.root("other-env", 60, environment="staging"),
            self.root("wrong-type", 60, type="SPAN"),
            self.root("wrong-name", 60, name="mcp-server: block_card"),
            self.root("still-running", 60, endTime=None),
        ]
        eligible, missing = vd.unscored_roots(observations, {"scored"}, self.NOW)
        self.assertEqual([o["id"] for o in eligible], ["scored", "missing-old", "missing-new"])
        self.assertEqual([o["id"] for o in missing], ["missing-old", "missing-new"])

    def test_all_scored(self):
        eligible, missing = vd.unscored_roots([self.root("a", 30), self.root("b", 40)], {"a", "b"}, self.NOW)
        self.assertEqual((len(eligible), missing), (2, []))

    def test_parse_ts_accepts_z_suffix_and_milliseconds(self):
        self.assertEqual(vd.parse_ts("2026-10-08T16:55:56.484Z"),
                         datetime(2026, 10, 8, 16, 55, 56, 484000, tzinfo=timezone.utc))


class PromptTextDrift(unittest.TestCase):
    LABELS = {"baseline": 1, "development": 3, "production": 4, "staging": 5}

    def pv(self, override=None):
        base = {1: {"prompt": prompts.V1_BASELINE}, 3: {"prompt": prompts.V3_REGRESSION},
                4: {"prompt": prompts.V2_CANDIDATE}, 5: {"prompt": prompts.V5_RELEASE}}
        return {**base, **(override or {})}

    def test_matching_texts_report_nothing(self):
        # whitespace at the edges is not drift
        self.assertEqual(vd.prompt_text_drift(self.pv({1: {"prompt": prompts.V1_BASELINE + "\n"}}), self.LABELS), [])

    def test_edited_development_prompt_is_reported_with_its_label(self):
        out = vd.prompt_text_drift(self.pv({3: {"prompt": prompts.V3_REGRESSION + " extra"}}), self.LABELS)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("development (v3) ≠ prompts.V3_REGRESSION"))

    def test_v4_is_the_candidate_whatever_labels_it_carries(self):
        out = vd.prompt_text_drift(self.pv({4: {"prompt": "something else"}}),
                                   {**self.LABELS, "production": 5, "previous-production": 4})
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("candidate (v4) ≠ prompts.V2_CANDIDATE"))

    def test_truncated_staging_text_shows_where_it_diverges(self):
        out = vd.prompt_text_drift(self.pv({5: {"prompt": prompts.V5_RELEASE[3:]}}), self.LABELS)
        self.assertEqual(len(out), 1)
        self.assertIn("staging (v5)", out[0])
        self.assertIn("first difference at char 0", out[0])

    def test_labels_that_do_not_exist_are_skipped(self):
        self.assertEqual(vd.prompt_text_drift({}, {}), [])


class McpProcessStart(unittest.TestCase):
    def fake_run(self, lsof_out, ps_out):
        def run(cmd, **kw):
            out = lsof_out if cmd[0] == "lsof" else ps_out
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        return run

    def test_parses_pid_and_converts_local_start_to_utc(self):
        with mock.patch.object(vd.subprocess, "run", self.fake_run("4242\n", "Thu Oct  8 09:01:23 2026\n")):
            pid, started = vd.mcp_process_start(8765)
        self.assertEqual(pid, "4242")
        self.assertEqual(started.utcoffset(), timedelta(0))
        self.assertEqual(started.astimezone().replace(tzinfo=None), datetime(2026, 10, 8, 9, 1, 23))

    def test_nothing_listening(self):
        with mock.patch.object(vd.subprocess, "run", self.fake_run("", "")):
            self.assertIsNone(vd.mcp_process_start(8765))

    def test_lsof_missing_or_unparseable_output(self):
        with mock.patch.object(vd.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(vd.mcp_process_start(8765))
        with mock.patch.object(vd.subprocess, "run", self.fake_run("1\n", "garbage\n")):
            self.assertIsNone(vd.mcp_process_start(8765))


if __name__ == "__main__":
    unittest.main()
