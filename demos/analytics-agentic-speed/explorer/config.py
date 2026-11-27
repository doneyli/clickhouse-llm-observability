"""Settings for the analytics agent, read from the demo's own `.env`.

A shell-exported ``LANGFUSE_PUBLIC_KEY`` for another project would otherwise win and
silently send traces somewhere else (a repo-wide footgun, see the root CLAUDE.md). So
``load_env`` drops every inherited ``LANGFUSE_*`` variable before it reads the demo's
`.env`, and fails when the file or a required key is missing. ``override=True`` alone is
not enough: a key the file leaves out would still come from the shell.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEMO_DIR = Path(__file__).resolve().parent.parent


# The same flights data on ClickHouse's public playground: a zero-setup target for
# rehearsing without a Cloud service. Its latency is shared and not ours to show.
PLAYGROUND = {
    "CH_HOST": "sql-clickhouse.clickhouse.com", "CH_PORT": "8443", "CH_USER": "demo",
    "CH_PASSWORD": "", "CH_DATABASE": "ontime", "CH_TABLE": "ontime",
}


ENV_FILE = DEMO_DIR / ".env"
LANGFUSE_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


class EnvError(SystemExit):
    """The demo's environment is unusable. A SystemExit, so a CLI prints it and exits 1."""


def in_github_actions() -> bool:
    """True only on a GitHub Actions runner, where the workflow step env is the source."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def missing_langfuse_vars(env: "os._Environ[str] | dict[str, str]") -> list[str]:
    missing = [k for k in LANGFUSE_KEYS if not (env.get(k) or "").strip()]
    if not ((env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or "").strip()):
        missing.append("LANGFUSE_BASE_URL")
    return missing


def load_env(*, require_langfuse: bool = True, env_file: Path | None = None) -> str:
    """Load the demo's .env and return where the settings came from.

    Every inherited ``LANGFUSE_*`` variable is dropped first, so the keys, host and base
    URL all come from the file. With no file, only a GitHub Actions runner (positively
    identified by ``GITHUB_ACTIONS=true``) may use its step env instead; anywhere else it
    is an ``EnvError``. ``require_langfuse`` also fails on a missing Langfuse key or base
    URL before any API is used. ``EXPLORER_TARGET=playground`` in the shell or .env
    points the agent at the public playground instead of the Cloud service.
    """
    path = env_file or ENV_FILE
    shell_target = os.getenv("EXPLORER_TARGET")
    if path.is_file():
        for k in [k for k in os.environ if k.startswith("LANGFUSE_")]:
            del os.environ[k]
        load_dotenv(path, override=True)
        source = str(path)
    elif in_github_actions():
        source = "the GitHub Actions step env"
    else:
        raise EnvError(f"no .env at {path}: copy .env.example to .env and fill it in "
                       "(shell-exported LANGFUSE_* keys are never used)")
    if require_langfuse:
        missing = missing_langfuse_vars(os.environ)
        if missing:
            raise EnvError(f"missing from {source}: {', '.join(missing)}")
        if not os.environ.get("LANGFUSE_BASE_URL", "").strip():
            os.environ["LANGFUSE_BASE_URL"] = os.environ["LANGFUSE_HOST"].strip()
    if (shell_target or os.getenv("EXPLORER_TARGET", "cloud")) == "playground":
        os.environ.update(PLAYGROUND)
    return source


@dataclass(frozen=True)
class ClickHouseSettings:
    host: str
    user: str
    password: str
    database: str = "flights"
    table: str = "ontime"
    port: int = 8443
    secure: bool = True

    @property
    def qualified_table(self) -> str:
        return f"{self.database}.{self.table}"

    @property
    def url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.host}:{self.port}/"

    @classmethod
    def from_env(cls, *, admin: bool = False) -> "ClickHouseSettings":
        """Agent connection by default; ``admin=True`` only for scripts/load_data.py."""
        user_var, pw_var = ("CH_ADMIN_USER", "CH_ADMIN_PASSWORD") if admin else ("CH_USER", "CH_PASSWORD")
        return cls(
            host=os.environ["CH_HOST"],
            user=os.environ[user_var],
            password=os.environ.get(pw_var, ""),
            database=os.getenv("CH_DATABASE", "flights"),
            table=os.getenv("CH_TABLE", "ontime"),
            port=int(os.getenv("CH_PORT", "8443")),
            secure=os.getenv("CH_SECURE", "true").lower() != "false",
        )


# Per-query limits sent with every agent query. The read-only user's grants are the
# real fence; these keep one bad template from scanning forever or flooding memory.
DEFAULT_QUERY_SETTINGS: dict[str, str] = {
    "max_execution_time": "20",
    "max_result_rows": "1000",
    "result_overflow_mode": "throw",
    "max_rows_to_read": "1000000000",
    "use_query_cache": "0",  # honest timings: never serve a scenario from the query cache
}

# USD per million tokens (input, output, cache read). Passed to Langfuse as cost_details
# so run cost is right even before Langfuse Cloud knows a model's price.
MODEL_PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


class UnknownModelPriceError(ValueError):
    """Spend cannot be measured safely for this model."""


def model_prices(model: str) -> tuple[float, float, float]:
    try:
        return MODEL_PRICES[model]
    except KeyError:
        raise UnknownModelPriceError(
            f"No pricing configured for model {model!r}; add verified pricing to MODEL_PRICES before running it."
        ) from None


@dataclass
class RunConfig:
    """Knobs for one agent run. ``max_scenarios_per_round`` is the hero knob."""

    max_scenarios_per_round: int = 300
    max_rounds: int = 4
    concurrency: int = 32
    budget_usd: float = 3.0
    model: str = field(default_factory=lambda: os.getenv("EXPLORER_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: os.getenv("EXPLORER_EFFORT", "medium"))
    prompt_label: str = field(default_factory=lambda: os.getenv("EXPLORER_PROMPT_LABEL", "production"))
    # One NUMERIC score per query span (db_server_ms). Off = round/trace aggregates only.
    score_each_query: bool = True
    # A gate resolves the pair once. An empty mapping keeps normal live label lookup.
    prompt_versions: dict[str, int] = field(default_factory=dict)
