#!/usr/bin/env python3
"""Loopback-only portal. Runs the named scripts and streams sanitized execution records."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid

from workflows import ROOT, HOST, WRAPPER, WORKFLOWS, Context, WorkflowError, load_state, public_state, reset_preview, scrub, script_source, validate, write_json


class Portal:
    def __init__(self, directory, org_env):
        self.directory = Path(directory).resolve()
        self.org_env = Path(org_env).resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.org_env.is_file():
            raise SystemExit("Organization credentials file not found. Supply --org-env; its contents are loaded only by the approved CLI.")
        self.runs = {}
        for path in sorted((self.directory / "runs").glob("*.json"), key=lambda path: path.stat().st_mtime)[-20:]:
            record = json.loads(path.read_text())
            self.runs[record["id"]] = record
        self.lock = threading.Lock()
        self.active = None
        self.previewing = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.tag = uuid.uuid4().hex[:6]

    def state(self):
        with self.lock:
            return {**public_state(load_state(self.directory)), "activeRun": self.active, "demoTag": self.tag}

    def start(self, workflow, params):
        params = validate(workflow, params)
        with self.lock:
            if self.active or self.previewing:
                raise WorkflowError("A workflow or reset preview is already running. Wait for it to complete.")
            if workflow not in {"application", "reset", "governance", "audit", "instance"} and params["projectId"] not in load_state(self.directory)["applications"]:
                raise WorkflowError("Select an application created by this portal.")
            run_id = str(uuid.uuid4())
            run = {"id": run_id, "workflow": workflow, "status": "running", "startedAt": time.time(), "events": [], "params": params}
            self.runs[run_id] = run
            self.active = run_id
        write_json(self.directory / "requests" / (run_id + ".json"), params)
        self.executor.submit(self.execute, run_id)
        return run_id

    def execute(self, run_id):
        run = self.runs[run_id]
        info = next(w for w in WORKFLOWS if w["id"] == run["workflow"])
        command = [str(ROOT / "scripts" / info["script"]), "--params-file", str(self.directory / "requests" / (run_id + ".json")),
                   "--state-dir", str(self.directory), "--org-env", str(self.org_env), "--run-id", run_id]
        try:
            # stderr goes to a file: a piped stderr that fills while stdout is being read would deadlock the run.
            with tempfile.TemporaryFile(mode="w+") as errors:
                process = subprocess.Popen(command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=errors, text=True)
                for line in process.stdout:
                    try:
                        event = scrub(json.loads(line))
                    except json.JSONDecodeError:
                        event = {"type": "error", "message": "The workflow returned an invalid execution event."}
                    with self.lock:
                        run["events"].append(event)
                code = process.wait()
                errors.seek(0)
                stderr = errors.read()
            with self.lock:
                complete = any(e["type"] == "complete" for e in run["events"])
                run["status"] = "complete" if code == 0 and complete else "failed"
                finish = next((e for e in run["events"] if e["type"] == "complete"), {})
                if run["status"] == "complete" and (finish.get("result", {}).get("pendingProjectIds") or finish.get("result", {}).get("pendingOrganizationIds")):
                    run["status"] = "pending"
                if run["status"] == "failed" and not any(e["type"] == "error" for e in run["events"]):
                    run["events"].append({"type": "error", "message": scrub(stderr)[-1000:] or "Workflow failed."})
        except Exception as error:
            with self.lock:
                run["events"].append({"type": "error", "message": scrub(str(error))})
                run["status"] = "failed"
        finally:
            with self.lock:
                run["finishedAt"] = time.time()
                self.active = None
                if run["workflow"] == "reset" and run["status"] == "complete":
                    self.tag = uuid.uuid4().hex[:6]
                record = json.loads(json.dumps(run))
            write_json(self.directory / "runs" / (run_id + ".json"), record)

    def preflight(self):
        command = [str(WRAPPER), "--env-file", str(self.org_env), "--host", HOST, "api", "organizations", "list-projects", "--json"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
        body = payload.get("body", {})
        projects = body.get("data", body.get("projects", [])) if isinstance(body, dict) else []
        return {"host": HOST, "status": payload.get("status"), "organizationProjectCount": len(projects), "ok": payload.get("status") == 200}

    def preview_reset(self):
        # A flag, not the lock, keeps runs out during the preview: the live inventory makes one CLI call per
        # project, and holding the lock that long would stall state reads and run polling.
        with self.lock:
            if self.active:
                raise WorkflowError("Wait for the running workflow before previewing reset.")
            if self.previewing:
                raise WorkflowError("A reset preview is already running.")
            self.previewing = True
        try:
            context = Context(self.directory, self.org_env, "reset-preview", emit=lambda event: None)
            return reset_preview(context)
        finally:
            with self.lock:
                self.previewing = False


def handler_for(portal):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalPlatformDemo/1"

        def log_message(self, fmt, *args):
            if args and str(args[0]).startswith("GET /api/runs/"):
                return
            super().log_message(fmt, *args)

        def allowed_host(self):
            host = self.headers.get("Host", "")
            return host in {"127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port)}

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            raw = json.dumps(body).encode() if content_type.startswith("application/json") else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {"error": "This demo is available on loopback only."})
            url = urlparse(self.path)
            if url.path == "/api/config":
                return self.send(200, {"host": HOST, "workflows": WORKFLOWS, "instanceConfigured": (portal.directory / "instance.env").is_file(), "nativeAdapterVersion": "4.76.0", "mode": "live", "comparison": "self-hosted OSS versus self-hosted Enterprise"})
            if url.path == "/api/state":
                return self.send(200, portal.state())
            if url.path == "/api/reset-preview":
                try:
                    return self.send(200, portal.preview_reset())
                except WorkflowError as error:
                    return self.send(409, {"error": scrub(str(error))})
            if url.path == "/api/preflight":
                try:
                    return self.send(200, portal.preflight())
                except Exception:
                    return self.send(503, {"error": "Cannot reach the local organization API."})
            if url.path == "/api/script":
                workflow = parse_qs(url.query).get("workflow", [""])[0]
                if workflow not in {w["id"] for w in WORKFLOWS}:
                    return self.send(400, {"error": "Unknown script."})
                return self.send(200, script_source(workflow))
            if url.path.startswith("/api/runs/"):
                run_id = url.path.split("/")[-1]
                with portal.lock:
                    run = portal.runs.get(run_id)
                    snapshot = json.loads(json.dumps(run)) if run else None
                return self.send(200, snapshot) if snapshot else self.send(404, {"error": "Run not found."})
            if url.path == "/future-addons.md":
                return self.send(200, (ROOT / "docs" / "future-addons.md").read_bytes(), "text/plain; charset=utf-8")
            paths = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            name = paths.get(url.path)
            if not name:
                return self.send(404, {"error": "Not found."})
            path = ROOT / "public" / name
            return self.send(200, path.read_bytes(), (mimetypes.guess_type(name)[0] or "text/plain") + "; charset=utf-8")

        def do_POST(self):
            origin = self.headers.get("Origin")
            allowed = {"http://127.0.0.1:" + str(self.server.server_port), "http://localhost:" + str(self.server.server_port)}
            if not self.allowed_host() or (origin and origin not in allowed) or self.headers.get("X-Demo-Request") != "1" or not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.send(403, {"error": "Use the local portal to run workflows."})
            if self.path != "/api/run":
                return self.send(404, {"error": "Not found."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("Invalid request size.")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Request must be a JSON object.")
                run_id = portal.start(data.get("workflow"), data.get("params"))
                return self.send(202, {"runId": run_id})
            except (ValueError, TypeError, WorkflowError) as error:
                return self.send(400, {"error": scrub(str(error))})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--org-env", default=str(ROOT / "private" / "org.env"))
    parser.add_argument("--state-dir", default=str(ROOT / "private"))
    args = parser.parse_args()
    os.umask(0o077)
    portal = Portal(args.state_dir, args.org_env)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(portal))
    print("Portal: http://127.0.0.1:" + str(server.server_port), flush=True)
    print("Langfuse: " + HOST, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        portal.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
