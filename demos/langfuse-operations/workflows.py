#!/usr/bin/env python3
"""Real, local Langfuse administration workflows. Credentials load only in the approved CLI."""

import argparse
import ast
from contextlib import contextmanager
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
WRAPPER = ROOT / "scripts" / "langfuse-connect.sh"
HOST = "http://localhost:3001"
OPERATIONS = json.loads((ROOT / "api-operations.json").read_text())
ROLES = {"VIEWER", "MEMBER", "ADMIN"}
KEY_ROLES = {"ADMIN": "Full access", "VIEWER": "Viewer", "INGEST": "Ingestion", "SCORES_INGEST": "Scores ingestion"}
DOC_ADMIN = "https://langfuse.com/docs/administration/scim-and-org-api"
DOC_API = "https://langfuse.com/docs/api-and-data-platform/features/public-api"
WORKFLOWS = [
    {"id": "application", "label": "Application", "title": "Provision an application", "button": "Create application", "edition": "mixed", "script": "onboard-application.sh", "project": False},
    {"id": "team", "label": "Team", "title": "Onboard a team", "button": "Onboard team", "edition": "enterprise", "script": "onboard-team.sh", "project": True},
    {"id": "permissions", "label": "Permissions", "title": "Change project access", "button": "Update role", "edition": "enterprise", "script": "change-access.sh", "project": True},
    {"id": "keys", "label": "Keys", "title": "Rotate the demo integration key", "button": "Rotate integration key", "edition": "mixed", "script": "rotate-application-key.sh", "project": True},
    {"id": "dashboard", "label": "Dashboards", "title": "Provision an operational dashboard", "button": "Create dashboard", "edition": "oss", "script": "provision-dashboard.sh", "project": True},
    {"id": "offboarding", "label": "Offboarding", "title": "Remove the team's project access", "button": "Remove team access", "edition": "enterprise", "script": "offboard-team.sh", "project": True},
    {"id": "governance", "label": "Protected labels", "title": "Protect production prompt promotion", "button": "Set up and verify governance", "edition": "enterprise", "script": "protected-labels.sh", "project": False},
    {"id": "audit", "label": "Audit evidence", "title": "Inspect native audit evidence", "button": "Generate and verify audit evidence", "edition": "enterprise", "script": "audit-evidence.sh", "project": False},
    {"id": "instance", "label": "Organizations", "title": "Provision an isolated organization", "button": "Create organization and workspace", "edition": "enterprise", "script": "provision-organization.sh", "project": False},
    {"id": "reset", "label": "Reset demo", "title": "Reset the demo environment", "button": "Delete demo projects and reset", "edition": "enterprise", "script": "reset-demo.sh", "project": False},
]


class WorkflowError(Exception):
    pass


def scrub(value):
    """Redact before an event reaches stdout, the browser, or a run log."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if re.search(r"secret|password|authorization|token|credential", k, re.I) else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"(?:sk|pk)-lf-[A-Za-z0-9-]+", "[redacted API key]", value)
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
        json.dump(value, out, indent=2)
        out.write("\n")
    temp.replace(path)


def load_state(directory):
    path = Path(directory) / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"applications": {}, "users": {}, "organizationId": None}


def public_state(state):
    apps = []
    for app in state.get("applications", {}).values():
        clean = {k: v for k, v in app.items() if k not in {"envFile", "previousEnvFile"}}
        clean["url"] = HOST + "/project/" + app["id"]
        apps.append(clean)
    organizations = [{k: org.get(k) for k in ("id", "name", "projectId", "projectName", "resetStatus")} for org in state.get("organizations", {}).values()]
    return {"applications": apps, "organizations": organizations, "governanceProjectId": state.get("governance", {}).get("projectId"), "organizationId": state.get("organizationId"), "host": HOST}


def validate(workflow, params):
    if workflow not in {w["id"] for w in WORKFLOWS}:
        raise WorkflowError("Unknown workflow.")
    if not isinstance(params, dict):
        raise WorkflowError("Parameters must be a JSON object.")
    p = dict(params)
    if workflow == "application":
        for field in ("applicationName", "teamName"):
            value = p.get(field, "")
            if not isinstance(value, str) or not value.strip() or len(value) > (60 if field == "applicationName" else 120):
                raise WorkflowError(field + " must contain a valid application/team name (application max 60, team max 120).")
            p[field] = value.strip()
        days = p.get("retentionDays", 30)
        if isinstance(days, bool) or not isinstance(days, int) or days < 0 or days in (1, 2):
            raise WorkflowError("Retention must be 0 (unlimited) or at least 3 whole days.")
        count = p.get("applicationCount", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5:
            raise WorkflowError("Create between 1 and 5 applications per run.")
    elif workflow not in {"reset", "governance", "audit", "instance"} and (not isinstance(p.get("projectId"), str) or not p["projectId"]):
        raise WorkflowError("Select an application created by this portal.")
    if workflow in {"governance", "instance"}:
        field = "governanceName" if workflow == "governance" else "organizationName"
        value = p.get(field, "")
        if not isinstance(value, str) or not value.strip() or len(value) > (60 if workflow == "governance" else 100):
            raise WorkflowError("Provide a name containing 1 to 100 characters.")
        p = {field: value.strip()}
    if workflow == "team":
        members = p.get("members", [])
        if not isinstance(members, list) or not 1 <= len(members) <= 10:
            raise WorkflowError("Provide 1 to 10 team members.")
        seen = set()
        for member in members:
            if not isinstance(member, dict):
                raise WorkflowError("Each team member must be an email and role object.")
            email = str(member.get("email", "")).strip().lower()
            if not re.fullmatch(r"[^\s@\"\\]+@[^\s@\"\\]+\.[^\s@\"\\]+", email) or len(email) > 254:
                raise WorkflowError("Each team member needs a valid email address.")
            if member.get("role") not in ROLES:
                raise WorkflowError("Choose Viewer, Member, or Admin.")
            if email in seen:
                raise WorkflowError("Each team member must have a different email.")
            member["email"] = email
            seen.add(email)
    if workflow == "permissions" and (p.get("role") not in ROLES or not p.get("userId")):
        raise WorkflowError("Select a managed member and a project role.")
    if workflow in {"application", "keys"} and (not isinstance(p.get("keyRole"), str) or p["keyRole"] not in KEY_ROLES):
        raise WorkflowError("Choose the new API key's permissions: Full access, Viewer, Ingestion, or Scores ingestion.")
    if workflow == "reset":
        if p.get("confirmation") != "RESET DEMO":
            raise WorkflowError("Preview the reset and type RESET DEMO to confirm permanent project deletion.")
        if not isinstance(p.get("previewFingerprint"), str) or not re.fullmatch(r"[a-f0-9]{64}", p["previewFingerprint"]):
            raise WorkflowError("Preview the objects to reset before confirming deletion.")
        p = {"confirmation": p["confirmation"], "previewFingerprint": p["previewFingerprint"]}
    return p


class Context:
    def __init__(self, directory, org_env, run_id, emit=None):
        self.directory = Path(directory).resolve()
        self.org_env = str(Path(org_env).resolve())
        self.run_id = run_id
        self.state = load_state(self.directory)
        self.emit = emit or (lambda event: print(json.dumps(scrub(event)), flush=True))
        self.calls = 0

    def save(self):
        write_json(self.directory / "state.json", self.state)

    def note(self, label, detail):
        self.emit({"type": "note", "label": label, "detail": detail, "edition": "orchestration"})

    def managed_app(self, project_id):
        app = self.state["applications"].get(project_id)
        if not app:
            raise WorkflowError("This portal only changes applications recorded in its own demo manifest.")
        return app

    def call(self, label, resource, action, ids=(), body=None, query=(), env=None, expected=(200, 201)):
        operation = OPERATIONS[resource + "/" + action]
        route = operation["path"]
        for key, value in zip(operation.get("pathParameterOrder", []), ids):
            from urllib.parse import quote
            route = route.replace("{" + key + "}", quote(str(value), safe=""))
        if query:
            from urllib.parse import urlencode
            names = operation.get("queryParameters", {})
            route += "?" + urlencode([(names.get(query[i].removeprefix("--"), query[i].removeprefix("--")), query[i + 1]) for i in range(0, len(query), 2)])
        argv = [str(WRAPPER), "--env-file", env or self.org_env, "--host", HOST, "api", resource, action, *map(str, ids)]
        if body is not None:
            argv += ["--body-json", json.dumps(body)]
        argv += list(query) + ["--json"]
        self.calls += 1
        step_id = self.run_id + ":" + str(self.calls)
        visible_argv = list(argv)
        visible_argv[2] = ("$CHILD_ORG_ENV" if operation["scope"] == "Organization key" else "$PROJECT_ENV") if env else "$ORG_ENV"
        visible_argv[0] = "scripts/langfuse-connect.sh"
        if body is not None:
            visible_argv[visible_argv.index("--body-json") + 1] = json.dumps(scrub(body))
        caller = inspect.currentframe().f_back
        source_line = caller.f_lineno
        caller_path = Path(caller.f_code.co_filename).resolve()
        if caller_path == Path(__file__).resolve():
            lines = caller_path.read_text().splitlines()
            tree = ast.parse("\n".join(lines))
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "call" and node.lineno <= source_line <= node.end_lineno]
            selected = min(calls, key=lambda node: node.end_lineno - node.lineno) if calls else None
            if selected:
                source_line = selected.lineno
                excerpt = "\n".join(lines[selected.lineno - 1:selected.end_lineno])
            else:
                excerpt = lines[source_line - 1]
        else:
            excerpt = "# Called through the workflow helper."
        request = {"type": "request", "stepId": step_id, "label": label, "method": operation["method"], "path": route,
                   "edition": operation["edition"], "scope": operation["scope"], "doc": operation["doc"], "surface": "Public REST / organization API" if operation["scope"] == "Organization key" else "Public REST / project API", "auth": "Basic [redacted]",
                   "body": scrub(body), "query": list(query), "command": shlex.join(scrub(visible_argv)),
                   "source": {"file": "workflows.py", "line": source_line, "code": excerpt}}
        self.emit(request)
        start = time.monotonic()
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=45, cwd=str(REPO))
            payload = json.loads(result.stdout) if result.stdout.strip() else {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            self.emit({"type": "response", "stepId": step_id, "status": None, "body": {"error": "CLI request failed or timed out"}, "ok": False})
            raise WorkflowError("CLI request failed or timed out: " + label) from error
        status = payload.get("status")
        response = payload.get("body", {})
        ok = status in expected
        self.emit({"type": "response", "stepId": step_id, "status": status, "body": scrub(response), "ok": ok,
                   "durationMs": round((time.monotonic() - start) * 1000)})
        if not ok:
            message = response.get("message") or response.get("error") if isinstance(response, dict) else str(response)
            raise WorkflowError(scrub(str(message or result.stderr or "Unexpected CLI response"))[:500])
        return response

    def bridge(self, label, mode, method, path, body=None, persona=None, expected=(200, 201)):
        env = self.directory / "instance.env" if mode == "instance" else Path(persona["envFile"])
        if not env.is_file():
            raise WorkflowError("Instance API is not configured. Follow README setup for private/instance.env and the Compose override.")
        surface = "Instance Management REST API" if mode == "instance" else "Native UI RPC (pinned 4.76.0)" if mode == "rpc" else "Native signup (demo preparation)"
        visible_path = path if mode != "rpc" else "/api/trpc/" + path
        auth = "Bearer [redacted]" if mode == "instance" else "User session [redacted]" if mode == "rpc" else "Anonymous signup"
        self.calls += 1
        step_id = self.run_id + ":" + str(self.calls)
        caller = inspect.currentframe().f_back
        if caller.f_code.co_name in {"native", "instance"}:
            caller = caller.f_back
        source_line = caller.f_lineno
        wire_body = {"json": body} if mode == "rpc" else {"name": persona["name"], "email": persona["email"], "password": "[redacted]"} if mode == "signup" else body
        if mode == "rpc" and method == "GET":
            from urllib.parse import quote
            visible_path += "?input=" + quote(json.dumps(wire_body, separators=(",", ":")), safe="")
        self.emit({"type": "request", "stepId": step_id, "label": label, "method": method, "path": visible_path,
                   "edition": "enterprise" if mode != "signup" else "orchestration", "scope": surface, "surface": surface, "auth": auth,
                   "doc": "https://langfuse.com/self-hosting/administration/instance-management-api" if mode == "instance" else "https://langfuse.com/docs/administration/audit-logs" if path == "auditLogs.all" else "https://langfuse.com/self-hosting/security/authentication-and-sso" if mode == "signup" else "https://langfuse.com/faq/all/who-can-edit-prompts-and-labels",
                   "body": scrub(wire_body) if method != "GET" else None,
                   "command": "node --env-file=$PRIVATE_ENV scripts/platform-api.mjs  # request JSON on stdin; " + surface,
                   "source": {"file": "workflows.py", "line": source_line, "code": Path(__file__).read_text().splitlines()[source_line-1]}})
        request = {"host": HOST, "mode": mode, "method": method, "body": body}
        request.update({"procedure": path} if mode == "rpc" else {"path": path})
        if mode == "signup": request["name"] = persona["name"]
        start = time.monotonic()
        try:
            result = subprocess.run(["node", "--env-file=" + str(env), str(ROOT / "scripts/platform-api.mjs")],
                                    input=json.dumps(request), capture_output=True, text=True, timeout=80,
                                    env={k: v for k, v in os.environ.items() if k not in {"ADMIN_API_KEY", "DEMO_EMAIL", "DEMO_PASSWORD"}})
            payload = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise WorkflowError("Platform adapter failed or timed out. Check Node 22+ and local connectivity.") from error
        ok = payload.get("status") in expected
        self.emit({"type": "response", "stepId": step_id, **scrub(payload), "ok": ok,
                   "durationMs": round((time.monotonic()-start)*1000)})
        if not ok:
            raise WorkflowError("Platform adapter: " + str(scrub(payload.get("body")))[:700])
        return payload.get("body", {})

    def native(self, label, procedure, body, persona, expected=(200,)):
        method = "GET" if procedure in {"auditLogs.all", "prompts.getProtectedLabels"} else "POST"
        return self.bridge(label, "rpc", method, procedure, body, persona, expected)

    def instance(self, label, method, path, body=None, expected=(200, 201)):
        return self.bridge(label, "instance", method, path, body, expected=expected)

    def store_key(self, app, response):
        if not response.get("publicKey") or not response.get("secretKey"):
            raise WorkflowError("Key creation returned no usable credentials.")
        path = self.directory / "keys" / (app["id"] + "-" + response["id"] + ".env")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = "LANGFUSE_PUBLIC_KEY=" + response["publicKey"] + "\nLANGFUSE_SECRET_KEY=" + response["secretKey"] + "\nLANGFUSE_BASE_URL=" + HOST + "\n"
        with os.fdopen(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
            out.write(content)
        return str(path)


def issue_project_key(ctx, app, role, label, name):
    """Fail closed if this deployment ignores or rejects the selected key role."""
    key = ctx.call(label, "projects", "create-api-key", (app["id"],), body={"name": name, "role": role})
    if key.get("role") != role:
        ctx.call("Revoke key whose permissions were not confirmed", "projects", "delete-api-key", (app["id"], key["id"]))
        raise WorkflowError("Langfuse did not confirm the requested key role. The unverified key was revoked.")
    return key, ctx.store_key(app, key)


@contextmanager
def project_setup_access(ctx, app):
    """Use a temporary Full access key when the integration key cannot provision assets."""
    for key_id in list(app.get("pendingSetupKeyIds", [])):
        ctx.call("Clean up previous temporary setup key", "projects", "delete-api-key", (app["id"], key_id), expected=(200, 204, 404))
        app["pendingSetupKeyIds"].remove(key_id)
        ctx.save()
    if app.get("keyRole") in (None, "ADMIN", "LEGACY_PROJECT_API_KEY"):
        yield app["envFile"]
        return
    key, env = issue_project_key(ctx, app, "ADMIN", "Issue temporary setup key", app["name"] + " temporary portal setup")
    app.setdefault("pendingSetupKeyIds", []).append(key["id"])
    ctx.save()
    try:
        ctx.note("Temporary setup access", "The integration keeps its selected permissions. A separate temporary key provisions portal assets and is revoked afterward.")
        yield env
    finally:
        ctx.call("Revoke temporary setup key", "projects", "delete-api-key", (app["id"], key["id"]))
        app["pendingSetupKeyIds"].remove(key["id"])
        ctx.save()
        Path(env).unlink(missing_ok=True)


def verify_project_key(ctx, app, env, role):
    """Exercise an allowed operation and, for restricted keys, a denied operation."""
    if role in (None, "ADMIN", "LEGACY_PROJECT_API_KEY", "VIEWER"):
        ctx.call("Verify key can read the known prompt", "prompts", "get", (app["promptName"],), env=env, query=("--label", "development"))
    else:
        ctx.call("Verify key can submit a synthetic score", "scores", "create", env=env, body={
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, ctx.run_id + "/key-check")),
            "sessionId": "portal-key-check-" + app["id"], "name": "portal-key-permission-check", "value": 1,
            "dataType": "NUMERIC", "environment": "portal-demo", "comment": "Synthetic API-key permission check", "metadata": {"synthetic": True}}, expected=(200, 201, 202))
        ctx.call("Verify restricted key cannot read prompts", "prompts", "get", (app["promptName"],), env=env, query=("--label", "development"), expected=(403,))
    if role == "VIEWER":
        ctx.call("Verify Viewer key cannot create a prompt", "prompts", "create", env=env, body={
            "type": "text", "name": "portal-permission-check/" + ctx.run_id,
            "prompt": "Synthetic permission check; creation should be denied."}, expected=(403,))


def onboard_application(ctx, p):
    """Create real projects and keys with Enterprise APIs, then seed OSS application assets."""
    result_ids = []
    count = p.get("applicationCount", 1)
    for index in range(count):
        name = p["applicationName"] + (" " + str(index + 1) if count > 1 else "")
        app = next((a for a in ctx.state["applications"].values() if a["name"] == name), None)
        if not app:
            project = ctx.call("Create application: " + name, "projects", "create", body={
                "name": name, "retention": p.get("retentionDays", 30),
                "metadata": {"team": p["teamName"], "application": name, "demo": "langfuse-admin-portal", "runId": ctx.run_id}})
            app = {"id": project["id"], "name": name, "team": p["teamName"], "retentionDays": project.get("retentionDays", p.get("retentionDays", 30)), "members": [], "createdAt": time.time()}
            ctx.state["applications"][app["id"]] = app
            ctx.save()
        else:
            ctx.note("Reuse recorded application", name + " already exists in this portal's manifest.")
            if app["team"] != p["teamName"] or app["retentionDays"] != p.get("retentionDays", 30):
                raise WorkflowError("This application already exists with different team or retention settings. Use a new name; this create workflow does not overwrite existing settings.")
        prefix = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "application"
        if not app.get("keyId"):
            key, env = issue_project_key(ctx, app, p["keyRole"], "Issue application key: " + KEY_ROLES[p["keyRole"]], prefix + "-demo")
            app.update(keyId=key["id"], envFile=env, keyRole=p["keyRole"])
            ctx.save()
        elif app.get("keyRole") and app["keyRole"] != p["keyRole"]:
            raise WorkflowError("This application already has a different key permission level. Use Keys to rotate it with the new permissions.")
        with project_setup_access(ctx, app) as setup_env:
            if not app.get("promptName"):
                prompt = ctx.call("Seed starter prompt", "prompts", "create", env=setup_env, body={
                    "type": "text", "name": prefix + "/system", "prompt": "Answer using only the supplied application context.", "labels": ["development"]})
                app["promptName"] = prompt["name"]
                ctx.save()
            if not app.get("datasetName"):
                dataset = ctx.call("Create evaluation dataset", "datasets", "create", env=setup_env, body={"name": prefix + "/smoke-tests", "description": "Synthetic platform onboarding examples"})
                app["datasetName"] = dataset["name"]
                ctx.save()
            if not app.get("datasetItemId"):
                item = ctx.call("Seed a synthetic evaluation example", "dataset-items", "create", env=setup_env, body={
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, app["id"] + "/smoke-test")), "datasetName": app["datasetName"],
                    "input": {"question": "What should I do if I cannot verify the supplied context?"},
                    "expectedOutput": "State the limitation and request the missing context.", "metadata": {"synthetic": True}})
                app["datasetItemId"] = item["id"]
                ctx.save()
            ctx.call("Verify project identity with setup key", "projects", "list", env=setup_env)
            ctx.call("Read back the starter prompt", "prompts", "get", (app["promptName"],), env=setup_env, query=("--label", "development"))
            ctx.call("Read back the evaluation dataset", "datasets", "get", (app["datasetName"],), env=setup_env)
        if app.get("keyRole") not in (None, "ADMIN", "LEGACY_PROJECT_API_KEY"):
            verify_project_key(ctx, app, app["envFile"], app["keyRole"])
        result_ids.append(app["id"])
    return {"projectIds": result_ids, "message": str(len(result_ids)) + " application workspace(s) ready in Langfuse."}


def onboard_team(ctx, p):
    """SCIM-provision demo people, then assign their project-specific roles."""
    app = ctx.managed_app(p["projectId"])
    for member in p["members"]:
        email = member["email"]
        known = ctx.state["users"].get(email)
        matches = ctx.call("Resolve user: " + email, "scim", "list-users", query=("--filter", 'userName eq "' + email + '"', "--count", "100"))
        resources = matches.get("Resources", [])
        if resources and not known:
            raise WorkflowError("That email already belongs to an existing organization user. Use a fresh demo identity; existing users are preserved.")
        if not resources:
            user = ctx.call("Provision user: " + email, "scim", "create-user", body={
                "userName": email, "name": {"formatted": email.split("@")[0].replace(".", " ").title()}, "active": True})
            known = {"id": user["id"], "email": email}
            ctx.state["users"][email] = known
            ctx.save()
        org = ctx.call("Verify the user's organization role", "organizations", "list-memberships")
        org_member = next((m for m in org.get("memberships", []) if m["userId"] == known["id"]), None)
        if not org_member or org_member.get("role") != "NONE":
            raise WorkflowError("The demo requires organization role NONE before applying isolated project access.")
        ctx.call("Grant " + member["role"] + ": " + email, "projects", "update-membership", (app["id"],), body={"userId": known["id"], "role": member["role"]})
        existing = next((m for m in app["members"] if m["userId"] == known["id"]), None)
        if existing:
            existing.update(role=member["role"], active=True)
        else:
            app["members"].append({"userId": known["id"], "email": email, "role": member["role"], "active": True})
        ctx.save()
    actual = ctx.call("Read back project memberships", "projects", "list-memberships", (app["id"],))
    roles = {m["userId"]: m["role"] for m in actual.get("memberships", [])}
    if any(roles.get(m["userId"]) != m["role"] for m in app["members"] if m["active"]):
        raise WorkflowError("Membership verification did not match the requested roles.")
    return {"projectIds": [app["id"]], "message": "Team provisioned and project roles verified. Sign-in remains a separate SSO or password-reset workflow."}


def change_access(ctx, p):
    """Update an existing portal-managed person's actual project membership."""
    app = ctx.managed_app(p["projectId"])
    member = next((m for m in app["members"] if m["userId"] == p["userId"] and m["active"]), None)
    if not member:
        raise WorkflowError("Select an active member onboarded by this portal.")
    before = ctx.call("Capture previous project membership", "projects", "list-memberships", (app["id"],))
    previous = next((m["role"] for m in before.get("memberships", []) if m["userId"] == member["userId"]), None)
    ctx.call("Change project role to " + p["role"], "projects", "update-membership", (app["id"],), body={"userId": member["userId"], "role": p["role"]})
    member.update(previousRole=previous, role=p["role"])
    ctx.save()
    after = ctx.call("Verify the updated role", "projects", "list-memberships", (app["id"],))
    if not any(m["userId"] == member["userId"] and m["role"] == p["role"] for m in after.get("memberships", [])):
        raise WorkflowError("The requested role was not returned by the read-back check.")
    return {"projectIds": [app["id"]], "message": member["email"] + ": " + str(previous) + " to " + p["role"] + ". Verify UI capabilities in that person's own authenticated session."}


def rotate_key(ctx, p):
    """Switch this demo integration to a verified replacement key, then revoke its old key."""
    app = ctx.managed_app(p["projectId"])
    old_id, old_env = app["keyId"], app["envFile"]
    key, new_env = issue_project_key(ctx, app, p["keyRole"], "Create replacement key: " + KEY_ROLES[p["keyRole"]], app["name"] + " replacement")
    try:
        verify_project_key(ctx, app, new_env, p["keyRole"])
    except WorkflowError:
        ctx.call("Revoke replacement that failed verification; keep current key", "projects", "delete-api-key", (app["id"], key["id"]))
        Path(new_env).unlink(missing_ok=True)
        raise
    app.update(keyId=key["id"], envFile=new_env, keyRole=p["keyRole"], previousKeyId=old_id, previousEnvFile=old_env)
    ctx.save()
    ctx.note("Demo integration switched", "Subsequent portal project calls now use the verified replacement key. External applications are not reconfigured.")
    ctx.call("Revoke the previous integration key", "projects", "delete-api-key", (app["id"], old_id))
    inventory = ctx.call("Verify replacement role and old key removal", "projects", "list-api-keys", (app["id"],))
    keys = inventory.get("apiKeys", [])
    if any(k["id"] == old_id for k in keys) or not any(k["id"] == key["id"] and k.get("role") == p["keyRole"] for k in keys):
        raise WorkflowError("Key inventory did not confirm the replacement role and old key removal.")
    ctx.call("Verify the old key is rejected", "prompts", "list", env=old_env, query=("--limit", "1"), expected=(401, 403))
    verify_project_key(ctx, app, new_env, p["keyRole"])
    return {"projectIds": [app["id"]], "message": "Demo integration key rotated with " + KEY_ROLES[p["keyRole"]] + " permissions; allowed operations succeed and the previous key is rejected."}


def provision_dashboard(ctx, p):
    """Create real dashboard, chart, and placement objects through the OSS project API."""
    app = ctx.managed_app(p["projectId"])
    with project_setup_access(ctx, app) as setup_env:
        if not app.get("dashboardId"):
            dashboard = ctx.call("Create operational dashboard", "unstable-dashboards", "create", env=setup_env, body={"name": app["name"] + " operations", "description": "Created by the local platform demo"})
            app["dashboardId"] = dashboard["id"]
            ctx.save()
        if not app.get("widgetId"):
            widget = ctx.call("Create P95 latency widget", "unstable-dashboard-widgets", "create", env=setup_env, body={
                "name": "P95 observation latency", "view": "observations", "dimensions": [], "metrics": [{"measure": "latency", "agg": "p95"}], "filters": [], "chartType": "NUMBER"})
            app["widgetId"] = widget["id"]
            ctx.save()
        if not app.get("placementId"):
            placement = ctx.call("Place the widget on the dashboard", "unstable-dashboards", "add-placement", (app["dashboardId"],), env=setup_env, body={"type": "widget", "widgetId": app["widgetId"], "width": 6, "height": 6})
            app["placementId"] = placement["id"]
            ctx.save()
        ctx.call("Read back the created dashboard", "unstable-dashboards", "get", (app["dashboardId"],), env=setup_env)
    return {"projectIds": [app["id"]], "message": "Dashboard objects created and verified. Charts populate when this application sends observations. These dashboard endpoints are unstable."}


def offboard_team(ctx, p):
    """Remove only project grants created by this portal; preserve people, data, and application keys."""
    app = ctx.managed_app(p["projectId"])
    members = [m for m in app["members"] if m["active"]]
    if not members:
        raise WorkflowError("This application has no active portal-managed team memberships.")
    org = ctx.call("Check organization inheritance before offboarding", "organizations", "list-memberships")
    org_roles = {m["userId"]: m["role"] for m in org.get("memberships", [])}
    if any(org_roles.get(m["userId"]) not in (None, "NONE") for m in members):
        raise WorkflowError("A team member has inherited organization access. Removing the project grant would not prove offboarding, so no grants were removed.")
    for member in members:
        ctx.call("Remove project access: " + member["email"], "projects", "delete-membership", (app["id"],), body={"userId": member["userId"]})
        member["active"] = False
        ctx.save()
    after = ctx.call("Verify the team's project grants are absent", "projects", "list-memberships", (app["id"],))
    remaining = {m["userId"] for m in after.get("memberships", [])}
    if any(m["userId"] in remaining for m in members):
        raise WorkflowError("A membership still exists after removal.")
    verify_project_key(ctx, app, app["envFile"], app.get("keyRole"))
    return {"projectIds": [app["id"]], "message": "Team project grants removed and verified; application data, organization memberships, and the integration key are preserved."}


def organization_projects(ctx, label):
    payload = ctx.call(label, "organizations", "list-projects")
    projects = payload.get("projects")
    if not isinstance(projects, list) or any(not isinstance(p, dict) or not p.get("id") for p in projects):
        raise WorkflowError("The organization inventory could not be verified. No reset is safe without a complete project list.")
    return projects


def inspect_reset_users(ctx, projects, managed_ids):
    """Protect demo identities that now have organization privileges or access to other projects."""
    users = list(ctx.state.get("users", {}).values())
    if not users:
        return []
    membership_payload = ctx.call("Check demo users' organization access", "organizations", "list-memberships")
    memberships = membership_payload.get("memberships")
    if not isinstance(memberships, list):
        raise WorkflowError("Could not verify organization memberships for reset.")
    org_members = {member["userId"]: member for member in memberships}
    outside_grants = {}
    for project in projects:
        if project["id"] in managed_ids:
            continue
        payload = ctx.call("Protect access to other project: " + project["name"], "projects", "list-memberships", (project["id"],))
        grants = payload.get("memberships")
        if not isinstance(grants, list):
            raise WorkflowError("Could not verify memberships of a project outside this demo.")
        for grant in grants:
            outside_grants.setdefault(grant["userId"], []).append(project["name"])
    result = []
    for user in sorted(users, key=lambda item: item["id"]):
        member = org_members.get(user["id"])
        reason = ""
        if member and member.get("role") != "NONE":
            reason = "Has organization role " + str(member.get("role")) + "; organization access is preserved."
        elif user["id"] in outside_grants:
            reason = "Has access to other projects: " + ", ".join(outside_grants[user["id"]])
        elif member and str(member.get("email", "")).lower() != user["email"].lower():
            reason = "The recorded identity no longer matches the organization member."
        result.append({"id": user["id"], "email": user["email"], "present": member is not None, "protected": bool(reason), "reason": reason})
    return result


def reset_preview(ctx):
    """Read-only preview, bound to the current manifest and verified live ownership."""
    projects = organization_projects(ctx, "Inventory projects before reset")
    live = {project["id"]: project for project in projects}
    managed = ctx.state.get("applications", {})
    rows = []
    for project_id, app in sorted(managed.items()):
        current = live.get(project_id)
        reason = ""
        if app.get("id") != project_id or not re.fullmatch(r"[A-Za-z0-9_-]+", project_id):
            reason = "Invalid project identity in the demo manifest."
        elif current and (current.get("metadata") or {}).get("demo") != "langfuse-admin-portal":
            reason = "The live project no longer carries this portal's ownership marker."
        rows.append({"id": project_id, "name": current["name"] if current else app["name"], "present": current is not None,
                     "blocked": bool(reason), "reason": reason,
                     "prompt": app.get("promptName"), "dataset": app.get("datasetName"), "dashboardId": app.get("dashboardId")})
    users = inspect_reset_users(ctx, projects, set(managed))
    organizations = instance_reset_preview(ctx)
    fingerprint = hashlib.sha256(json.dumps({"manifest": ctx.state, "projects": rows, "users": users, "organizations": organizations}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    removable_users = [u for u in users if not u["protected"]]
    return {"projects": rows, "organizations": organizations, "users": removable_users, "protectedUsers": [u for u in users if u["protected"]],
            "preservedProjectCount": sum(project["id"] not in managed for project in projects),
            "fingerprint": fingerprint, "canReset": bool(rows or removable_users or organizations) and not any(p["blocked"] for p in rows + organizations),
            "userAccountNote": "SCIM removes demo users from this organization; their global Langfuse accounts remain.",
            "deletionNote": "Project deletion is irreversible. All contents are included; background data purging may continue after the projects disappear from inventory."}


def remove_project_key_files(ctx, project_id):
    """Delete only generated key files for a verified deleted project; never read credential contents."""
    keys = ctx.directory / "keys"
    if keys.is_symlink():
        raise WorkflowError("The generated-key directory is a symbolic link; local key cleanup was stopped.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project_id):
        raise WorkflowError("Invalid project identity for local key cleanup.")
    count = 0
    for path in keys.glob(project_id + "-*.env"):
        if path.stem.rsplit("-", 1)[0] == project_id and path.is_file() and not path.is_symlink() and path.resolve() != Path(ctx.org_env).resolve():
            path.unlink()
            count += 1
    return count


def reset_demo(ctx, p):
    """Delete only the reviewed portal projects and eligible demo memberships, then reset local records."""
    p = validate("reset", p)
    preview = reset_preview(ctx)
    if preview["fingerprint"] != p["previewFingerprint"]:
        raise WorkflowError("The reset inventory changed since preview. Preview again before deleting anything.")
    if not preview["canReset"]:
        raise WorkflowError("There is nothing eligible to reset, or a project's ownership check failed. Review the preview.")
    pending_orgs = reset_instance_organizations(ctx, preview["organizations"])
    project_ids = {project["id"] for project in preview["projects"]}
    errors, requested, removed, files_removed = [], [], [], 0
    for project in preview["projects"]:
        if not project["present"]:
            continue
        try:
            result = ctx.call("Delete demo project and all contents: " + project["name"], "projects", "delete", (project["id"],), expected=(202, 404))
            if result.get("success") is False:
                raise WorkflowError("Langfuse did not accept the project deletion.")
            ctx.state["applications"][project["id"]]["resetStatus"] = "deletion_requested"
            ctx.save()
            requested.append(project["id"])
        except WorkflowError as error:
            errors.append(project["name"] + ": " + str(error))
    for attempt in range(5):
        current_projects = organization_projects(ctx, "Verify demo projects are no longer listed")
        present = {project["id"] for project in current_projects}
        if not project_ids.intersection(present) or attempt == 4:
            break
        time.sleep(1)
    for project_id in sorted(project_ids - present):
        try:
            files_removed += remove_project_key_files(ctx, project_id)
            ctx.state["applications"].pop(project_id, None)
            ctx.save()
            removed.append(project_id)
        except (OSError, WorkflowError) as error:
            errors.append("Local cleanup for " + project_id + ": " + str(error))
    # Recheck outside access immediately before deprovisioning identities.
    users = inspect_reset_users(ctx, current_projects, project_ids)
    reviewed_users = {user["id"] for user in preview["users"]}
    users_removed = 0
    for user in users:
        if user["protected"] or user["id"] not in reviewed_users:
            ctx.note("Demo user preserved: " + user["email"], user["reason"] or "This identity was not eligible in the reviewed preview.")
            continue
        try:
            if user["present"]:
                ctx.call("Remove demo user from organization: " + user["email"], "scim", "delete-user", (user["id"],), expected=(200, 204, 404))
            ctx.call("Verify demo organization membership is absent: " + user["email"], "scim", "get-user", (user["id"],), expected=(404,))
            ctx.state["users"].pop(user["email"], None)
            ctx.save()
            users_removed += 1
        except WorkflowError as error:
            errors.append(user["email"] + ": " + str(error))
    governance = ctx.state.get("governance", {})
    if governance.get("projectId") and governance["projectId"] not in ctx.state["applications"]:
        for persona in governance.get("personas", {}).values():
            if persona["email"] not in ctx.state["users"]:
                path = Path(persona["envFile"])
                if path.parent.resolve() == (ctx.directory / "personas").resolve() and not path.is_symlink():
                    path.unlink(missing_ok=True)
        if all(p["email"] not in ctx.state["users"] for p in governance.get("personas", {}).values()):
            ctx.state.pop("governance", None)
        ctx.save()
    if errors:
        raise WorkflowError("Reset partially completed. Remaining records were kept for retry. " + "; ".join(errors))
    pending = sorted(project_ids.intersection(present))
    message = str(len(removed)) + " demo project(s) removed from inventory; " + str(users_removed) + " demo organization membership(s) cleared; " + str(files_removed) + " generated key file(s) removed."
    if pending:
        message += " " + str(len(pending)) + " project deletion(s) still pending; preview and retry to finish local cleanup."
    message += " " + str(len(preview["organizations"]) - len(pending_orgs)) + " demo organization(s) cleared."
    if pending_orgs:
        message += " " + str(len(pending_orgs)) + " organization deletion(s) await background project purging; preview and retry Reset."
    message += " Global user accounts and other projects remain. Background project data purging may continue."
    ctx.note("Reset scope", "Organization credentials and sanitized run history are retained. New demos can be onboarded again.")
    return {"projectIds": [], "removedProjectIds": removed, "deletionRequestedIds": requested, "pendingProjectIds": pending, "pendingOrganizationIds": pending_orgs, "message": message}


def prepare_governance(ctx, name=None):
    """Create a dedicated neutral project plus real Member and Admin sign-in personas."""
    governance = ctx.state.setdefault("governance", {})
    app = ctx.state["applications"].get(governance.get("projectId"))
    if not app:
        name = name or "Governance Demo"
        if any(a["name"] == name for a in ctx.state["applications"].values()):
            raise WorkflowError("Choose a fresh governance project name; existing application history is preserved.")
        result = onboard_application(ctx, {"applicationName": name, "teamName": "Platform governance", "retentionDays": 30, "keyRole": "ADMIN"})
        app = ctx.managed_app(result["projectIds"][0])
        governance["projectId"] = app["id"]
        ctx.save()
    elif name and name != app["name"]:
        raise WorkflowError("Governance is already prepared for " + app["name"] + ". Reset before choosing a different name.")
    personas = governance.setdefault("personas", {})
    for role in ("MEMBER", "ADMIN"):
        if role not in personas:
            tag = uuid.uuid4().hex[:10]
            email = "operations." + role.lower() + "+" + tag + "@example.invalid"
            path = ctx.directory / "personas" / (role.lower() + "-" + tag + ".env")
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with os.fdopen(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as out:
                out.write("DEMO_EMAIL=" + email + "\nDEMO_PASSWORD=Ops!" + secrets.token_hex(24) + "\n")
            personas[role] = {"email": email, "name": "Operations " + role.title(), "envFile": str(path), "registered": False}
            ctx.save()
        persona = personas[role]
        if not persona.get("registered"):
            ctx.bridge("Prepare native " + role.title() + " sign-in identity", "signup", "POST", "/api/auth/signup", persona=persona)
            persona["registered"] = True
            ctx.save()
        onboard_team(ctx, {"projectId": app["id"], "members": [{"email": persona["email"], "role": role}]})
    return app, governance


def protected_labels(ctx, p):
    """Prove a Member can edit ordinary labels, is denied production, and an Admin can promote."""
    app, governance = prepare_governance(ctx, p["governanceName"])
    admin, member = governance["personas"]["ADMIN"], governance["personas"]["MEMBER"]
    name = "governance-demo/release"
    with project_setup_access(ctx, app) as env:
        versions = governance.setdefault("versions", [])
        for number in (1, 2):
            if len(versions) < number:
                prompt = ctx.call("Create governed prompt version " + str(number), "prompts", "create", env=env,
                                  body={"name": name, "type": "text", "prompt": "Synthetic release candidate " + str(number), "labels": ["production"] if number == 1 else ["development"]})
                versions.append({"id": prompt.get("id"), "version": prompt["version"]})
                ctx.save()
        # Public prompt writes intentionally remain available for CI/service accounts.
        ctx.call("Reset production to baseline via writable service key", "prompts", "update-version", (name, versions[0]["version"]), env=env, body={"newLabels": ["production"]})
        ctx.native("Protect the production label as Admin", "prompts.addProtectedLabel", {"projectId": app["id"], "label": "production"}, admin)
        protected = ctx.native("Read back the protected-label policy", "prompts.getProtectedLabels", {"projectId": app["id"]}, admin)
        if "production" not in protected:
            raise WorkflowError("Protected-label policy was not persisted.")
        candidate_id = versions[1]["id"]
        if not candidate_id:
            raise WorkflowError("Prompt ID missing from server response; native adapter contract needs review.")
        base = {"projectId": app["id"], "promptId": candidate_id}
        ctx.native("Member successfully changes an ordinary label", "prompts.setLabels", {**base, "labels": ["development", "staging"]}, member)
        ctx.native("Expected denial: Member attempts production promotion", "prompts.setLabels", {**base, "labels": ["development", "staging", "production"]}, member, expected=(403,))
        baseline = ctx.call("Verify denied promotion did not change production", "prompts", "get", (name,), env=env, query=("--label", "production"))
        if baseline["version"] != versions[0]["version"]:
            raise WorkflowError("Production changed despite denied Member promotion.")
        ctx.native("Admin promotes the candidate to production", "prompts.setLabels", {**base, "labels": ["development", "staging", "production"]}, admin)
        promoted = ctx.call("Read production after Admin promotion", "prompts", "get", (name,), env=env, query=("--label", "production"))
        if promoted["version"] != versions[1]["version"]:
            raise WorkflowError("Admin promotion did not select the expected version.")
    ctx.emit({"type": "governance", "projectId": app["id"], "checks": ["Member changed staging: allowed", "Member changed production: HTTP 403", "Production unchanged after denial", "Admin changed production: allowed", "Production now resolves to version " + str(promoted["version"])]})
    return {"projectIds": [app["id"]], "message": "Protected production verified with real Member and Admin sessions. Writable project keys retain CI promotion access."}


def audit_evidence(ctx, p):
    """Correlate a real role change with the native actor, timestamp, and before/after evidence."""
    from datetime import datetime
    app, governance = prepare_governance(ctx)
    user = ctx.state["users"][governance["personas"]["MEMBER"]["email"]]
    started = time.time()
    try:
        ctx.call("Create an auditable role change: Member to Viewer", "projects", "update-membership", (app["id"],), body={"userId": user["id"], "role": "VIEWER"})
        result = ctx.native("Read native project audit history", "auditLogs.all", {"projectId": app["id"], "page": 0, "limit": 50}, governance["personas"]["ADMIN"])
        rows = result.get("data", [])
        for row in rows:
            for field in ("before", "after"):
                if isinstance(row.get(field), str):
                    try: row[field] = json.loads(row[field])
                    except json.JSONDecodeError: pass
        matched = [row for row in rows if row.get("resourceType") == "projectMembership"
                   and isinstance(row.get("after"), dict) and row["after"].get("userId") == user["id"]
                   and row["after"].get("role") == "VIEWER" and isinstance(row.get("before"), dict)
                   and row["before"].get("role") == "MEMBER"
                   and datetime.fromisoformat(row["createdAt"].replace("Z", "+00:00")).timestamp() >= started]
        if not matched:
            raise WorkflowError("The role change was not found in native audit evidence. The portal log alone is not proof.")
        ctx.emit({"type": "audit", "projectId": app["id"], "records": scrub(rows), "matchedIds": [row["id"] for row in matched], "totalCount": result.get("totalCount"), "url": HOST + "/project/" + app["id"] + "/settings/audit-logs"})
    finally:
        ctx.call("Restore the demo user's Member role", "projects", "update-membership", (app["id"],), body={"userId": user["id"], "role": "MEMBER"})
    return {"projectIds": [app["id"]], "message": "Native audit evidence verified for Member → Viewer, with actor, timestamp, and before/after values. Member role restored."}


def provision_organization(ctx, p):
    """Create an instance-level organization, bootstrap its org key, then its first project."""
    orgs = ctx.state.setdefault("organizations", {})
    name = p["organizationName"]
    org = next((o for o in orgs.values() if o["name"] == name), None)
    if not org:
        # Inventory prevents accidental duplicate creation or adopting an unrelated organization.
        inventory = ctx.instance("Inventory organizations across the instance", "GET", "/api/admin/organizations")
        if any(o["name"] == name for o in inventory.get("organizations", [])):
            raise WorkflowError("An unmanaged organization already uses that name. Choose a new demo name.")
        created = ctx.instance("Create isolated organization", "POST", "/api/admin/organizations", {"name": name, "metadata": {"demo": "langfuse-operations", "runId": ctx.run_id}})
        org = {"id": created["id"], "name": created["name"]}
        orgs[org["id"]] = org
        ctx.save()
    if org.get("resetStatus"):
        raise WorkflowError("This organization has pending cleanup. Use Reset to finish before provisioning it again.")
    path = "/api/admin/organizations/" + org["id"]
    current = ctx.instance("Verify recorded organization ownership", "GET", path)
    if (current.get("metadata") or {}).get("demo") != "langfuse-operations" or any(p["id"] != org.get("projectId") for p in current.get("projects", [])):
        raise WorkflowError("Recorded organization ownership or project inventory changed. No further provisioning was attempted.")
    if not org.get("envFile"):
        key = ctx.instance("Bootstrap organization administration key", "POST", path + "/apiKeys", {"name": "operations-demo-bootstrap"})
        org.update(keyId=key["id"], envFile=ctx.store_key(org, key))
        ctx.save()
    if not org.get("projectId"):
        project = ctx.call("Create the organization's first application", "projects", "create", env=org["envFile"],
                           body={"name": name + " / Starter application", "retention": 30, "metadata": {"demo": "langfuse-operations", "organizationId": org["id"]}})
        org.update(projectId=project["id"], projectName=project["name"])
        ctx.save()
    # Give an existing presenter visibility without creating or changing a global account.
    members = ctx.call("Resolve the local presenter", "organizations", "list-memberships")
    presenter = next((m for m in members.get("memberships", []) if m.get("email") == "demo@example.com"), None)
    if presenter:
        ctx.call("Give the local presenter access to the new organization", "organizations", "update-membership", env=org["envFile"], body={"userId": presenter["userId"], "role": "ADMIN"})
        org["presenterId"] = presenter["userId"]
        ctx.save()
    verified = ctx.instance("Verify isolated organization and project inventory", "GET", path)
    if org["projectId"] not in {p["id"] for p in verified.get("projects", [])}:
        raise WorkflowError("New organization project inventory did not match.")
    return {"organizationIds": [org["id"]], "message": "Organization, organization key, and starter project created and verified using instance Bearer and organization Basic authentication."}


def instance_reset_preview(ctx):
    rows = []
    for org_id, org in sorted(ctx.state.get("organizations", {}).items()):
        reason, projects, present = "", [], True
        if org.get("id") != org_id or org_id == ctx.state.get("organizationId") or not re.fullmatch(r"[A-Za-z0-9_-]+", org_id):
            reason = "Organization identity failed the ownership check."
        else:
            try:
                current = ctx.instance("Verify demo organization ownership", "GET", "/api/admin/organizations/" + org_id, expected=(200, 404))
                present = bool(current.get("id"))
                if present:
                    projects = current.get("projects", [])
                    if (current.get("metadata") or {}).get("demo") != "langfuse-operations":
                        reason = "Organization ownership marker is missing."
                    elif any(project["id"] != org.get("projectId") or (project.get("metadata") or {}).get("demo") != "langfuse-operations" for project in projects):
                        reason = "Organization contains unrecorded or unmarked projects."
                    elif org.get("envFile"):
                        memberships = ctx.call("Verify isolated organization membership scope", "organizations", "list-memberships", env=org["envFile"])
                        keys = ctx.instance("Verify isolated organization key scope", "GET", "/api/admin/organizations/" + org_id + "/apiKeys")
                        if any(m["userId"] != org.get("presenterId") for m in memberships.get("memberships", [])):
                            reason = "Organization contains unrecorded memberships."
                        elif any(k["id"] != org.get("keyId") for k in keys.get("apiKeys", [])):
                            reason = "Organization contains unrecorded administration keys."
            except WorkflowError as error:
                reason = str(error)
        rows.append({"id": org_id, "name": org["name"], "present": present, "blocked": bool(reason), "reason": reason, "projects": [{"id": p["id"], "name": p["name"]} for p in projects]})
    return rows


def reset_instance_organizations(ctx, rows):
    pending = []
    if rows and instance_reset_preview(ctx) != rows:
        raise WorkflowError("Organization reset scope changed. Preview again before deleting.")
    for row in rows:
        org = ctx.state["organizations"][row["id"]]
        path = "/api/admin/organizations/" + row["id"]
        if row["present"]:
            # Recheck immediately before deleting. Refuse unexpected additions.
            current = ctx.instance("Recheck organization deletion scope", "GET", path)
            if (current.get("metadata") or {}).get("demo") != "langfuse-operations" or any(p["id"] != org.get("projectId") or (p.get("metadata") or {}).get("demo") != "langfuse-operations" for p in current.get("projects", [])):
                raise WorkflowError("Organization inventory changed. Preview again.")
            org["resetStatus"] = "deletion_requested"
            ctx.save()
            for project in current.get("projects", []):
                ctx.call("Delete isolated starter project", "projects", "delete", (project["id"],), env=org["envFile"], expected=(202, 404))
            for attempt in range(10):
                empty = ctx.instance("Verify organization is empty", "GET", path)
                if not empty.get("projects"): break
                time.sleep(1)
            if empty.get("projects"):
                raise WorkflowError("Organization project deletion is pending. Preview and retry reset.")
            result = ctx.instance("Delete the empty demo organization", "DELETE", path, expected=(200, 400))
            if not result.get("success"):
                if result.get("error") == "Cannot delete organization with existing projects":
                    pending.append(row["id"])
                    ctx.note("Organization cleanup pending: " + org["name"], "Background project purging must finish before Langfuse allows organization deletion. Preview and retry Reset; records and credentials were kept.")
                    continue
                raise WorkflowError("Organization deletion was not accepted; records were kept for retry.")
            ctx.instance("Verify organization was deleted", "GET", path, expected=(404,))
        remove_project_key_files(ctx, row["id"])
        ctx.state["organizations"].pop(row["id"])
        ctx.save()
    return pending


FUNCTIONS = {"application": onboard_application, "team": onboard_team, "permissions": change_access, "keys": rotate_key, "dashboard": provision_dashboard, "offboarding": offboard_team, "reset": reset_demo, "governance": protected_labels, "audit": audit_evidence, "instance": provision_organization}


def script_source(workflow):
    info = next(w for w in WORKFLOWS if w["id"] == workflow)
    function = FUNCTIONS[workflow]
    return {"entryFile": "scripts/" + info["script"], "entry": (ROOT / "scripts" / info["script"]).read_text(), "file": "workflows.py", "line": inspect.getsourcelines(function)[1], "code": inspect.getsource(function)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", choices=FUNCTIONS)
    parser.add_argument("--params-file", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--org-env", required=True)
    parser.add_argument("--run-id", default=str(uuid.uuid4()))
    args = parser.parse_args()
    os.umask(0o077)
    directory = Path(args.state_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "workflow.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            params = validate(args.workflow, json.loads(Path(args.params_file).read_text()))
            ctx = Context(directory, args.org_env, args.run_id)
            result = FUNCTIONS[args.workflow](ctx, params)
            ctx.emit({"type": "complete", "result": result, "state": public_state(ctx.state), "apiCalls": ctx.calls})
        except (WorkflowError, BlockingIOError) as error:
            print(json.dumps({"type": "error", "message": scrub(str(error))}), flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
