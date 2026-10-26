#!/usr/bin/env python3
"""
One local self-hosted Langfuse for every standalone demo — find it, start it, give demos a project.

    python scripts/langfuse_local.py status                 # instances on this machine + where each demo points
    python scripts/langfuse_local.py up                     # reuse a healthy v4 instance, else start the shared one
    python scripts/langfuse_local.py provision real-estate  # org + project + key → demos/real-estate/.env.selfhosted

Finding an instance: running containers of the `langfuse/langfuse` web image, their published port,
and `/api/public/health`. Usable = healthy and v4+ (the demos read `/v2/observations` and the
experiments API, which a v3 server 404s). Preference: --url / LANGFUSE_LOCAL_URL, then the shared
`langfuse-local` stack, then the newest version. With none, `up` starts infra/langfuse-local.

Provisioning writes rows the way Langfuse stores them, through the instance's own Postgres
container: the org, the project, OWNER access for the instance's existing owners, and a project
API key whose fast hash is sha256(secret + hex(sha256(SALT))). The supported route — the Instance
Management API — is Enterprise-only and needs ADMIN_API_KEY on the server, which existing local
instances do not set. Local demo instances only: in production keys come from the UI or that API
and live in a secrets manager.

Standard library only, so it runs with any python3. See docs/LANGFUSE_DUAL_INGEST.md.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = ROOT / "infra" / "langfuse-local"
SHARED_PROJECT = "langfuse-local"
SHARED_URL = "http://localhost:3300"
MIN_MAJOR = 4


@dataclass(frozen=True)
class Demo:
    org_id: str
    org_name: str
    project_id: str
    project_name: str


# One org per demo keeps the bank and claims Enterprise stories (RBAC users, per-team projects,
# protected labels, audit logs) separate. Ids match the per-demo stacks these replace, so their
# seeders keep working against the shared instance.
DEMOS = {
    "real-estate": Demo("real-estate-demo", "Real Estate Demo", "real-estate", "real-estate"),
    "bank-assistant": Demo("northwind", "Northwind Bank", "retail-assistant", "retail-assistant"),
    "insurance-claims": Demo("northwind-life", "Northwind Life and Annuity", "claims-prod", "claims-prod"),
}


@dataclass
class Instance:
    url: str
    container: str
    compose_project: str
    version: str
    healthy: bool
    env: dict = field(repr=False)

    @property
    def version_tuple(self) -> tuple:
        try:
            return tuple(int(p) for p in self.version.split(".")[:3])
        except ValueError:
            return (0,)

    @property
    def usable(self) -> bool:
        return self.healthy and self.version_tuple[0] >= MIN_MAJOR

    @property
    def why_not(self) -> str:
        if not self.healthy:
            return "not healthy"
        if self.version_tuple[0] < MIN_MAJOR:
            return f"v{self.version_tuple[0]} server — the demos need v{MIN_MAJOR}+"
        return ""


# ── small helpers ─────────────────────────────────────────────────────────────────────────────

def run(args: list, check: bool = True, **kw) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=check, **kw).stdout.strip()


def http_json(method: str, url: str, *, auth: Optional[tuple] = None, timeout: float = 5.0):
    """(status, parsed body) — (0, {}) when unreachable."""
    req = urllib.request.Request(url, method=method)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:  # noqa: BLE001
        return 0, {}


def read_env(path: Path) -> dict:
    values: dict = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main_checkout() -> Path:
    """The main checkout (worktrees share its git dir) — where the repo-root .env lives."""
    try:
        common = run(["git", "rev-parse", "--git-common-dir"], cwd=ROOT)
        return (ROOT / common).resolve().parent
    except Exception:  # noqa: BLE001
        return ROOT


def shown(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# ── discovery ─────────────────────────────────────────────────────────────────────────────────

def _is_web_image(image: str) -> bool:
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name = name[: name.rfind(":")]
    return name.endswith("langfuse/langfuse")  # the worker image is langfuse/langfuse-worker


def discover() -> list:
    try:
        ids = run(["docker", "ps", "-q"]).split()
    except (OSError, subprocess.CalledProcessError):
        return []
    if not ids:
        return []
    found = []
    for c in json.loads(run(["docker", "inspect", *ids])):
        if not _is_web_image(c["Config"]["Image"]):
            continue
        bindings = (c["NetworkSettings"].get("Ports") or {}).get("3000/tcp") or []
        port = next((b["HostPort"] for b in bindings if b.get("HostPort")), None)
        if not port:
            continue
        url = f"http://localhost:{port}"
        status, health = http_json("GET", f"{url}/api/public/health", timeout=3)
        env = dict(e.split("=", 1) for e in c["Config"].get("Env") or [] if "=" in e)
        labels = c["Config"].get("Labels") or {}
        found.append(Instance(url, c["Name"].lstrip("/"), labels.get("com.docker.compose.project", ""),
                              health.get("version", "?"), status == 200 and health.get("status") == "OK", env))
    return found


def choose(instances: list, prefer_url: Optional[str] = None) -> Optional[Instance]:
    usable = [i for i in instances if i.usable]
    if prefer_url:
        return next((i for i in usable if i.url.rstrip("/") == prefer_url.rstrip("/")), None)
    shared = [i for i in usable if i.compose_project == SHARED_PROJECT]
    if shared:
        return shared[0]
    return max(usable, key=lambda i: i.version_tuple, default=None)


def shared_pinned_version() -> str:
    pinned = read_env(SHARED_DIR / ".env").get("LANGFUSE_VERSION")
    if pinned:
        return pinned
    compose = (SHARED_DIR / "docker-compose.yml").read_text()
    marker = "langfuse/langfuse:${LANGFUSE_VERSION:-"
    return compose.split(marker, 1)[1].split("}", 1)[0] if marker in compose else "?"


# ── the shared stack ──────────────────────────────────────────────────────────────────────────

def _shared_env() -> dict:
    """infra/langfuse-local/.env: generated once, never committed (.gitignore covers .env)."""
    path = SHARED_DIR / ".env"
    values = read_env(path)
    if not values:
        values = {
            "SALT": secrets.token_hex(16),
            "ENCRYPTION_KEY": secrets.token_hex(32),
            "NEXTAUTH_SECRET": secrets.token_hex(32),
            "LANGFUSE_INIT_USER_EMAIL": "admin@langfuse.local",
            # Sign-up rules want a special character.
            "LANGFUSE_INIT_USER_PASSWORD": f"Local-{secrets.token_hex(6)}!",
        }
        path.write_text("# Shared local Langfuse — generated by scripts/langfuse_local.py. Demo-only secrets.\n"
                        + "".join(f"{k}={v}\n" for k, v in values.items()))
        print(f"+ generated {shown(path)} (sign-in password inside)")
    return values


def start_shared(wait_s: int = 300) -> Instance:
    env = {**os.environ, **_shared_env()}
    license_key = read_env(main_checkout() / ".env").get("LANGFUSE_EE_LICENSE_KEY", "")
    env["LANGFUSE_EE_LICENSE_KEY"] = license_key
    print("✓ Enterprise license found in the repo-root .env" if license_key
          else "! no LANGFUSE_EE_LICENSE_KEY in the repo-root .env — Enterprise features stay off")
    print(f"→ starting the shared stack ({shown(SHARED_DIR)}) — the first pull takes a few minutes")
    subprocess.run(["docker", "compose", "-f", str(SHARED_DIR / "docker-compose.yml"), "up", "-d"],
                   env=env, check=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        status, health = http_json("GET", f"{SHARED_URL}/api/public/health", timeout=3)
        if status == 200:
            print(f"✓ shared Langfuse {health.get('version')} healthy at {SHARED_URL}")
            break
        time.sleep(3)
    else:
        raise SystemExit(f"shared Langfuse did not become healthy within {wait_s}s — "
                         f"docker compose -p {SHARED_PROJECT} logs langfuse-web")
    instance = choose(discover(), SHARED_URL)
    if instance is None:
        raise SystemExit(f"started, but no healthy Langfuse container answers at {SHARED_URL}")
    return instance


def ensure_instance(prefer_url: Optional[str] = None) -> Instance:
    instances = discover()
    instance = choose(instances, prefer_url)
    if instance:
        pinned = shared_pinned_version()
        if pinned != "?" and instance.version_tuple < tuple(int(p) for p in pinned.split(".")):
            print(f"  note: {instance.url} runs {instance.version}; the shared stack pins {pinned} "
                  f"(upgrade by recreating it with the newer image)")
        return instance
    if prefer_url:
        raise SystemExit(f"no healthy Langfuse v{MIN_MAJOR}+ answers at {prefer_url}")
    for i in instances:
        print(f"  skipped {i.url} ({i.container}, {i.version}): {i.why_not}")
    return start_shared()


# ── provisioning ──────────────────────────────────────────────────────────────────────────────

class Postgres:
    """psql inside the instance's own Postgres container, found from DATABASE_URL + compose labels."""

    def __init__(self, instance: Instance):
        dsn = urllib.parse.urlparse(instance.env.get("DATABASE_URL", ""))
        if not dsn.hostname:
            raise SystemExit(f"{instance.container}: no DATABASE_URL — cannot provision this instance")
        self.user, self.password = dsn.username or "postgres", dsn.password or ""
        self.db = (dsn.path or "/postgres").lstrip("/") or "postgres"
        filters = ["--filter", f"label=com.docker.compose.service={dsn.hostname}"]
        if instance.compose_project:
            filters += ["--filter", f"label=com.docker.compose.project={instance.compose_project}"]
        names = run(["docker", "ps", *filters, "--format", "{{.Names}}"]).split()
        if not names:  # not a compose sibling: DATABASE_URL names the container itself
            names = run(["docker", "ps", "--filter", f"name=^/{dsn.hostname}$", "--format", "{{.Names}}"]).split()
        if not names:
            raise SystemExit(f"{instance.container}: cannot find its Postgres container ({dsn.hostname})")
        self.container = names[0]

    def query(self, sql: str) -> str:
        return run(["docker", "exec", "-e", f"PGPASSWORD={self.password}", self.container, "psql", "-U", self.user,
                    "-d", self.db, "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", sql])


def keys_work(url: str, public_key: str, secret_key: str, project_id: str) -> bool:
    status, body = http_json("GET", f"{url}/api/public/projects", auth=(public_key, secret_key))
    return status == 200 and any(p.get("id") == project_id for p in body.get("data", []))


def provision(demo_name: str, demo_dir: Path, prefer_url: Optional[str]) -> int:
    demo = DEMOS[demo_name]
    out = demo_dir / ".env.selfhosted"
    instance = ensure_instance(prefer_url)
    print(f"✓ local Langfuse {instance.version} at {instance.url} ({instance.container})")

    current = read_env(out)
    if (current.get("LANGFUSE_BASE_URL", "").rstrip("/") == instance.url
            and keys_work(instance.url, current.get("LANGFUSE_PUBLIC_KEY", ""), current.get("LANGFUSE_SECRET_KEY", ""),
                          current.get("LANGFUSE_PROJECT_ID", ""))):
        print(f"= {shown(out)} already points at {instance.url} and its keys work")
        return 0
    if current:
        backup = out.with_name(f".env.selfhosted.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        out.rename(backup)
        print(f"  previous file kept as {backup.name} (pointed at {current.get('LANGFUSE_BASE_URL')})")

    pg = Postgres(instance)
    salt = instance.env.get("SALT")
    if not salt:
        raise SystemExit(f"{instance.container}: no SALT in its environment — cannot mint a key")

    pg.query(f"insert into organizations (id, name) values ({sql_str(demo.org_id)}, {sql_str(demo.org_name)}) "
             f"on conflict (id) do nothing")
    # Reuse a live project with the demo's name in that org, whatever its id; else create ours.
    project_id = pg.query(f"select id from projects where org_id = {sql_str(demo.org_id)} and "
                          f"name = {sql_str(demo.project_name)} and deleted_at is null limit 1")
    if not project_id:
        other_org = pg.query(f"select org_id from projects where id = {sql_str(demo.project_id)}")
        if other_org:
            raise SystemExit(f"project id {demo.project_id} already exists in org {other_org} on {instance.url}")
        pg.query(f"insert into projects (id, org_id, name) values ({sql_str(demo.project_id)}, "
                 f"{sql_str(demo.org_id)}, {sql_str(demo.project_name)})")
        project_id = demo.project_id
        print(f"+ project {demo.project_name} in org {demo.org_name}")
    # Whoever owns an org on this instance can see the demo's org too (single-user laptop instance).
    granted = pg.query(
        f"insert into organization_memberships (id, org_id, user_id, role) "
        f"select gen_random_uuid()::text, {sql_str(demo.org_id)}, owners.user_id, 'OWNER' from "
        f"(select distinct user_id from organization_memberships where role = 'OWNER') owners "
        f"where not exists (select 1 from organization_memberships m where m.org_id = {sql_str(demo.org_id)} "
        f"and m.user_id = owners.user_id) returning user_id")
    if granted:
        print(f"+ {len(granted.splitlines())} existing owner(s) added to {demo.org_name}")

    public_key = f"pk-lf-{demo_name}-{secrets.token_hex(6)}"
    secret_key = f"sk-lf-{demo_name}-{secrets.token_hex(16)}"
    fast_hash = hashlib.sha256((secret_key + hashlib.sha256(salt.encode()).hexdigest()).encode()).hexdigest()
    pg.query(f"insert into api_keys (id, note, public_key, hashed_secret_key, display_secret_key, project_id, "
             f"fast_hashed_secret_key, scope) values ({sql_str(str(uuid.uuid4()))}, "
             f"{sql_str(f'{demo_name} demo (scripts/langfuse_local.py)')}, {sql_str(public_key)}, "
             f"{sql_str(secrets.token_hex(24))}, {sql_str('sk-lf-...' + secret_key[-4:])}, {sql_str(project_id)}, "
             f"{sql_str(fast_hash)}, 'PROJECT')")
    if not keys_work(instance.url, public_key, secret_key, project_id):
        raise SystemExit(f"minted a key for {project_id} but {instance.url} rejects it")

    out.write_text(
        f"# Local self-hosted Langfuse for this demo — written by scripts/langfuse_local.py provision {demo_name}\n"
        f"# Instance: {instance.container} ({instance.version}). Re-run provision to re-target.\n"
        f"LANGFUSE_BASE_URL={instance.url}\n"
        f"LANGFUSE_PROJECT_ID={project_id}\n"
        f"LANGFUSE_PROJECT_NAME={demo.project_name}\n"
        f"LANGFUSE_PUBLIC_KEY={public_key}\n"
        f"LANGFUSE_SECRET_KEY={secret_key}\n")
    print(f"+ API key for {project_id} → {shown(out)} (verified)")
    print(f"  UI: {instance.url}/project/{project_id}")
    return 0


# ── commands ──────────────────────────────────────────────────────────────────────────────────

def cmd_status(args) -> int:
    instances = discover()
    chosen = choose(instances, args.url)
    if not instances:
        print("no Langfuse web containers running")
    for i in sorted(instances, key=lambda i: i.url):
        mark = "→" if chosen and i.container == chosen.container else ("✓" if i.usable else "✗")
        note = "" if i.usable else f"  ({i.why_not})"
        print(f"{mark} {i.url:<24} {i.version:<9} {i.container} [{i.compose_project or '-'}]{note}")
    if not chosen:
        print(f"  no usable instance — `python scripts/langfuse_local.py up` starts the shared one on {SHARED_URL}")
    print()
    for name in DEMOS:
        env = read_env(args.demos_root / name / ".env.selfhosted")
        if not env:
            print(f"  {name:<17} no .env.selfhosted (Cloud only) — provision {name} to add the local target")
            continue
        url = env.get("LANGFUSE_BASE_URL", "?")
        ok = keys_work(url, env.get("LANGFUSE_PUBLIC_KEY", ""), env.get("LANGFUSE_SECRET_KEY", ""),
                       env.get("LANGFUSE_PROJECT_ID", ""))
        print(f"  {name:<17} → {url} project {env.get('LANGFUSE_PROJECT_ID', '?')}: "
              f"{'keys OK' if ok else 'UNREACHABLE or keys rejected'}")
    return 0


def cmd_up(args) -> int:
    instance = ensure_instance(args.url)
    print(f"✓ using Langfuse {instance.version} at {instance.url} ({instance.container})")
    return 0


def cmd_provision(args) -> int:
    names = list(DEMOS) if args.demo == "all" else [args.demo]
    for name in names:
        print(f"── {name}")
        provision(name, args.demos_root / name, args.url)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("LANGFUSE_LOCAL_URL"),
                        help="use this instance (e.g. http://localhost:3200) instead of choosing one")
    parser.add_argument("--demos-root", type=Path, default=ROOT / "demos",
                        help="where the demo folders live (default: this checkout's demos/)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="instances found and where each demo points")
    sub.add_parser("up", help="reuse a healthy v4 instance, or start the shared one")
    p = sub.add_parser("provision", help="org + project + API key for a demo → .env.selfhosted")
    p.add_argument("demo", choices=[*DEMOS, "all"])
    args = parser.parse_args()
    return {"status": cmd_status, "up": cmd_up, "provision": cmd_provision}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
