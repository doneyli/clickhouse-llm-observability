#!/usr/bin/env python3
"""Readiness check for langfuse-enterprise-operations: reads the instance back, says READY or NOT READY.

    python3 scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    python3 scripts/verify_demo.py --links    # also print the link each act opens

This demo writes no traces, so there is no trace or score to count. It is ready when the
licensed local instance, the organization key, the running portal and the seeded
governance project all read back correctly. Every Langfuse read goes through the same
credential path the portal uses: the vendored CLI wrapper (`scripts/langfuse-connect.sh
--env-file`) for public REST, and `node --env-file` with `scripts/platform-api.mjs` for the
instance API. Python never reads a credential file, and shell-exported LANGFUSE_* keys are
stripped from every child process. Standard library only, no model calls, no writes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEMO_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DEMO_DIR.parent.parent
sys.path.insert(0, str(DEMO_DIR))
import workflows as wf  # noqa: E402  (stdlib only; gives HOST, WRAPPER and the manifest loader)

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with workflows.py and platform-api.mjs.
SERVER_VERSION = "4.76.0"                    # scripts/platform-api.mjs refuses any other version
PORTAL_URL = "http://127.0.0.1:8091"         # server.py --port default
PRESENTER_EMAIL = "demo@example.com"         # provision_organization grants this user Admin when present
GOVERNANCE_PROMPT = "governance-demo/release"  # protected_labels() creates two versions of it
MIN_PYTHON = (3, 10)
MIN_NODE = 22
SEED_CMD = "python3 scripts/seed_all.py"
RUN_CMD = "./scripts/run-portal.sh"
TIMEOUT_S = 45                               # the same per-call limit Context.call uses

RESULTS: List[Tuple[str, bool, str]] = []    # (PASS | FAIL | WARN, required, what)

Runner = Callable[..., Any]                  # subprocess.run, or a fake in tests


# --- Result helpers ---------------------------------------------------------------------

def check(ok: bool, what: str, *, required: bool = True, hint: str = "") -> bool:
    """Record one check and print its line. A failed optional check is a WARN, not a FAIL."""
    status = "PASS" if ok else ("FAIL" if required else "WARN")
    RESULTS.append((status, required, what))
    print(f"  {status}  {what}" + (f"  ->  {hint}" if hint and not ok else ""))
    return ok


def summary(results: Optional[List[Tuple[str, bool, str]]] = None) -> Tuple[str, int]:
    """The closing line and the exit code: 0 when no required check failed, 1 otherwise."""
    rs = RESULTS if results is None else results
    passed = sum(1 for s, _, _ in rs if s == "PASS")
    failed = sum(1 for s, _, _ in rs if s == "FAIL")
    warned = sum(1 for s, _, _ in rs if s == "WARN")
    detail = f"{passed}/{len(rs)} checks passed"
    if failed:
        detail += f", {failed} required failed"
    if warned:
        detail += f", {warned} warning{'s' if warned != 1 else ''}"
    return (f"NOT READY ({detail})", 1) if failed else (f"READY ({detail})", 0)


# --- Manifest and versions --------------------------------------------------------------

def _strip_comment(line: str) -> str:
    quote = ""
    for i, ch in enumerate(line):
        if ch in "\"'" and not quote:
            quote = ch
        elif ch == quote:
            quote = ""
        elif ch == "#" and not quote and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def manifest_value(text: str, section: str, key: str) -> Optional[str]:
    """Read `section.key` from demo.yaml (`section=""` for a top-level key) without PyYAML."""
    current = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        m = re.match(r"\s*([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        if not line[0].isspace():
            current = m.group(1)
            if section == "" and m.group(1) == key:
                return _unquote(m.group(2).strip()) or None
        elif current == section and m.group(1) == key:
            return _unquote(m.group(2).strip()) or None
    return None


def major_version(text: str) -> Optional[int]:
    """`v24.13.0` -> 24. None when the text has no version number."""
    m = re.search(r"(\d+)\.\d+", text or "")
    return int(m.group(1)) if m else None


def private_mode_ok(path: Path) -> bool:
    """True when no group or other permission bits are set (the README asks for 0600)."""
    return (path.stat().st_mode & 0o077) == 0


KEEP = {"LANGFUSE_CLI_BIN"}                 # the wrapper's runner override is not a credential


def child_env(drop: Tuple[str, ...] = ()) -> Dict[str, str]:
    """The child's environment without shell LANGFUSE_* keys and hosts (and any named secrets)."""
    return {k: v for k, v in os.environ.items() if (k in KEEP or not k.startswith("LANGFUSE_")) and k not in drop}


# --- Reads ----------------------------------------------------------------------------------

def cli(env_file: Path, resource: str, action: str, *args: str, runner: Runner = subprocess.run) -> Tuple[Optional[int], Any]:
    """One public REST read through the vendored CLI wrapper, like Context.call. Returns (status, body)."""
    argv = [str(wf.WRAPPER), "--env-file", str(env_file), "--host", wf.HOST, "api", resource, action, *args, "--json"]
    try:
        result = runner(argv, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=str(REPO_ROOT), env=child_env())
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return None, f"CLI call failed: {type(error).__name__}"
    if not isinstance(payload, dict) or payload.get("status") is None:
        return None, (result.stderr or "no response from the Langfuse CLI").strip()[-300:]
    return payload.get("status"), payload.get("body")


def instance_get(env_file: Path, path: str, runner: Runner = subprocess.run) -> Tuple[Optional[int], Any]:
    """One instance-API GET through scripts/platform-api.mjs, like Context.instance. Returns (status, body)."""
    request = {"host": wf.HOST, "mode": "instance", "method": "GET", "path": path, "body": None}
    try:
        result = runner(["node", "--env-file=" + str(env_file), str(DEMO_DIR / "scripts" / "platform-api.mjs")],
                        input=json.dumps(request), capture_output=True, text=True, timeout=TIMEOUT_S,
                        env=child_env(("ADMIN_API_KEY", "DEMO_EMAIL", "DEMO_PASSWORD")))
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return None, f"platform adapter failed: {type(error).__name__}"
    return (payload.get("status") or None), payload.get("body")


def http_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 5) -> Tuple[Optional[int], Any]:
    req = urllib.request.Request(url, headers=dict({"Accept": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError) as e:
        return None, f"{type(e).__name__}: {getattr(e, 'reason', e)}"
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def node_version(runner: Runner = subprocess.run) -> str:
    try:
        return runner(["node", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


# --- Pure logic (covered by tests/test_verify_demo.py) -----------------------------------

def manifest_checks(state: Dict[str, Any], live_ids: set) -> List[Tuple[bool, str, bool, str]]:
    """(ok, what, required, hint) for the portal manifest against the live project inventory."""
    out: List[Tuple[bool, str, bool, str]] = []
    governance = state.get("governance") or {}
    gov_id = governance.get("projectId")
    out.append((bool(gov_id) and gov_id in live_ids,
                f"governance project recorded and listed ({gov_id or 'none recorded'})", True, f"run {SEED_CMD}"))
    personas = governance.get("personas") or {}
    registered = sorted(r for r, p in personas.items() if p.get("registered"))
    out.append((registered == ["ADMIN", "MEMBER"],
                f"native sign-in personas registered: {', '.join(registered) or 'none'} (want ADMIN, MEMBER)", True,
                f"run {SEED_CMD}; a signup policy, email verification or SSO enforcement can block it"))
    out.append((len(governance.get("versions") or []) == 2,
                f"governed prompt versions recorded: {len(governance.get('versions') or [])} (want 2)", True, f"run {SEED_CMD}"))
    apps = state.get("applications") or {}
    others = sorted(pid for pid in apps if pid != gov_id)
    stale = [pid for pid in others if pid not in live_ids]
    out.append((not stale, f"recorded applications listed in the organization: {len(others) - len(stale)} of {len(others)}",
                False, "stale records: preview Reset in the portal, or move private/ aside for a clean start"))
    orgs = state.get("organizations") or {}
    pending = sorted(o.get("name", oid) for oid, o in orgs.items() if o.get("resetStatus"))
    out.append((not pending, f"isolated organizations with cleanup pending: {', '.join(pending) or 'none'}",
                False, "preview and retry Reset before the Organizations act"))
    return out


def governance_version_ok(body: Any, governance: Dict[str, Any]) -> bool:
    """The production label resolves to one of the two versions the governance run recorded."""
    versions = {v.get("version") for v in governance.get("versions") or []}
    return isinstance(body, dict) and body.get("version") in versions


def org_name_for(organizations: List[Dict[str, Any]], live_ids: set) -> Optional[str]:
    """The name of the organization whose projects overlap the org key's project inventory."""
    for org in organizations:
        if live_ids & {p.get("id") for p in org.get("projects") or [] if isinstance(p, dict)}:
            return org.get("name")
    return None


def act_links(host: str, portal: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """One (label, url) per act of the demo script, in act order."""
    links = [("Portal (every act)", portal + "/"), ("Langfuse", host)]
    governance = state.get("governance") or {}
    for app in (state.get("applications") or {}).values():
        if app.get("id") and app["id"] != governance.get("projectId"):
            links.append((f"Act 1 · {app.get('name', app['id'])}", f"{host}/project/{app['id']}"))
    if governance.get("projectId"):
        project = f"{host}/project/{governance['projectId']}"
        links.append(("Act 4 · governed prompt", f"{project}/prompts/{urllib.parse.quote(GOVERNANCE_PROMPT, safe='')}"))
        links.append(("Act 5 · native audit log", f"{project}/settings/audit-logs"))
    for org in (state.get("organizations") or {}).values():
        if org.get("projectId"):
            links.append((f"Act 6 · {org.get('name', org['projectId'])}", f"{host}/project/{org['projectId']}"))
    return links


# --- Checks -------------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None, runner: Runner = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    ap.add_argument("--state-dir", default=str(DEMO_DIR / "private"), help="the portal's --state-dir (default: private/)")
    ap.add_argument("--org-env", help="the portal's --org-env (default: <state-dir>/org.env)")
    ap.add_argument("--portal-url", default=PORTAL_URL, help=f"where run-portal.sh listens (default: {PORTAL_URL})")
    args = ap.parse_args(argv)
    del RESULTS[:]
    state_dir = Path(args.state_dir).resolve()
    org_env = Path(args.org_env).resolve() if args.org_env else state_dir / "org.env"
    instance_env = state_dir / "instance.env"
    manifest = (DEMO_DIR / "demo.yaml").read_text()
    expected_org = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")

    print("langfuse-enterprise-operations readiness")
    print("Config")
    check(sys.version_info >= MIN_PYTHON, f"Python {sys.version.split()[0]} (README asks for {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)",
          required=False, hint="the test suite passes on 3.9, but the live workflows are only rehearsed on 3.10+")
    node = node_version(runner)
    check((major_version(node) or 0) >= MIN_NODE, f"Node {node or 'not found'} (want {MIN_NODE}+)",
          hint="the platform adapter needs Node's --env-file loader")
    has_org_env = check(org_env.is_file(), f"organization key file {org_env}; shell-exported LANGFUSE_* ignored",
                        hint="mkdir -p private && cp org.env.example private/org.env, then fill in an organization key")
    if has_org_env:
        check(private_mode_ok(org_env), "organization key file is private (mode 0600)", required=False,
              hint=f"chmod 600 {org_env}")

    print("Services")
    status, health = http_json(wf.HOST + "/api/public/health")
    version = health.get("version") if isinstance(health, dict) else None
    if check(status == 200, f"Langfuse up at {wf.HOST} ({status or health})", hint="start the local Langfuse stack"):
        check(version == SERVER_VERSION, f"Langfuse server {version} (want {SERVER_VERSION})",
              hint="Protected labels and Audit evidence refuse other versions; see the README's Known limits")
    host = urllib.parse.urlparse(args.portal_url).netloc
    status, config = http_json(args.portal_url + "/api/config", {"Host": host})
    check(status == 200 and isinstance(config, dict) and config.get("mode") == "live",
          f"portal up at {args.portal_url} ({status or config})", hint=f"start it: {RUN_CMD}")

    state: Dict[str, Any] = {}
    live_ids: set = set()
    if has_org_env:
        print("Organization key")
        status, body = cli(org_env, "organizations", "list-projects", runner=runner)
        projects = body.get("projects") if isinstance(body, dict) else None
        ok = check(status == 200 and isinstance(projects, list),
                   f"organization key lists its projects ({status}: {len(projects) if isinstance(projects, list) else wf.scrub(body)})",
                   hint="401: wrong or revoked key; 403: a project key, use an organization key")
        if ok:
            live_ids = {p.get("id") for p in projects if isinstance(p, dict)}
            status, _ = cli(org_env, "scim", "get-service-provider-config", runner=runner)
            check(status == 200, f"SCIM and the organization API answer ({status})",
                  hint="needs an Enterprise license on the instance")
            status, body = cli(org_env, "organizations", "list-memberships", runner=runner)
            members = body.get("memberships") if isinstance(body, dict) else []
            check(any(m.get("email") == PRESENTER_EMAIL for m in members or []),
                  f"presenter {PRESENTER_EMAIL} is an organization member", required=False,
                  hint="the Organizations act gives this user Admin on the new organization only when present")

        print("Portal manifest")
        state = wf.load_state(state_dir)
        for passed, what, required, hint in manifest_checks(state, live_ids):
            check(passed, what, required=required, hint=hint)
        governance = state.get("governance") or {}
        gov_app = (state.get("applications") or {}).get(governance.get("projectId") or "")
        if gov_app and gov_app.get("envFile") and governance.get("versions"):
            status, body = cli(Path(gov_app["envFile"]), "prompts", "get", GOVERNANCE_PROMPT, "--label", "production", runner=runner)
            check(status == 200 and governance_version_ok(body, governance),
                  f"{GOVERNANCE_PROMPT}: production = v{body.get('version') if isinstance(body, dict) else '?'} ({status})",
                  hint=f"run {SEED_CMD}")

    print("Instance API (Organizations act only)")
    if check(instance_env.is_file(), f"instance key file {instance_env}", required=False,
             hint="python3 scripts/prepare-instance-key.py, then ./scripts/enable-instance-api.sh; skip if you do not show Organizations"):
        status, body = instance_get(instance_env, "/api/admin/organizations", runner)
        orgs = body.get("organizations") if isinstance(body, dict) else None
        if check(status == 200 and isinstance(orgs, list), f"instance API lists organizations ({status})", required=False,
                 hint="web lacks ADMIN_API_KEY: ./scripts/enable-instance-api.sh --dry-run"):
            for org in orgs[:25]:
                if "projects" not in org and org.get("id"):  # the list may omit projects; the detail read has them
                    status, detail = instance_get(instance_env, "/api/admin/organizations/" + org["id"], runner)
                    org["projects"] = detail.get("projects", []) if status == 200 and isinstance(detail, dict) else []
            name = org_name_for(orgs, live_ids)
            check(name == expected_org, f"organization key belongs to '{name}' (want '{expected_org}')", required=False,
                  hint="fine if you run in another organization; set langfuse.project in demo.yaml to match")

    if args.links:
        print("\nLinks")
        for label, url in act_links(wf.HOST, args.portal_url, state):
            print(f"  {label:<30} {url}")

    line, code = summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
