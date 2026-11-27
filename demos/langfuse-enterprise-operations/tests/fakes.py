"""Offline stand-ins for the Langfuse CLI wrapper, the Node adapter and the Node env probe.

The fake parses the exact argv verify_demo.py and seed_all.py build, rejects any query flag the
operation does not declare in api-operations.json, and answers from in-memory data. No network,
no Node, no Langfuse.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import workflows as wf  # noqa: E402


class Completed:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def prompt(version, number):
    """A live prompt version as the v2 prompts API returns it."""
    desired = wf.governance_prompt(number)
    return {"id": "p" + str(version), "name": desired["name"], "type": desired["type"], "prompt": desired["prompt"],
            "config": {}, "version": version, "labels": []}


class FakeLangfuse:
    ADAPTER_SECRETS = ("ADMIN_API_KEY", "DEMO_EMAIL", "DEMO_PASSWORD")

    def __init__(self, private):
        private = Path(private)
        self.private = private
        self.projects = [{"id": "gov", "name": "Governance Demo", "metadata": {"demo": "langfuse-admin-portal"}},
                         {"id": "app1", "name": "Support assistant", "metadata": {"demo": "langfuse-admin-portal"}}]
        self.project_members = {"gov": [{"userId": "u-admin", "role": "ADMIN"}, {"userId": "u-member", "role": "MEMBER"}]}
        self.org_members = [{"userId": "presenter", "email": "demo@example.com", "role": "OWNER"}]
        self.prompts = {1: prompt(1, 1), 2: prompt(2, 2)}
        self.production = 2
        self.policy = ["production"]
        self.instance_orgs = {"org9": {"id": "org9", "name": "Operations Demo Organization",
                                       "metadata": {"demo": "langfuse-operations"}, "projects": [{"id": "starter"}]}}
        self.missing_names = {}       # env file path -> names the probe reports missing
        self.unloadable = set()       # env file paths Node cannot load
        self.status = {}              # (resource, action) or ("adapter", mode) -> forced HTTP status, or "timeout"
        self.node = "v24.13.0"
        self.calls = []

    # --- dispatch ---------------------------------------------------------------------------
    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["node", "--version"]:
            return Completed(self.node + "\n")
        if argv[0] == "node" and "-e" in argv:
            return self.env_probe(argv, kwargs)
        if argv[0] == "node":
            return self.adapter(argv, kwargs)
        return self.cli(argv, kwargs)

    def api_calls(self):
        return [c for c in self.calls if c[:2] != ["node", "--version"] and "-e" not in c]

    def env_probe(self, argv, kwargs):
        path = argv[1].split("=", 1)[1]
        names = argv[argv.index("-e") + 2:]
        leaked = [n for n in names if n in kwargs["env"]]
        assert not leaked, "a shell export could satisfy " + ", ".join(leaked)
        if path in self.unloadable or not Path(path).is_file():
            return Completed("", "node: not found", 9)
        return Completed(json.dumps(self.missing_names.get(path, [])))

    def adapter(self, argv, kwargs):
        assert argv[2].endswith("scripts/platform-api.mjs")
        leaked = [k for k in self.ADAPTER_SECRETS + ("LANGFUSE_PUBLIC_KEY",) if k in kwargs["env"]]
        assert not leaked, "adapter child inherits " + ", ".join(leaked)
        request = json.loads(kwargs["input"])
        assert request["host"] == wf.HOST and request["method"] == "GET", request
        forced = self.status.get(("adapter", request["mode"]))
        if forced == "timeout":
            raise subprocess.TimeoutExpired("node", 80)
        if request["mode"] == "rpc":
            assert request["procedure"] in {"prompts.getProtectedLabels", "auditLogs.all"}, request["procedure"]
            body = copy.deepcopy(self.policy)
        else:
            path = request["path"]
            if path == "/api/admin/organizations":
                body = {"organizations": [{k: o[k] for k in ("id", "name", "metadata")} for o in self.instance_orgs.values()]}
            else:
                org = self.instance_orgs.get(path.rsplit("/", 1)[-1])
                if org is None:
                    return Completed(json.dumps({"status": 404, "body": {"error": "not found"}}))
                body = copy.deepcopy(org)
        return Completed(json.dumps({"status": forced or 200, "body": body}))

    def cli(self, argv, kwargs):
        assert argv[0] == str(wf.WRAPPER) and argv[1] == "--env-file" and argv[3:5] == ["--host", wf.HOST], argv
        assert argv[5] == "api" and argv[-1] == "--json", argv
        assert not [k for k in kwargs["env"] if k.startswith("LANGFUSE_") and k != "LANGFUSE_CLI_BIN"]
        resource, action, rest = argv[6], argv[7], argv[8:-1]
        operation = wf.OPERATIONS[resource + "/" + action]
        ids = []
        while rest and not rest[0].startswith("--"):
            ids.append(rest.pop(0))
        assert len(ids) == len(operation.get("pathParameterOrder", [])), (resource, action, ids)
        flags = {}
        while rest:
            flag = rest.pop(0)
            name = flag[2:]
            if name not in operation.get("queryParameters", {}):
                raise AssertionError("invalid query parameter --" + name + " for " + resource + "/" + action)
            flags[name] = rest.pop(0)
        forced = self.status.get((resource, action))
        if forced == "timeout":
            raise subprocess.TimeoutExpired("cli", 45)
        if forced == "empty":
            return Completed("", "exit 4: network")
        status, body = self.route(resource, action, ids, flags, Path(argv[2]))
        return Completed(json.dumps({"status": forced or status, "body": body}))

    def route(self, resource, action, ids, flags, env):
        if (resource, action) == ("organizations", "list-projects"):
            return 200, {"projects": copy.deepcopy(self.projects)}
        if (resource, action) == ("scim", "get-service-provider-config"):
            return 200, {"schemas": []}
        if (resource, action) == ("organizations", "list-memberships"):
            return 200, {"memberships": copy.deepcopy(self.org_members)}
        if (resource, action) == ("projects", "list-memberships"):
            return 200, {"memberships": copy.deepcopy(self.project_members.get(ids[0], []))}
        if (resource, action) == ("prompts", "get"):
            assert ids == [wf.GOVERNANCE_PROMPT] and env.name == "gov-key.env", (ids, env)
            version = int(flags["version"]) if "version" in flags else (self.production if flags.get("label") == "production" else None)
            found = self.prompts.get(version)
            return (200, copy.deepcopy(found)) if found else (404, {"message": "Prompt not found"})
        raise AssertionError("unexpected call " + resource + "/" + action)


def ready_state(private):
    """private/state.json as a completed governance run (and one Act 1 application) leaves it."""
    private = Path(private)
    for rel in ("org.env", "instance.env", "keys/gov-key.env", "keys/app1-key.env", "personas/admin.env",
                "personas/member.env", "keys/org9-key.env"):
        path = private / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder, never read by Python\n")
        path.chmod(0o600)
    return {
        "applications": {
            "gov": {"id": "gov", "name": "Governance Demo", "envFile": str(private / "keys/gov-key.env"), "keyRole": "ADMIN", "members": []},
            "app1": {"id": "app1", "name": "Support assistant", "envFile": str(private / "keys/app1-key.env"), "members": []},
        },
        "users": {"a@example.invalid": {"id": "u-admin", "email": "a@example.invalid"},
                  "m@example.invalid": {"id": "u-member", "email": "m@example.invalid"}},
        "organizationId": None,
        "governance": {"projectId": "gov", "versions": [{"id": "p1", "version": 1}, {"id": "p2", "version": 2}],
                       "personas": {"ADMIN": {"email": "a@example.invalid", "registered": True, "envFile": str(private / "personas/admin.env")},
                                    "MEMBER": {"email": "m@example.invalid", "registered": True, "envFile": str(private / "personas/member.env")}}},
        "organizations": {"org9": {"id": "org9", "name": "Operations Demo Organization", "projectId": "starter",
                                   "envFile": str(private / "keys/org9-key.env")}},
    }
