"""Tests for scripts/seed-app-prompts.py: complete reconciliation of the text-to-sql and
vector-rag managed prompts against a fake Prompts v2 API.

The fake sits behind the real urllib request construction (a fake `urlopen`), keeps
versions and labels like Langfuse does (a new version carrying a label moves the label),
and rejects query parameters the real endpoints do not take. No network, no Langfuse.

  python -m unittest discover -s scripts/tests -v
"""

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import json
import sys
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("seed_app_prompts", SCRIPTS / "seed-app-prompts.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load()
ENV = {"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
       "LANGFUSE_BASE_URL": "https://lf.example/"}
PREFIX = "/api/public/v2/prompts"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakePrompts:
    """An in-memory Prompts v2 API. `fail` maps (method, prompt name or None) to an HTTP status,
    or to an exception instance to raise (a network failure)."""

    def __init__(self):
        self.prompts = {}   # name -> [version dict]
        self.fail = {}
        self.calls = []
        self.create_response = None  # override what POST returns (a malformed server reply)

    # --- helpers for arranging state
    def put(self, name, prompt, labels, type_="text", config=None):
        versions = self.prompts.setdefault(name, [])
        for v in versions:
            v["labels"] = [l for l in v["labels"] if l not in labels]
        row = {"name": name, "version": len(versions) + 1, "type": type_, "prompt": prompt,
               "config": copy.deepcopy(config or {}), "labels": list(labels)}
        versions.append(row)
        return row

    def seed_like_the_helper(self):
        for entry in sp.PROMPTS:
            name, text, _m, label, config = sp._unpack(entry)
            self.put(name, text, [label], config=config)

    def version_count(self):
        return sum(len(v) for v in self.prompts.values())

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]

    # --- the fake urlopen
    def __call__(self, req, timeout=None):
        assert timeout, "every request has a timeout"
        token = base64.b64encode(b"pk-test:sk-test").decode()
        assert req.get_header("Authorization") == f"Basic {token}"
        url = urllib.parse.urlsplit(req.full_url)
        assert f"{url.scheme}://{url.netloc}" == "https://lf.example", req.full_url
        params = dict(urllib.parse.parse_qsl(url.query, keep_blank_values=True))
        method = req.get_method()
        path = urllib.parse.unquote(url.path)
        body = json.loads(req.data) if req.data else None
        name = body["name"] if body else (params.get("name") or (path[len(PREFIX) + 1:] or None))
        self.calls.append((method, path, params, body))
        failure = self.fail.get((method, name), self.fail.get((method, None)))
        if isinstance(failure, Exception):
            raise failure
        if failure:
            return self._error(req, failure)

        if method == "GET" and path.startswith(PREFIX + "/"):
            assert set(params) <= {"label", "version"}, f"unknown params {params}"
            label = params["label"]
            for v in self.prompts.get(name, []):
                if label in v["labels"]:
                    return self._ok(v)
            return self._error(req, 404)
        if method == "GET" and path == PREFIX:
            assert set(params) <= {"name", "label", "tag", "page", "limit"}, f"unknown params {params}"
            rows = [{"name": n, "versions": [v["version"] for v in vs],
                     "labels": sorted({l for v in vs for l in v["labels"]})}
                    for n, vs in self.prompts.items() if n == params.get("name")]
            return self._ok({"data": rows, "meta": {"page": 1, "totalPages": 1}})
        if method == "POST" and path == PREFIX:
            assert set(body) <= {"name", "type", "prompt", "labels", "config", "commitMessage", "tags"}
            row = self.put(body["name"], body["prompt"], body.get("labels") or [], body["type"],
                           body.get("config"))
            return self._ok(self.create_response if self.create_response is not None else row)
        raise AssertionError(f"unexpected {method} {path}")

    @staticmethod
    def _ok(obj):
        return _Response(json.dumps(obj).encode())

    @staticmethod
    def _error(req, status):
        raise urllib.error.HTTPError(req.full_url, status, "error", {}, io.BytesIO(b"{}"))


def run(fake, *argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = sp.main(list(argv), environ=dict(ENV if env is None else env), opener=fake)
    return code, out.getvalue(), err.getvalue()


def line_for(out, name, label="production"):
    return next(l for l in out.splitlines() if f" {name} [{label}]:" in l)


class SeedAppPromptsTest(unittest.TestCase):
    def test_fresh_project_creates_every_prompt_with_its_label(self):
        fake = FakePrompts()
        code, out, _ = run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.posts()), len(sp.PROMPTS))
        names = {e[0] for e in sp.PROMPTS}  # a second label on an existing name is an update
        self.assertIn(f"{len(names)} created, {len(sp.PROMPTS) - len(names)} updated, 0 unchanged, 0 failed", out)
        for entry in sp.PROMPTS:
            name, text, _m, label, config = sp._unpack(entry)
            served = next(v for v in fake.prompts[name] if label in v["labels"])
            self.assertEqual((served["type"], served["prompt"], served["config"]), ("text", text, config))

    def test_second_label_on_an_existing_name_is_reported_as_updated(self):
        fake = FakePrompts()
        _, out, _ = run(fake)
        self.assertIn("created", line_for(out, "text-to-sql-analysis", "production"))
        self.assertIn("updated", line_for(out, "text-to-sql-analysis", "candidate"))
        self.assertIn("label 'candidate' added", line_for(out, "text-to-sql-analysis", "candidate"))

    def test_unchanged_reseed_writes_nothing(self):
        fake = FakePrompts()
        fake.seed_like_the_helper()
        before = fake.version_count()
        code, out, _ = run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.posts(), [])
        self.assertEqual(fake.version_count(), before)
        self.assertIn(f"0 created, 0 updated, {len(sp.PROMPTS)} unchanged, 0 failed", out)

    def test_config_only_drift_is_repaired(self):
        fake = FakePrompts()
        fake.seed_like_the_helper()
        served = next(v for v in fake.prompts["vector-rag-generation"] if "production" in v["labels"])
        served["config"] = {"model": "claude-old-model", "temperature": 0.2}
        code, out, _ = run(fake)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.posts()), 1)
        line = line_for(out, "vector-rag-generation")
        self.assertIn("updated", line)
        self.assertIn("config", line)
        self.assertNotIn("text", line.split(":", 1)[1].split("(was")[0])
        now = next(v for v in fake.prompts["vector-rag-generation"] if "production" in v["labels"])
        self.assertEqual(now["config"], sp.CONFIG)

    def test_type_drift_is_repaired(self):
        fake = FakePrompts()
        fake.seed_like_the_helper()
        served = next(v for v in fake.prompts["vector-rag-generation"] if "production" in v["labels"])
        served["type"] = "chat"
        _, out, _ = run(fake, "--only", "vector-rag-generation")
        self.assertIn("type 'chat' -> 'text'", line_for(out, "vector-rag-generation"))
        self.assertEqual(fake.posts()[0][3]["type"], "text")

    def test_text_drift_after_a_ui_edit_moves_the_label_back(self):
        """DEMO_SCRIPT Act 2 edits the prompt in the UI; re-seeding is the reset."""
        fake = FakePrompts()
        fake.seed_like_the_helper()
        fake.put("vector-rag-generation", "Answer in exactly three bullet points. {{context}} {{question}}",
                 ["production"], config=sp.CONFIG)
        _, out, _ = run(fake, "--only", "vector-rag-generation")
        line = line_for(out, "vector-rag-generation")
        self.assertIn("updated", line)
        self.assertIn("text", line)
        self.assertIn("now v3", line)
        served = next(v for v in fake.prompts["vector-rag-generation"] if "production" in v["labels"])
        self.assertEqual(served["prompt"], sp.VECTOR_RAG_GENERATION)

    def test_whitespace_change_is_drift(self):
        fake = FakePrompts()
        fake.seed_like_the_helper()
        served = next(v for v in fake.prompts["vector-rag-generation"] if "production" in v["labels"])
        served["prompt"] += "\n"
        _, out, _ = run(fake, "--only", "vector-rag-generation")
        self.assertIn("updated", line_for(out, "vector-rag-generation"))

    def test_text_to_sql_prompts_are_reconciled_per_label(self):
        fake = FakePrompts()
        fake.seed_like_the_helper()
        critic = next(v for v in fake.prompts["text-to-sql-critic"] if "opinion-only" in v["labels"])
        critic["config"] = {}
        _, out, _ = run(fake)
        self.assertIn("updated", line_for(out, "text-to-sql-critic", "opinion-only"))
        self.assertIn("unchanged", line_for(out, "text-to-sql-critic", "production"))
        self.assertEqual(len(fake.posts()), 1)

    def test_dry_run_reports_without_writing(self):
        fake = FakePrompts()
        code, out, _ = run(fake, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(fake.posts(), [])
        self.assertIn("[dry run]", out)

    def test_only_limits_the_run_and_rejects_unknown_names(self):
        fake = FakePrompts()
        code, out, _ = run(fake, "--only", "vector-rag-generation")
        self.assertEqual(code, 0)
        self.assertEqual({c[3]["name"] for c in fake.posts()}, {"vector-rag-generation"})
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
            sp.main(["--only", "no-such-prompt"], environ=dict(ENV), opener=fake)
        self.assertEqual(e.exception.code, 2)

    def test_partial_write_failure_continues_and_exits_1(self):
        fake = FakePrompts()
        fake.fail[("POST", "vector-rag-generation")] = 500
        code, out, err = run(fake)
        self.assertEqual(code, 1)
        self.assertIn("failed", line_for(out, "vector-rag-generation"))
        self.assertIn("HTTP 500", line_for(out, "vector-rag-generation"))
        self.assertIn("1 failed", out)
        self.assertEqual(len(fake.posts()), len(sp.PROMPTS))  # the others were still written
        self.assertIn("not reconciled", err)

    def test_total_network_failure_fails_every_entry(self):
        fake = FakePrompts()
        fake.fail[("GET", None)] = urllib.error.URLError("connection refused")
        code, out, _ = run(fake)
        self.assertEqual(code, 1)
        self.assertIn(f"0 created, 0 updated, 0 unchanged, {len(sp.PROMPTS)} failed", out)
        self.assertEqual(fake.posts(), [])

    def test_read_failure_is_a_failure_not_a_create(self):
        fake = FakePrompts()
        fake.fail[("GET", "vector-rag-generation")] = 500
        code, out, _ = run(fake, "--only", "vector-rag-generation")
        self.assertEqual(code, 1)
        self.assertEqual(fake.posts(), [])
        self.assertIn("HTTP 500", out)

    def test_create_response_without_the_label_is_a_failure(self):
        fake = FakePrompts()
        fake.create_response = {"version": 1, "labels": []}
        code, out, _ = run(fake, "--only", "vector-rag-generation")
        self.assertEqual(code, 1)
        self.assertIn("no version carrying label 'production'", out)

    def test_missing_keys_exit_1_before_any_request(self):
        fake = FakePrompts()
        code, _, err = run(fake, env={"LANGFUSE_BASE_URL": "https://lf.example"})
        self.assertEqual(code, 1)
        self.assertEqual(fake.calls, [])
        self.assertIn("not set", err)

    def test_host_resolution_prefers_base_url_and_strips_the_slash(self):
        self.assertEqual(sp.resolve_host({"LANGFUSE_BASE_URL": "https://a.example/ ",
                                          "LANGFUSE_HOST": "https://b.example"}), "https://a.example")
        self.assertEqual(sp.resolve_host({"LANGFUSE_HOST": "https://b.example/"}), "https://b.example")
        self.assertEqual(sp.resolve_host({}), "http://localhost:3001")

    def test_drift_lists_every_difference(self):
        cur = {"type": "chat", "prompt": "old", "config": {"model": "x"}, "labels": ["latest"]}
        d = sp.drift(cur, "new", "production", {"model": "y"})
        self.assertEqual(len(d), 4)
        self.assertEqual(sp.drift(None, "t", "production", {}), ["missing"])
        ok = {"type": "text", "prompt": "t", "config": {}, "labels": ["production"]}
        self.assertEqual(sp.drift(ok, "t", "production", None), [])


if __name__ == "__main__":
    unittest.main()
