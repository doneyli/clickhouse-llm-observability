"""Provisioning fills blank template keys without losing local configuration."""

import importlib.util
from pathlib import Path

from dotenv import dotenv_values


spec = importlib.util.spec_from_file_location("provision_cloud", Path(__file__).resolve().parents[1]
                                           / "scripts" / "provision_cloud.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_blank_template_keys_are_replaced_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "DEMO", tmp_path)
    (tmp_path / ".env").write_text("# Keep the model override\nLANGFUSE_PUBLIC_KEY=\nLANGFUSE_SECRET_KEY=\n"
                                  "LANGFUSE_BASE_URL=https://old.example\nLANGFUSE_PROJECT_ID=old\n"
                                  "AGENT_MODEL=custom-model\nANTHROPIC_API_KEY=local-model-key\n")
    (tmp_path / ".env.cloud-org").write_text("LANGFUSE_BASE_URL=https://cloud.example\n"
                                            "LANGFUSE_ORG_PUBLIC_KEY=org-public\nLANGFUSE_ORG_SECRET_KEY=org-secret\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shell-model-key")
    monkeypatch.setattr(provision.httpx, "get", lambda *a, **kw: Response({
        "projects": [{"id": "project-id", "name": "marketing-agent-debugging"}]}))
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return Response({"publicKey": "new-public", "secretKey": "new-secret"})

    monkeypatch.setattr(provision.httpx, "post", post)
    assert provision.main([]) == 0
    assert len(calls) == 1 and calls[0].endswith("/project-id/apiKeys")
    values = dotenv_values(tmp_path / ".env")
    assert values["LANGFUSE_PUBLIC_KEY"] == "new-public"
    assert values["LANGFUSE_SECRET_KEY"] == "new-secret"
    assert values["LANGFUSE_BASE_URL"] == "https://cloud.example"
    assert values["LANGFUSE_PROJECT_ID"] == "project-id"
    assert values["AGENT_MODEL"] == "custom-model"
    assert values["ANTHROPIC_API_KEY"] == "local-model-key"
    assert "# Keep the model override" in (tmp_path / ".env").read_text()
    # Rerunning an initialized demo must not mint a second key.
    assert provision.main([]) == 0
    assert len(calls) == 1


def test_complete_key_pair_skips_org_and_project_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "DEMO", tmp_path)
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=existing-public\nLANGFUSE_SECRET_KEY=existing-secret\n")
    # No .env.cloud-org exists, so any attempt to provision would fail.
    assert provision.main([]) == 0
