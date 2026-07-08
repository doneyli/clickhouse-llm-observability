"""Runtime configuration loader for the brand promo demo."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator, model_validator


class CustomerConfig(BaseModel):
    display_name: str
    industry: str
    geography: list[str]


class BrandFamily(BaseModel):
    name: str
    category: str
    hero_skus: list[str]


class CatalogConfig(BaseModel):
    brand_families: list[BrandFamily]


class ComplianceConfig(BaseModel):
    regulatory_bodies: list[str]
    brand_guideline_doc: str
    regulatory_rules_doc: str

    @model_validator(mode="after")
    def docs_exist(self) -> ComplianceConfig:
        for doc_path in (self.brand_guideline_doc, self.regulatory_rules_doc):
            p = Path(doc_path)
            if not p.is_absolute():
                # Paths are relative to the project root (demo dir)
                # We check at runtime; if missing, warn but don't fail scaffold
                pass
        return self


class HeroAgent(BaseModel):
    name: str
    description: str


class OtherAgent(BaseModel):
    name: str
    description: str
    trace_share: float


class AgentFleetConfig(BaseModel):
    hero_agent: HeroAgent
    other_agents: list[OtherAgent]


class LLMModels(BaseModel):
    orchestrator: str
    research_crew: str
    strategy_crew: str
    compliance: str
    judge: str


class LLMConfig(BaseModel):
    provider: str
    models: LLMModels
    prompt_caching: bool
    max_tokens_default: int


class SyntheticConfig(BaseModel):
    total_traces: int
    days_back: int
    business_hours_weighting: bool
    hero_agent_share: float
    error_rate: float
    cost_outlier_rate: float
    failure_mode_distribution: dict[str, float]

    @field_validator("failure_mode_distribution")
    @classmethod
    def distribution_sums_to_reasonable(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if total >= 1.0:
            raise ValueError(
                f"failure_mode_distribution values sum to {total:.3f}, must be < 1.0"
            )
        return v


class DemoQuery(BaseModel):
    id: str
    text: str
    expected_outcome: str


class LangfuseConfig(BaseModel):
    host: str
    project_name: str
    capture_everything: bool
    retention_days_traces: int
    retention_days_observations: int


Backend = Literal["langfuse"]


class DemoConfig(BaseModel):
    customer: CustomerConfig
    catalog: CatalogConfig
    regions: list[str]
    retail_partners: list[str]
    compliance: ComplianceConfig
    agent_fleet: AgentFleetConfig
    llm: LLMConfig
    synthetic_history: SyntheticConfig
    live_demo_queries: list[DemoQuery]
    backend: Backend = "langfuse"
    langfuse: LangfuseConfig | None = None

    @model_validator(mode="after")
    def _check_backend_config_present(self) -> DemoConfig:
        if self.backend == "langfuse" and self.langfuse is None:
            raise ValueError("backend=langfuse requires a `langfuse:` block in demo.config.yaml")
        return self

    def all_skus(self) -> list[str]:
        """Collect every hero SKU across all brand families."""
        skus: list[str] = []
        for bf in self.catalog.brand_families:
            skus.extend(bf.hero_skus)
        return skus

    def all_brand_names(self) -> list[str]:
        return [bf.name for bf in self.catalog.brand_families]


class EnvConfig(BaseModel):
    anthropic_api_key: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None
    langfuse_admin_token: str | None = None
    tavily_api_key: str | None = None
    backend: Backend | None = None


def load_config(path: str = "demo.config.yaml") -> DemoConfig:
    """Load and validate demo config from YAML."""
    config_path = Path(path)
    if not config_path.is_absolute():
        # Try relative to CWD first, then relative to this file's project root
        if not config_path.exists():
            project_root = Path(__file__).parent.parent
            config_path = project_root / path
    with open(config_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return DemoConfig.model_validate(raw)


def load_env() -> EnvConfig:
    """Load .env and return typed credential config.

    `override=False` so that an explicit shell export (e.g. `BACKEND=langfuse`
    in front of `uv run python ...`) wins over the dotenv file. Otherwise a
    stale BACKEND value in .env silently flips the backend out from under the
    operator.
    """
    project_root = Path(__file__).parent.parent
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)
    backend_env = os.getenv("BACKEND")
    backend: Backend | None = None
    if backend_env == "langfuse":
        backend = backend_env  # type: ignore[assignment]
    return EnvConfig(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        langfuse_host=os.getenv("LANGFUSE_HOST"),
        langfuse_admin_token=os.getenv("LANGFUSE_ADMIN_TOKEN"),
        tavily_api_key=os.getenv("TAVILY_API_KEY"),
        backend=backend,
    )


def resolve_backend() -> Backend:
    """Resolve the active backend. Langfuse is the only supported backend, so
    this always resolves to "langfuse". Kept as a function (not a constant) so
    callers have a single resolution point if more backends are added later.
    """
    env_backend = os.getenv("BACKEND")
    if env_backend == "langfuse":
        return env_backend  # type: ignore[return-value]
    try:
        return load_config().backend
    except Exception:
        return "langfuse"
