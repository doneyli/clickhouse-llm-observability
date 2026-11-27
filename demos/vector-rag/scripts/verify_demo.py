#!/usr/bin/env python3
"""Readiness check for vector-rag: reads Langfuse back and says READY or NOT READY.

Run from the repo root (it reads the root stack's .env; shell-exported LANGFUSE_* are ignored):

    python demos/vector-rag/scripts/verify_demo.py            # one PASS / FAIL / WARN line per check; exit 1 when a required one fails
    python demos/vector-rag/scripts/verify_demo.py --links    # also print the link each act opens

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows. Standard library only, so it runs in any Python 3.9+ without
the demo's dependencies. No model calls; a handful of Langfuse reads, done in seconds.

Observations are read with the Observations API v2. The root stack's self-hosted server
(langfuse/langfuse:3.221.1) answers v2 with 404, so on a 404 the check falls back to the
v1 observations list, which v3 servers still serve. Scores use the Scores API v3 on both.
No legacy /traces endpoint is used.
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
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

# --- DEMO-SPECIFIC: what a ready demo looks like. Keep in sync with langfuse_config.py,
# rag_pipeline.py, scripts/seed-app-prompts.py and scripts/seed-code-evaluators.sh.
TRACE_NAME = "vector-rag"                       # langfuse_trace() in langfuse_config.py sets it on every observation
PROMPT_NAME = "vector-rag-generation"           # fetched by rag_pipeline.py, seeded by scripts/seed-app-prompts.py
EXPECTED_PROMPTS = {PROMPT_NAME: "production"}  # prompt name -> label the seeder sets
# The code evaluators setup.sh seeds score every vector-rag generation at ingest (Act 3):
# response-structure-check writes the first three, credential-leak-guard the fourth.
EXPECTED_SCORES = ["output-present", "structure-clean", "response-length", "credential-leak"]
MIN_OBSERVATIONS = 2                            # the LangChain chain, with the ChatAnthropic generation under it
LOOKBACK_DAYS = 7                               # how far back to look for a run
FRESH_HOURS = 24                                # older than this only warns: re-run the day before
V1_MAX_PAGES = 10                               # v1 fallback: generations scanned, 100 per page, newest first
# Local services: (name, url, required). The demo script runs the one-shot CLI, which needs
# no server; the long-running API on VECTOR_RAG_PORT only matters to the query-router demo.
SERVICES: List[Tuple[str, str, bool]] = [
    ("vector-rag API (query-router tier, not used by the script)",
     "http://localhost:{VECTOR_RAG_PORT}/health", False),
]
SEED_CMD = "python demos/vector-rag/scripts/seed_all.py"
PROMPT_HELPER = REPO_ROOT / "scripts" / "seed-app-prompts.py"  # the seeded definitions, compared in full
RUN_CMD = "docker compose --profile demo run --rm vector-rag python main.py"
TIMEOUT_S = 10

RESULTS: List[Tuple[str, bool, str]] = []       # (PASS | FAIL | WARN, required, what)


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


# --- Environment and manifest -----------------------------------------------------------

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
    """Read `section.key` from demo.yaml (`section=""` for a top-level key) without PyYAML.

    Handles the manifest's flat shape only: top-level scalars and one level of nesting.
    """
    current = None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        nested = line[0].isspace()
        m = re.match(r"\s*([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        if not nested:
            current = m.group(1)
            if section == "" and m.group(1) == key:
                return _unquote(m.group(2).strip()) or None
        elif current == section and m.group(1) == key:
            return _unquote(m.group(2).strip()) or None
    return None


def env_file(profile: Optional[str]) -> Path:
    """Container demos read the root stack's .env; standalone and workshop demos their own."""
    return REPO_ROOT / ".env" if profile == "container" else DEMO_DIR / ".env"


def load_env(path: Path, environ: Optional[MutableMapping[str, str]] = None) -> bool:
    """Make `path` the only source of LANGFUSE_*: drop shell exports, then apply the file.

    A shell-exported key for another project would otherwise send traces there with no
    error, while these reads look at a different project. Every key the file sets also
    overrides the shell. Shell LANGFUSE_* are dropped even when the file is missing, so a
    missing file fails the key check instead of reading another project. Returns whether
    the file was loaded.
    """
    env = os.environ if environ is None else environ
    for k in [k for k in env if k.startswith("LANGFUSE_")]:
        del env[k]
    if not path.is_file():
        return False
    env.update(parse_env(path.read_text()))
    return True


# --- Langfuse reads -----------------------------------------------------------------------

def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    """Parse an API timestamp such as 2026-11-26T10:00:00.123Z on Python 3.9 as well."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)?$", value)
    if not m:
        raise ValueError(f"unrecognised timestamp {value!r}")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    tz = m.group(3) or "Z"
    return datetime.fromisoformat(f"{m.group(1)}.{frac}{'+00:00' if tz == 'Z' else tz}")


class ApiError(Exception):
    """An HTTP error (status set) or a network failure (status None)."""

    def __init__(self, status: Optional[int], message: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


class Langfuse:
    """The few public-API calls the demo scripts need, on APIs Langfuse Cloud still serves:
    projects, prompts, Observations API v2 and Scores API v3."""

    def __init__(self, base_url: str, public_key: str, secret_key: str, timeout: float = TIMEOUT_S) -> None:
        self.base = base_url.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json",
                        "User-Agent": "demo-verify/1"}
        self.timeout = timeout

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                body: Optional[Any] = None) -> Any:
        query = {k: (str(v).lower() if isinstance(v, bool) else v)
                 for k, v in (params or {}).items() if v is not None}
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers, **({"Content-Type": "application/json"} if data else {}))
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
            raise ApiError(e.code, detail or e.reason) from None
        except (urllib.error.URLError, OSError) as e:
            raise ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            raise ApiError(None, f"non-JSON response from {path}") from None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def cursor(self, path: str, params: Dict[str, Any], cap: int = 1000, max_pages: int = 20) -> List[dict]:
        """Follow `meta.cursor` (Observations v2, Scores v3). A page may be empty while a cursor
        is still returned, so stop on a missing cursor, not on an empty page. Bounded by
        `max_pages`; a cursor the server already returned is an error, not an endless loop."""
        out: List[dict] = []
        cur = None
        seen = set()
        for _ in range(max_pages):
            d = self.get(path, **dict(params, limit=100, **({"cursor": cur} if cur else {}))) or {}
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                break
            if cur in seen:
                raise ApiError(None, f"{path} returned the same cursor twice")
            seen.add(cur)
        return out[:cap]


def act_links(base: str, state: Dict[str, Any]) -> List[Tuple[str, str]]:
    """One (label, url) per act of DEMO_SCRIPT.md, in act order."""
    p = f"{base}/project/{state.get('project_id')}"
    links = []
    trace = f"{p}/traces/{state['trace_id']}" if state.get("trace_id") else None
    if trace:
        links.append(("Act 1 · the latest run", trace))
    links.append(("Act 2 · the prompt", f"{p}/prompts/{urllib.parse.quote(PROMPT_NAME, safe='')}"))
    if trace:
        links.append(("Act 3 · its scores", trace))
    links.append(("Act 4 · the traces list", f"{p}/traces"))  # the out-of-corpus run lands here
    return links


# --- The seeded prompt definition ---------------------------------------------------------

def seeded_definitions(helper: Path = PROMPT_HELPER) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """{(name, label): {type, prompt, config}} as the root helper seeds them. Loaded after the
    .env, so the model in CONFIG comes from the same ANTHROPIC_MODEL the seed used."""
    spec = importlib.util.spec_from_file_location("seed_app_prompts", helper)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = {}
    for entry in mod.PROMPTS:
        name, text, _message, label, config = mod._unpack(entry)
        out[(name, label)] = {"type": getattr(mod, "PROMPT_TYPE", "text"), "prompt": text, "config": config or {}}
    return out


def prompt_drift(served: Dict[str, Any], want: Dict[str, Any], label: str) -> List[str]:
    """What the served version gets wrong against the seeded definition ([] = matches)."""
    out = []
    for key in ("type", "prompt", "config"):
        if (served.get(key) or ({} if key == "config" else None)) != want[key]:
            out.append("text" if key == "prompt" else key)
    if label not in (served.get("labels") or []):
        out.append(f"label {label}")
    return out


# --- Finding the latest run -------------------------------------------------------------

def trace_name_filter(since: datetime, until: datetime, *extra: dict) -> str:
    """The `filter` JSON for observations of vector-rag traces started in [since, until].

    traceName is not a query parameter: passed as one it is silently ignored, so it goes
    in the filter. Under the published Observations v2 contract a `startTime` condition in
    the filter replaces BOTH fromStartTime and toStartTime, so a filter carrying only a lower
    bound would drop the upper one; the filter therefore carries both bounds. Older servers
    let any filter replace every query-parameter filter, which the two bounds also survive.
    Callers still send fromStartTime / toStartTime for servers that ignore `filter`."""
    return json.dumps([{"type": "string", "column": "traceName", "operator": "=", "value": TRACE_NAME},
                       {"type": "datetime", "column": "startTime", "operator": ">=", "value": iso(since)},
                       {"type": "datetime", "column": "startTime", "operator": "<=", "value": iso(until)},
                       *extra])


def linked_generations(observations: List[dict], prompt_name: str = PROMPT_NAME) -> List[dict]:
    """The generations stamped with the managed prompt: what Act 1 and Act 2 point at."""
    return [o for o in observations
            if (o.get("type") or "").upper() == "GENERATION" and o.get("promptName") == prompt_name]


def newest(observations: List[dict]) -> Optional[dict]:
    """The observation with the latest startTime, whatever order the API returned."""
    dated = [o for o in observations if o.get("startTime")]
    return max(dated, key=lambda o: parse_time(o["startTime"])) if dated else None


def pages_v1(lf: "Langfuse", params: Dict[str, Any], max_pages: int = V1_MAX_PAGES) -> List[dict]:
    """Follow page / totalPages on the v1 observations list."""
    out: List[dict] = []
    for page in range(1, max_pages + 1):
        d = lf.get("/api/public/observations", **dict(params, limit=100, page=page)) or {}
        out += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            break
    return out


def latest_run(lf: "Langfuse", since: datetime, until: datetime) -> Optional[Dict[str, Any]]:
    """{api, traceId, startTime, observations} for the newest vector-rag run, or None.

    v2: the newest root observation of a `vector-rag` trace, then every observation in it.
    v1 (self-hosted v3 server): v1 has no root flag and no traceName field, so the run is
    the trace of the newest generation linked to the managed prompt. The traceName filter
    is sent too, but the promptName match is what decides, so a server that ignores the
    filter still gives the right answer.
    """
    window = {"fromStartTime": iso(since), "toStartTime": iso(until)}
    try:
        # Newest first. A page can be empty while a cursor is still returned, so follow the
        # cursor (bounded, repeated-cursor safe) until the first root turns up.
        roots = lf.cursor("/api/public/v2/observations", dict(
            window, fields="core,basic",
            filter=trace_name_filter(since, until, {"type": "boolean", "column": "isRootObservation",
                                                    "operator": "=", "value": True})), cap=1)
        if not roots:
            return None
        root = roots[0]
        obs = lf.cursor("/api/public/v2/observations", dict(
            window, traceId=root["traceId"], fields="core,basic,prompt",
            fromStartTime=iso(parse_time(root["startTime"]) - timedelta(hours=1))))
        return {"api": "v2", "traceId": root["traceId"], "startTime": root["startTime"], "observations": obs}
    except ApiError as e:
        if e.status != 404:
            raise
    gens = pages_v1(lf, dict(window, type="GENERATION", filter=trace_name_filter(
        since, until, {"type": "string", "column": "type", "operator": "=", "value": "GENERATION"})))
    gen = newest(linked_generations(gens))
    if gen is None:
        return None
    obs = pages_v1(lf, {"traceId": gen["traceId"]})
    return {"api": "v1", "traceId": gen["traceId"], "startTime": gen["startTime"], "observations": obs}


# --- Checks -------------------------------------------------------------------------------

def service_checks() -> None:
    if not SERVICES:
        return
    print("Services")
    port = os.environ.get("VECTOR_RAG_PORT") or "8003"
    for name, template, required in SERVICES:
        url = template.format(VECTOR_RAG_PORT=port)
        hint = "only needed for the query-router demo: docker compose --profile demo up -d vector-rag"
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                check(r.status < 500, f"{name} up at {url} ({r.status})", required=required)
        except urllib.error.HTTPError as e:
            check(e.code < 500, f"{name} up at {url} ({e.code})", required=required)
        except (urllib.error.URLError, OSError) as e:
            check(False, f"{name} not reachable at {url}: {type(e).__name__}", required=required, hint=hint)


def langfuse_checks(lf: Langfuse, expected_project: Optional[str]) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    print("Langfuse")
    try:
        projects = (lf.get("/api/public/projects") or {}).get("data") or []
    except ApiError as e:
        check(False, f"keys authenticate against {lf.base}: {e}",
              hint="401: wrong keys or wrong LANGFUSE_BASE_URL region; network error: is Langfuse up?")
        return state
    proj = projects[0] if projects else {}
    state["project_id"] = proj.get("id")
    if not check(bool(expected_project) and proj.get("name") == expected_project,
                 f"keys map to project '{proj.get('name')}' (want '{expected_project}')",
                 hint="use this project's keys in .env, or fix langfuse.project in demo.yaml"):
        return state

    try:
        print("Seeded artifacts")
        try:
            wanted = seeded_definitions()
        except Exception as e:  # a missing or broken helper fails the prompt checks, not the run
            check(False, f"read the seeded definitions from {PROMPT_HELPER}: {type(e).__name__}: {e}")
            wanted = {}
        for name, label in EXPECTED_PROMPTS.items():
            try:
                p = lf.get(f"/api/public/v2/prompts/{urllib.parse.quote(name, safe='')}", label=label) or {}
            except ApiError as e:
                check(False, f"prompt {name} with label {label}: {e}", hint=f"run {SEED_CMD}")
                continue
            want = wanted.get((name, label))
            if not check(want is not None, f"{PROMPT_HELPER.name} defines {name} [{label}]"):
                continue
            diff = prompt_drift(p, want, label)
            check(not diff, f"prompt {name}: {label} = v{p.get('version')}"
                  + (f", differs from the seeded definition: {', '.join(diff)}" if diff else ", matches the seeded definition"),
                  hint=f"an Act 2 edit or an old seed: run {SEED_CMD}")

        print("Latest run")
        now = datetime.now(timezone.utc)
        run = latest_run(lf, now - timedelta(days=LOOKBACK_DAYS), now + timedelta(minutes=5))
        if not check(run is not None, f"a '{TRACE_NAME}' run in the last {LOOKBACK_DAYS} days",
                     hint=f"seed the prompt, then {RUN_CMD}"):
            return state
        state["trace_id"] = trace_id = run["traceId"]
        obs = run["observations"]
        check(len(obs) >= MIN_OBSERVATIONS,
              f"latest run has {len(obs)} observation(s) (want {MIN_OBSERVATIONS}+; read with observations {run['api']})",
              hint="observations may still be landing: wait 30 s and re-run")
        linked = linked_generations(obs)
        check(bool(linked), f"latest run's generation is linked to {PROMPT_NAME}"
              + (f" v{linked[0].get('promptVersion')}" if linked else ""),
              hint=f"the run used the inline fallback: seed the prompt, then {RUN_CMD}")
        names = sorted({s.get("name") for s in lf.cursor("/api/public/v3/scores", {"traceId": trace_id})})
        missing = [n for n in EXPECTED_SCORES if n not in names]
        check(not missing, f"latest run's scores: {', '.join(names) or 'none'}"
              + (f"; missing {', '.join(missing)}" if missing else ""),
              hint="code evaluators score shortly after the trace lands: wait and re-run; "
                   "none at all: ./scripts/seed-code-evaluators.sh")
        age_h = (now - parse_time(run["startTime"])).total_seconds() / 3600
        check(age_h <= FRESH_HOURS, f"latest run is {age_h:.1f} h old (fresh under {FRESH_HOURS} h)",
              required=False, hint=f"run {RUN_CMD} the day before")
    except ApiError as e:
        check(False, f"Langfuse read failed: {e}")
    return state


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    args = ap.parse_args(argv)
    del RESULTS[:]
    try:
        run_checks(args)
    except Exception as e:  # anything unexpected still reaches the verdict, as a failure
        check(False, f"verify_demo crashed: {type(e).__name__}: {e}")
    line, code = summary()
    print(f"\n{line}")
    return code


def run_checks(args: argparse.Namespace) -> None:
    manifest_path = DEMO_DIR / "demo.yaml"
    manifest = manifest_path.read_text() if manifest_path.is_file() else ""
    profile = manifest_value(manifest, "", "profile")
    print(f"{TRACE_NAME} readiness")
    print("Config")
    if not check(profile == "container", f"demo.yaml declares profile container (found {profile!r})",
                 hint="this check reads the root stack's .env, which only a container demo uses"):
        load_env(env_file(profile))  # still drop shell LANGFUSE_*
        return
    path = env_file(profile)
    shown = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
    check(load_env(path), f"env loaded from {shown}; shell-exported LANGFUSE_* ignored",
          hint="cp .env.example .env and fill it in" if path.parent == DEMO_DIR else "run ./setup.sh at the repo root")
    expected = os.environ.get("DEMO_LANGFUSE_PROJECT") or manifest_value(manifest, "langfuse", "project")
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or ""
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if check(bool(base and pk and sk), "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set"):
        service_checks()
        lf = Langfuse(base, pk, sk)
        state = langfuse_checks(lf, expected)
        if args.links and state.get("project_id"):
            print("\nLinks")
            for label, url in act_links(lf.base, state):
                print(f"  {label:<28} {url}")


if __name__ == "__main__":
    sys.exit(main())
