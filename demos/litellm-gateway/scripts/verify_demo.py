#!/usr/bin/env python3
"""Readiness check for litellm-gateway: reads Langfuse back and says READY or NOT READY.

    python3 demos/litellm-gateway/scripts/verify_demo.py           # from the repo root
    python3 demos/litellm-gateway/scripts/verify_demo.py --links   # also print each act's link

Read-only. It starts no service and makes no model call: `./demos/litellm-gateway/run_demo.sh`
is the run path (it starts `litellm-proxy` and makes one model call). This check confirms that
a run already landed, that the gateway is up for the live act, and that the keys belong to the
expected project. One PASS / FAIL / WARN line per check, then READY or NOT READY; exit 0 only
when every required check passed. Standard library only.

Langfuse reads: projects, and Observations API v2. The repo's self-hosted server is pinned to
v3.221.1, where Observations v2 answers 404; only on that 404 does the read fall back to the v1
observations list, which that server still serves. No legacy trace endpoint is used.
`client.py` reuses these reads for its post-run check.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

DEMO_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = DEMO_DIR.parent.parent

# --- What a ready demo looks like. Keep in sync with client.py's build_payload().
TRACE_NAME = "litellm-gateway-demo"      # metadata.trace_name on every proxy-path request
DEMO_USER = "gateway-demo-user"          # metadata.trace_user_id: scopes the read to this demo
MIN_OBSERVATIONS = 1                     # the gateway exports one generation per call
LOOKBACK_DAYS = 7                        # how far back to look for a run
FRESH_HOURS = 24                         # older than this only warns: re-run the day before
RUN_CMD = "./demos/litellm-gateway/run_demo.sh"
TIMEOUT_S = 10
PAGE_LIMIT = 100
MAX_PAGES = 20
# Prefixes cleared from the inherited environment before the root .env is applied.
KEY_PREFIXES = ("LANGFUSE_", "LITELLM_LANGFUSE_")

V2_OBSERVATIONS = "/api/public/v2/observations"
V1_OBSERVATIONS = "/api/public/observations"   # pinned-v3 fallback only, on a v2 404


# --- Verdict ---------------------------------------------------------------------------

class Verdict:
    """PASS / FAIL / WARN lines and the closing READY or NOT READY line."""

    def __init__(self) -> None:
        self.results: List[Tuple[str, bool, str]] = []

    def check(self, ok: bool, what: str, *, required: bool = True, hint: str = "") -> bool:
        status = "PASS" if ok else ("FAIL" if required else "WARN")
        self.results.append((status, required, what))
        print(f"  {status}  {what}" + (f"  ->  {hint}" if hint and not ok else ""))
        return ok

    def summary(self) -> Tuple[str, int]:
        passed = sum(1 for s, _, _ in self.results if s == "PASS")
        failed = sum(1 for s, _, _ in self.results if s == "FAIL")
        warned = sum(1 for s, _, _ in self.results if s == "WARN")
        detail = f"{passed}/{len(self.results)} checks passed"
        if failed:
            detail += f", {failed} required failed"
        if warned:
            detail += f", {warned} warning{'s' if warned != 1 else ''}"
        if failed or not self.results:
            return f"NOT READY ({detail})", 1
        return f"READY ({detail})", 0


# --- Environment and manifest ----------------------------------------------------------

def _strip_comment(line: str) -> str:
    """Drop a trailing ` # comment` that is not inside quotes."""
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
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\") if value[0] == '"' else inner
    return value


def parse_env(text: str) -> Dict[str, str]:
    """Parse KEY=value lines: blank lines, comments, `export`, quotes and inline comments."""
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        value = value.strip()
        out[key.strip()] = _unquote(value) if value[:1] in "\"'" else _strip_comment(value)
    return out


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


def env_path_for(profile: Optional[str]) -> Optional[Path]:
    """Only a manifest that declares `profile: container` reads the root stack's .env."""
    return REPO_ROOT / ".env" if profile == "container" else None


def load_env(path: Path, environ: MutableMapping[str, str]) -> bool:
    """Make `path` the only source of Langfuse keys: drop inherited ones, then apply the file.

    Inherited LANGFUSE_* and LITELLM_LANGFUSE_* are dropped even when the file is missing, so a
    missing file fails the config check instead of reading another project. Every key the file
    sets overrides the shell. Returns whether the file was loaded.
    """
    for k in [k for k in environ if k.startswith(KEY_PREFIXES)]:
        del environ[k]
    if not path.is_file():
        return False
    environ.update(parse_env(path.read_text()))
    return True


def resolve_settings(env: MutableMapping[str, str]) -> Tuple[Optional[Dict[str, str]], str]:
    """The keys and URL the read-back uses, as client.py and the proxy pick them.

    Dedicated `LITELLM_LANGFUSE_*` keys win over the stack-wide ones, as in the compose
    service. A half-set pair is an error, not a silent fall back to the other project.
    Returns (settings, "") or (None, reason).
    """
    dpk, dsk = env.get("LITELLM_LANGFUSE_PUBLIC_KEY", ""), env.get("LITELLM_LANGFUSE_SECRET_KEY", "")
    if bool(dpk) != bool(dsk):
        return None, "only one of LITELLM_LANGFUSE_PUBLIC_KEY / LITELLM_LANGFUSE_SECRET_KEY is set"
    if dpk:
        pk, sk, source = dpk, dsk, "LITELLM_LANGFUSE_*"
    else:
        pk, sk, source = env.get("LANGFUSE_PUBLIC_KEY", ""), env.get("LANGFUSE_SECRET_KEY", ""), "LANGFUSE_*"
    base = (env.get("LITELLM_LANGFUSE_BASE_URL") or env.get("LANGFUSE_BASE_URL")
            or env.get("LANGFUSE_HOST") or "")
    missing = [n for n, v in (("public key", pk), ("secret key", sk), ("base URL", base)) if not v]
    if missing:
        return None, f"missing {', '.join(missing)} ({source})"
    gateway = env.get("LITELLM_BASE_URL") or f"http://localhost:{env.get('LITELLM_PORT') or '4000'}"
    return {"base": base.rstrip("/"), "pk": pk, "sk": sk, "source": source,
            "gateway": gateway.rstrip("/")}, ""


# --- Langfuse reads ----------------------------------------------------------------------

def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    """Parse an API timestamp such as 2026-11-26T10:00:00.123Z on Python 3.9 as well."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", value or "")
    if not m:
        raise ValueError(f"unrecognised timestamp {value!r}")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    tz = m.group(3) or "Z"
    return datetime.fromisoformat(f"{m.group(1)}.{frac}{'+00:00' if tz == 'Z' else tz}")


class ApiError(Exception):
    """An HTTP error (status set) or a network or parse failure (status None)."""

    def __init__(self, status: Optional[int], message: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


class Langfuse:
    """The public-API reads this demo needs: projects, Observations v2, and the pinned-v3
    v1 observations fallback."""

    def __init__(self, base_url: str, public_key: str, secret_key: str, timeout: float = TIMEOUT_S) -> None:
        self.base = base_url.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                        "User-Agent": "demo-verify/1"}
        self.timeout = timeout

    def get(self, path: str, **params: Any) -> Any:
        query = {k: (str(v).lower() if isinstance(v, bool) else v)
                 for k, v in params.items() if v is not None}
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        req = urllib.request.Request(url, headers=self.headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise ApiError(e.code, detail or str(e.reason)) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise ApiError(None, f"non-JSON response from {path}") from None

    def cursor(self, path: str, params: Dict[str, Any]) -> List[dict]:
        """Follow `meta.cursor` (Observations v2). A page may be empty while a cursor is still
        returned, so stop on a missing cursor, not on an empty page. Bounded, and a cursor seen
        twice is an error rather than a loop."""
        out: List[dict] = []
        cur: Optional[str] = None
        seen = set()
        for _ in range(MAX_PAGES):
            d = self.get(path, **dict(params, limit=PAGE_LIMIT, cursor=cur)) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur:
                return out
            if cur in seen:
                raise ApiError(None, f"{path} returned the same cursor twice")
            seen.add(cur)
        raise ApiError(None, f"{path} still had pages after {MAX_PAGES}; narrow the window")

    def pages(self, path: str, params: Dict[str, Any]) -> List[dict]:
        """Page-number traversal for the v1 observations list, bounded by `meta.totalPages`."""
        out: List[dict] = []
        for page in range(1, MAX_PAGES + 1):
            d = self.get(path, **dict(params, page=page, limit=PAGE_LIMIT)) or {}
            out += d.get("data") or []
            total = (d.get("meta") or {}).get("totalPages") or 0
            if page >= total:
                return out
        raise ApiError(None, f"{path} still had pages after {MAX_PAGES}; narrow the window")


def observation_rows(lf: Langfuse, since: datetime, until: datetime,
                     v2_params: Dict[str, Any], v1_params: Dict[str, Any]) -> Tuple[str, List[dict]]:
    """Observation rows in a time window, as ("v2" | "v1", rows).

    Observations v2 first, with `basic` (name, userId, sessionId, isRootObservation) and
    `trace_context` (traceName, tags). Only an HTTP 404 from v2, which the pinned v3 server
    returns, falls back to the v1 list; any other error propagates.
    """
    window = {"fromStartTime": iso(since), "toStartTime": iso(until)}
    try:
        return "v2", lf.cursor(V2_OBSERVATIONS, dict(window, fields="core,basic,trace_context", **v2_params))
    except ApiError as e:
        if e.status != 404:
            raise
    return "v1", lf.pages(V1_OBSERVATIONS, dict(window, **v1_params))


def trace_observations(lf: Langfuse, api: str, trace_id: str, since: datetime, until: datetime) -> List[dict]:
    """Every observation of one trace, counted as rows, on the API the window read used."""
    window = {"fromStartTime": iso(since), "toStartTime": iso(until)}
    if api == "v2":
        return lf.cursor(V2_OBSERVATIONS, dict(window, traceId=trace_id, fields="core"))
    return lf.pages(V1_OBSERVATIONS, dict(window, traceId=trace_id))


def latest_trace(rows: List[dict]) -> Optional[Dict[str, Any]]:
    """Group rows by trace and return the newest trace's id, start, name and session."""
    by_trace: Dict[str, List[dict]] = {}
    for r in rows:
        if r.get("traceId") and r.get("startTime"):
            by_trace.setdefault(r["traceId"], []).append(r)
    if not by_trace:
        return None
    tid, group = max(by_trace.items(), key=lambda kv: max(parse_time(r["startTime"]) for r in kv[1]))
    root = next((r for r in group if r.get("isRootObservation")), group[0])
    return {
        "id": tid,
        "start": min(parse_time(r["startTime"]) for r in group),
        "name": root.get("traceName") or root.get("name"),
        "session": next((r.get("sessionId") for r in group if r.get("sessionId")), None),
        "rows": len(group),
    }


def act_links(base: str, project_id: str, trace: Optional[Dict[str, Any]], gateway: str) -> List[Tuple[str, str]]:
    """One (label, url) per act of DEMO_SCRIPT.md, in act order."""
    p = f"{base.rstrip('/')}/project/{project_id}"
    links = []
    if trace:
        links.append(("Act 1 · latest gateway trace", f"{p}/traces/{trace['id']}"))
        if trace.get("session"):
            links.append(("Act 1 · its session", f"{p}/sessions/{urllib.parse.quote(trace['session'], safe='')}"))
    links.append(("Act 2 · LiteLLM admin UI", f"{gateway.rstrip('/')}/ui/"))
    return links


# --- Checks ------------------------------------------------------------------------------

def gateway_up(url: str) -> Tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{url}/health/liveliness", timeout=4) as r:
            return r.status < 400, str(r.status)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return False, type(e).__name__


def langfuse_checks(v: Verdict, lf: Langfuse, expected_project: str, now: datetime) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        v.check(False, f"keys authenticate against {lf.base}: {e}",
                hint="401: wrong key pair, or the base URL points at another host or region")
        return state
    if not v.check(bool(projects), f"keys resolve to a project at {lf.base}",
                   hint="the API returned no project for these keys"):
        return state
    proj = projects[0]
    state["project_id"] = proj.get("id")
    if not v.check(proj.get("name") == expected_project,
                   f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                   hint="use that project's keys in the root .env, or set DEMO_LANGFUSE_PROJECT "
                        "to the project the gateway exports to"):
        return state

    print("Latest run")
    since, until = now - timedelta(days=LOOKBACK_DAYS), now + timedelta(minutes=5)
    try:
        api, rows = observation_rows(lf, since, until, {"userId": DEMO_USER}, {"userId": DEMO_USER})
    except ApiError as e:
        v.check(False, f"observation read failed: {e}")
        return state
    label = V2_OBSERVATIONS if api == "v2" else f"{V1_OBSERVATIONS} (pinned v3 server)"
    try:
        trace = latest_trace([r for r in rows if r.get("userId", DEMO_USER) == DEMO_USER])
    except ValueError as e:
        v.check(False, f"unreadable observation timestamp: {e}")
        return state
    if not v.check(trace is not None,
                   f"a gateway run (user {DEMO_USER}) in the last {LOOKBACK_DAYS} days, via {label}",
                   hint=f"run {RUN_CMD} (starts litellm-proxy, one model call)"):
        return state
    state["trace"] = trace
    try:
        obs = trace_observations(lf, api, trace["id"], trace["start"] - timedelta(hours=1), until)
    except ApiError as e:
        v.check(False, f"observation count for trace {trace['id']}: {e}")
        return state
    count = len({o.get("id") for o in obs if o.get("id")})
    v.check(count >= MIN_OBSERVATIONS,
            f"latest run {trace['id']} ('{trace['name'] or 'unnamed'}') has {count} observation(s) "
            f"(want {MIN_OBSERVATIONS}+)",
            hint="observations may still be landing: wait 30 s and re-run")
    age_h = (now - trace["start"]).total_seconds() / 3600
    v.check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
            required=False, hint=f"run {RUN_CMD} the day before")
    return state


def run(argv: Optional[List[str]] = None, environ: Optional[MutableMapping[str, str]] = None,
        now: Optional[datetime] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    args = ap.parse_args(argv)
    env = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    v = Verdict()

    print("litellm-gateway readiness")
    print("Config")
    manifest_path = DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    path = env_path_for(manifest_value(manifest, "", "profile"))
    settings: Optional[Dict[str, str]] = None
    expected: Optional[str] = None
    if v.check(path is not None, "demo.yaml declares profile: container (reads the root .env)",
               hint="restore demos/litellm-gateway/demo.yaml"):
        if v.check(load_env(path, env), f"env loaded from {path.relative_to(REPO_ROOT)}; inherited "
                   "LANGFUSE_* and LITELLM_LANGFUSE_* ignored", hint="run ./setup.sh at the repo root"):
            settings, reason = resolve_settings(env)
            v.check(settings is not None, "Langfuse keys and base URL set" + (f": {reason}" if reason else ""),
                    hint="run ./setup.sh, or set both LITELLM_LANGFUSE_* keys")
            expected = env.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
            v.check(bool(expected), f"expected project: '{expected or ''}'",
                    hint="set langfuse.project in demo.yaml or DEMO_LANGFUSE_PROJECT in the root .env")

    if settings and expected:
        print("Services")
        up, detail = gateway_up(settings["gateway"])
        v.check(up, f"litellm-proxy live at {settings['gateway']} ({detail})", hint=f"run {RUN_CMD} to start it")
        lf = Langfuse(settings["base"], settings["pk"], settings["sk"])
        state = langfuse_checks(v, lf, expected, now)
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state["project_id"], state.get("trace"), settings["gateway"]):
                print(f"  {label:<30} {url}")

    line, code = v.summary()
    print(f"\n{line}")
    return code


def main() -> int:
    try:
        return run()
    except Exception as e:  # anything unforeseen still ends in a verdict
        print(f"  FAIL  unexpected error: {type(e).__name__}: {e}")
        print("\nNOT READY (verification crashed)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
