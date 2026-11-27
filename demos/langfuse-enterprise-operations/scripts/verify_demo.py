#!/usr/bin/env python3
"""Readiness check for langfuse-enterprise-operations: reads the instance back, says READY or NOT READY.

    python3 scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    python3 scripts/verify_demo.py --links    # also print the link each act opens

This demo writes no traces, so there is no trace or score to count. It is ready when the
licensed local instance, the organization key, the running portal and the seeded
governance project all read back live: the project and its ownership marker, both persona
memberships, the protected-label policy and both governed prompt definitions. Cached flags
in private/state.json are never accepted on their own.

Every read goes through the credential path the portal uses: the vendored CLI wrapper
(`scripts/langfuse-connect.sh --env-file`) for public REST, and `node --env-file` with
`scripts/platform-api.mjs` for the instance API and the native policy read. Python never
reads a credential file; a small Node check confirms the required names are set. Shell
LANGFUSE_* keys are cleared first and stripped from every child process. Standard library
only, no model calls, no writes. Required checks cover the full script and the short path;
WARN lines are for the optional Acts 6 and 8 only.
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
import workflows as wf  # noqa: E402  (stdlib only; HOST, WRAPPER, the manifest loader and the prompt definitions)

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with workflows.py and platform-api.mjs.
SERVER_VERSION = "4.76.0"                    # scripts/platform-api.mjs refuses any other version
PORTAL_URL = "http://127.0.0.1:8091"         # server.py --port default
PRESENTER_EMAIL = "demo@example.com"         # provision_organization grants this user Admin when present
GOVERNANCE_PROMPT = wf.GOVERNANCE_PROMPT     # protected_labels() keeps two versions of it
PROJECT_MARKER = "langfuse-admin-portal"     # metadata.demo on every project onboard_application creates
ORG_MARKER = "langfuse-operations"           # metadata.demo on every organization provision_organization creates
MIN_PYTHON = (3, 10)
MIN_NODE = 22
SEED_CMD = "python3 scripts/seed_all.py"
RUN_CMD = "./scripts/run-portal.sh"
TIMEOUT_S = 45                               # the same per-call limit Context.call uses
KEEP = {"LANGFUSE_CLI_BIN"}                  # the wrapper's runner override is not a credential

RESULTS: List[Tuple[str, bool, str]] = []    # (PASS | FAIL | WARN, required, what)
Runner = Callable[..., Any]                  # subprocess.run, or a fake in tests
Check = Tuple[bool, str, bool, str]          # (ok, what, required, hint)

# Prints the names (never the values) that are unset or still a template placeholder.
_ENV_PROBE = ("const bad=process.argv.slice(1).filter(n=>!process.env[n]||/^replace-with-/.test(process.env[n]));"
              "process.stdout.write(JSON.stringify(bad));")


# --- Result helpers ---------------------------------------------------------------------

def check(ok: bool, what: str, *, required: bool = True, hint: str = "") -> bool:
    """Record one check and print its line. A failed optional check is a WARN, not a FAIL."""
    status = "PASS" if ok else ("FAIL" if required else "WARN")
    RESULTS.append((status, required, what))
    print(f"  {status}  {what}" + (f"  ->  {hint}" if hint and not ok else ""))
    return ok


def record(checks: List[Check]) -> bool:
    """Print a list of checks; True when none of the required ones failed."""
    return all([check(ok, what, required=req, hint=hint) or not req for ok, what, req, hint in checks])


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
    return (f"NOT READY ({detail})", 1) if failed or not rs else (f"READY ({detail})", 0)


# --- Manifest, versions and environment -------------------------------------------------

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


def clear_shell_keys(environ: Optional[Dict[str, str]] = None) -> None:
    """Drop inherited LANGFUSE_* (except the CLI runner override) before anything reads the env."""
    env = os.environ if environ is None else environ
    for k in [k for k in env if k.startswith("LANGFUSE_") and k not in KEEP]:
        del env[k]


def child_env(drop: Tuple[str, ...] = ()) -> Dict[str, str]:
    """The child's environment without LANGFUSE_* keys and hosts (and any named secrets)."""
    return {k: v for k, v in os.environ.items() if (k in KEEP or not k.startswith("LANGFUSE_")) and k not in drop}


def env_missing(path: Path, names: Tuple[str, ...], runner: Runner = subprocess.run) -> Optional[List[str]]:
    """Names unset or still a placeholder in `path`, checked by Node's loader so Python never sees a value.
    The names are removed from the child's environment first, so a shell export cannot satisfy them.
    None when the check itself could not run."""
    try:
        result = runner(["node", "--env-file=" + str(path), "-e", _ENV_PROBE, *names], capture_output=True, text=True,
                        timeout=10, env=child_env(names))
        missing = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return missing if isinstance(missing, list) else None


def env_check(path: Optional[str], names: Tuple[str, ...], what: str, runner: Runner) -> Check:
    """One required check that a credential file exists and sets every name."""
    if not path or not Path(path).is_file():
        return (False, f"{what}: file {path or 'not recorded'} missing", True, f"run {SEED_CMD}")
    missing = env_missing(Path(path), names, runner)
    if missing is None:
        return (False, f"{what}: could not load {path} with Node", True, "check Node 22+ and the file's syntax")
    return (not missing, f"{what}: {', '.join(names)} set" + (f"; missing {', '.join(missing)}" if missing else ""),
            True, f"fill in {path}")


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


def adapter(env_file: Path, request: Dict[str, Any], runner: Runner = subprocess.run) -> Tuple[Optional[int], Any]:
    """One read through scripts/platform-api.mjs, like Context.bridge. Returns (status, body)."""
    request = dict({"host": wf.HOST, "body": None}, **request)
    try:
        result = runner(["node", "--env-file=" + str(env_file), str(DEMO_DIR / "scripts" / "platform-api.mjs")],
                        input=json.dumps(request), capture_output=True, text=True, timeout=80,
                        env=child_env(("ADMIN_API_KEY", "DEMO_EMAIL", "DEMO_PASSWORD")))
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return None, f"platform adapter failed: {type(error).__name__}"
    if not isinstance(payload, dict):
        return None, "platform adapter returned no result"
    return (payload.get("status") or None), payload.get("body")


def instance_get(env_file: Path, path: str, runner: Runner = subprocess.run) -> Tuple[Optional[int], Any]:
    return adapter(env_file, {"mode": "instance", "method": "GET", "path": path}, runner)


def native_get(persona_env: Path, procedure: str, body: Dict[str, Any], runner: Runner = subprocess.run) -> Tuple[Optional[int], Any]:
    """A read-only native UI RPC (as the recorded persona); the adapter allowlists the procedure."""
    return adapter(persona_env, {"mode": "rpc", "method": "GET", "procedure": procedure, "body": body}, runner)


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


# --- Live checks (covered by tests/test_verify_demo.py against a fake CLI and adapter) ------

def marker(item: Any) -> Optional[str]:
    return ((item.get("metadata") or {}) if isinstance(item, dict) else {}).get("demo")


def governance_checks(state: Dict[str, Any], projects: Dict[str, Dict[str, Any]], org_env: Path,
                      runner: Runner = subprocess.run) -> List[Check]:
    """Every governance prerequisite for Acts 4 and 5, read back live. A missing prerequisite fails and
    the checks that depend on it are not attempted (the failure already reaches the verdict)."""
    hint = f"run {SEED_CMD}"
    governance = state.get("governance") or {}
    gov_id = governance.get("projectId")
    out: List[Check] = []
    if not gov_id:
        return [(False, "governance project recorded in private/state.json", True, hint)]
    live = projects.get(gov_id)
    out.append((live is not None and marker(live) == PROJECT_MARKER,
                f"governance project {gov_id} listed with metadata.demo = {PROJECT_MARKER}"
                + ("" if live is not None else " (not in the organization)"), True, hint))
    app = (state.get("applications") or {}).get(gov_id)
    if not app:
        return out + [(False, "governance application record in private/state.json", True, hint)]
    key = env_check(app.get("envFile"), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"), "governance project key", runner)
    out.append(key)

    personas = governance.get("personas") or {}
    users = state.get("users") or {}
    persona_ok: Dict[str, bool] = {}
    for role in ("ADMIN", "MEMBER"):
        persona = personas.get(role) or {}
        user = users.get(persona.get("email") or "") or {}
        if not (persona.get("registered") and user.get("id")):
            out.append((False, f"{role.title()} persona recorded with a SCIM user id", True, hint))
            persona_ok[role] = False
            continue
        c = env_check(persona.get("envFile"), ("DEMO_EMAIL", "DEMO_PASSWORD"), f"{role.title()} persona sign-in", runner)
        out.append(c)
        persona_ok[role] = c[0]

    status, body = cli(org_env, "projects", "list-memberships", gov_id, runner=runner)
    grants = {m.get("userId"): m.get("role") for m in (body.get("memberships") or [])} if status == 200 and isinstance(body, dict) else None
    for role in ("ADMIN", "MEMBER"):
        user_id = (users.get((personas.get(role) or {}).get("email") or "") or {}).get("id")
        actual = grants.get(user_id) if grants is not None else None
        out.append((grants is not None and actual == role,
                    f"{role.title()} persona has project role {role} ({'read failed: ' + str(status) if grants is None else actual})",
                    True, hint))

    if persona_ok.get("ADMIN"):
        status, body = native_get(Path(personas["ADMIN"]["envFile"]), "prompts.getProtectedLabels", {"projectId": gov_id}, runner)
        labels = body if isinstance(body, list) else []
        out.append((status == 200 and "production" in labels,
                    f"protected-label policy protects production ({status}: {labels if status == 200 else wf.scrub(body)})",
                    True, hint))

    if key[0]:
        env = Path(app["envFile"])
        versions = governance.get("versions") or []
        for number in (1, 2):
            if len(versions) < number:
                out.append((False, f"{GOVERNANCE_PROMPT} slot {number} recorded", True, hint))
                continue
            recorded = versions[number - 1]
            status, body = cli(env, "prompts", "get", GOVERNANCE_PROMPT, "--version", str(recorded.get("version")), runner=runner)
            ok = status == 200 and wf.prompt_matches(body, wf.governance_prompt(number)) and body.get("version") == recorded.get("version")
            out.append((ok, f"{GOVERNANCE_PROMPT} v{recorded.get('version')} matches the slot {number} definition ({status})",
                        True, hint))
        status, body = cli(env, "prompts", "get", GOVERNANCE_PROMPT, "--label", "production", runner=runner)
        recorded_versions = {v.get("version") for v in versions}
        current = body.get("version") if status == 200 and isinstance(body, dict) else None
        out.append((current in recorded_versions and current is not None,
                    f"{GOVERNANCE_PROMPT}: production = v{current} ({status})", True, hint))
    return out


def application_checks(state: Dict[str, Any], projects: Dict[str, Dict[str, Any]]) -> List[Check]:
    """Recorded applications other than governance. They are created live in Act 1, so only warn."""
    gov_id = (state.get("governance") or {}).get("projectId")
    others = sorted(pid for pid in (state.get("applications") or {}) if pid != gov_id)
    stale = [pid for pid in others if pid not in projects]
    unmarked = [pid for pid in others if pid in projects and marker(projects[pid]) != PROJECT_MARKER]
    pending = sorted(o.get("name", oid) for oid, o in (state.get("organizations") or {}).items() if o.get("resetStatus"))
    return [
        (not stale, f"recorded applications listed in the organization: {len(others) - len(stale)} of {len(others)}",
         False, "stale records: preview Reset (Act 8) in the portal"),
        (not unmarked, f"recorded applications carrying metadata.demo = {PROJECT_MARKER}: {len(others) - len(unmarked)} of {len(others)}",
         False, "Reset (Act 8) refuses unmarked projects"),
        (not pending, f"organizations with cleanup pending: {', '.join(pending) or 'none'}", False,
         "preview and retry Reset (Act 8)"),
    ]


def declared_org_checks(state: Dict[str, Any], expected: Optional[str], instance_env: Path,
                        runner: Runner = subprocess.run) -> List[Check]:
    """The manifest's declared target is the organization the Organizations workflow provisions.
    Once the manifest records it, its ownership must read back; until then Act 6 creates it."""
    recorded = [o for o in (state.get("organizations") or {}).values() if o.get("name") == expected]
    if not recorded:
        out: List[Check] = [(False, f"declared organization '{expected}' provisioned", False,
                             f"Act 6 (optional) creates it with organization name '{expected}'")]
        if instance_env.is_file():
            status, body = instance_get(instance_env, "/api/admin/organizations", runner)
            out.append((status == 200 and isinstance(body, dict) and isinstance(body.get("organizations"), list),
                        f"instance API lists organizations ({status})", False,
                        "web lacks ADMIN_API_KEY: ./scripts/enable-instance-api.sh --dry-run"))
        else:
            out.append((False, f"instance key file {instance_env}", False,
                        "python3 scripts/prepare-instance-key.py, then ./scripts/enable-instance-api.sh (Act 6 only)"))
        return out
    out = [env_check(str(instance_env), ("ADMIN_API_KEY",), "instance key for the recorded declared organization", runner)]
    if not out[0][0]:
        return out
    for org in recorded:
        status, body = instance_get(instance_env, "/api/admin/organizations/" + str(org.get("id")), runner)
        ok = (status == 200 and isinstance(body, dict) and body.get("name") == expected and marker(body) == ORG_MARKER
              and (not org.get("projectId") or org["projectId"] in {p.get("id") for p in body.get("projects") or []}))
        out.append((ok, f"declared organization '{expected}' ({org.get('id')}) owned: name, metadata.demo = {ORG_MARKER}, "
                        f"starter project ({status})", True, "preview Reset (Act 8); the portal refuses unowned organizations"))
        out.append(env_check(org.get("envFile"), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"),
                             f"'{expected}' organization key", runner))
    return out


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


def organization_projects(org_env: Path, runner: Runner) -> Tuple[Optional[Dict[str, Dict[str, Any]]], str]:
    """The org key's live project inventory by id, or None with the reason it could not be read."""
    status, body = cli(org_env, "organizations", "list-projects", runner=runner)
    projects = body.get("projects") if isinstance(body, dict) else None
    if status != 200 or not isinstance(projects, list) or any(not isinstance(p, dict) or not p.get("id") for p in projects):
        return None, f"{status}: {wf.scrub(body)}"
    return {p["id"]: p for p in projects}, f"{status}: {len(projects)} project(s)"


# --- Main -----------------------------------------------------------------------------------

def section(name: str, fn: Callable[[], Any]) -> Any:
    """Run one section; an unexpected error becomes a FAIL instead of a traceback."""
    try:
        return fn()
    except Exception as error:  # noqa: BLE001 - every failure must reach the verdict
        check(False, f"{name}: unexpected {type(error).__name__}: {wf.scrub(str(error))[:200]}")
        return None


def main(argv: Optional[List[str]] = None, runner: Runner = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    ap.add_argument("--state-dir", default=str(DEMO_DIR / "private"), help="the portal's --state-dir (default: private/)")
    ap.add_argument("--org-env", help="the portal's --org-env (default: <state-dir>/org.env)")
    ap.add_argument("--portal-url", default=PORTAL_URL, help=f"where run-portal.sh listens (default: {PORTAL_URL})")
    args = ap.parse_args(argv)
    del RESULTS[:]
    clear_shell_keys()
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
    node_ok = check((major_version(node) or 0) >= MIN_NODE, f"Node {node or 'not found'} (want {MIN_NODE}+)",
                    hint="the platform adapter and the credential check need Node's --env-file loader")
    file_ok = check(org_env.is_file(), f"organization key file {org_env}; shell-exported LANGFUSE_* cleared",
                    hint="python3 scripts/seed_all.py creates it from org.env.example; then fill it in")
    config_ok = node_ok and file_ok
    if org_env.is_file():
        check(private_mode_ok(org_env), "organization key file is private (mode 0600)", required=False, hint=f"chmod 600 {org_env}")
    if config_ok:
        config_ok = record([env_check(str(org_env), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"), "organization key", runner)])
    if not config_ok:
        print("  ....  Langfuse reads skipped until the configuration passes")
        line, code = summary()
        print(f"\n{line}")
        return code

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

    print("Organization key")
    projects, detail = organization_projects(org_env, runner)
    check(projects is not None, f"organization key lists its projects ({detail})",
          hint="401: wrong or revoked key; 403: a project key, use an organization key")
    if projects is not None:
        status, _ = cli(org_env, "scim", "get-service-provider-config", runner=runner)
        check(status == 200, f"SCIM and the organization API answer ({status})", hint="needs an Enterprise license on the instance")
        status, body = cli(org_env, "organizations", "list-memberships", runner=runner)
        members = body.get("memberships") if status == 200 and isinstance(body, dict) else None
        check(members is not None, f"organization memberships readable ({status})", hint="needs an Enterprise license")
        check(any(m.get("email") == PRESENTER_EMAIL for m in members or []),
              f"presenter {PRESENTER_EMAIL} is an organization member (Act 6)", required=False,
              hint="the Organizations act gives this user Admin on the new organization only when present")

    state: Dict[str, Any] = {}
    print("Portal manifest and governance (Acts 4 and 5)")
    loaded = section("private/state.json", lambda: wf.load_state(state_dir))
    if isinstance(loaded, dict):
        state = loaded
        if projects is not None:
            section("governance read-back", lambda: record(governance_checks(state, projects, org_env, runner)))
            section("recorded applications", lambda: record(application_checks(state, projects)))
        else:
            check(False, "governance read-back skipped: the organization inventory is unreadable")
    print(f"Declared organization '{expected_org}' (Act 6)")
    section("declared organization", lambda: record(declared_org_checks(state, expected_org, instance_env, runner)))

    if args.links:
        print("\nLinks")
        for label, url in act_links(wf.HOST, args.portal_url, state):
            print(f"  {label:<30} {url}")

    line, code = summary()
    print(f"\n{line}")
    print("Short path (Opening, Acts 1, 4, 5, Close) needs every required check; WARN lines affect only Acts 6 and 8.")
    return code


if __name__ == "__main__":
    sys.exit(main())
