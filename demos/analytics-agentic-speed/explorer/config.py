"""Settings for the analytics agent, read from the demo's own `.env`.

The demo's `.env` is loaded with ``override=True`` on purpose: a shell-exported
``LANGFUSE_PUBLIC_KEY`` for another project would otherwise win and silently send
traces somewhere else (a repo-wide footgun, see the root CLAUDE.md).
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


def load_env() -> None:
    """Load the demo's .env (overriding the shell). ``EXPLORER_TARGET=playground`` in the
    shell or .env points the agent at the public playground instead of the Cloud service."""
    shell_target = os.getenv("EXPLORER_TARGET")
    load_dotenv(DEMO_DIR / ".env", override=True)
    if (shell_target or os.getenv("EXPLORER_TARGET", "cloud")) == "playground":
        os.environ.update(PLAYGROUND)


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
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


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
