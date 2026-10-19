"""
Policy-administration and screening MCP server — run: `.venv/bin/python -m claims.mcp_server`

A separate process exposing the systems of record as MCP tools over streamable HTTP,
the way an integration team fronts policy admin, the claims system and sanctions
screening. Synthetic data from data/claims/*.json.

Cross-process tracing: the claims graph puts W3C `traceparent` + `baggage` in each
request's MCP `_meta`; this server extracts both and opens its spans as children, so
one trace shows agent → MCP client → MCP server → system-of-record call, and the
server's observations carry the claim's session, user, version and environment.
"""

from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from opentelemetry import context as otel_context

from claims import claimsdb, config

langfuse = config.get_langfuse(service_name="policy-admin-mcp")
mcp = FastMCP("northwind-policy-admin", host="127.0.0.1", port=config.MCP_PORT)


def _carrier(ctx: Context | None) -> dict:
    try:
        meta = ctx.request_context.meta if ctx else None
        if meta is None:
            return {}
        extra = getattr(meta, "model_extra", None) or {}
        return {k: v for k, v in {**extra, **(meta if isinstance(meta, dict) else {})}.items()
                if k in config.CARRIER_KEYS}
    except Exception:  # noqa: BLE001
        return {}


def _run(ctx, name: str, payload: dict, system: str, fn):
    carrier = _carrier(ctx)
    token = otel_context.attach(config.extract_trace_context(carrier)) if carrier else None
    try:
        with langfuse.start_as_current_observation(
                as_type="span", name=f"mcp-server: {name}", input=payload,
                metadata={"mcp.server": "northwind-policy-admin", "mcp.tool": name,
                          "linked_by": "traceparent + baggage in MCP _meta" if "baggage" in carrier else
                          "traceparent in MCP _meta" if carrier else "none"}) as span:
            with langfuse.start_as_current_observation(as_type="span", name=f"{system}.{name}",
                                                       metadata={"system": system}):
                try:
                    result = fn()
                except KeyError as exc:
                    result = {"error": "NOT_FOUND", "detail": str(exc)}
            span.update(output=result, level="WARNING" if isinstance(result, dict) and "error" in result else "DEFAULT")
            return result
    finally:
        if token is not None:
            otel_context.detach(token)


@mcp.tool()
def get_claim_file(claim_id: str, ctx: Context = None) -> dict:
    """Claim header from the claims system: policy number, dates, claimant, documents and forms received."""
    return _run(ctx, "get_claim_file", {"claim_id": claim_id}, "claims-admin",
                lambda: claimsdb.claim_header(claimsdb.get(claim_id)))


@mcp.tool()
def get_policy(policy_number: str, ctx: Context = None) -> dict:
    """Policy record: product line, governing state, issue date, status at death, amounts, insured."""
    return _run(ctx, "get_policy", {"policy_number": policy_number}, "policy-admin",
                lambda: claimsdb.policy_record(claimsdb.by_policy(policy_number)))


@mcp.tool()
def get_beneficiaries(policy_number: str, ctx: Context = None) -> dict:
    """Beneficiary designation on file: names, relationships, dates of birth, shares, living/deceased."""
    return _run(ctx, "get_beneficiaries", {"policy_number": policy_number}, "policy-admin",
                lambda: {"beneficiaries": claimsdb.beneficiaries(claimsdb.by_policy(policy_number))})


@mcp.tool()
def get_agent_compensation(policy_number: str, ctx: Context = None) -> dict:
    """Agent of record and whether first-year (issue) commission was paid on the policy."""
    return _run(ctx, "get_agent_compensation", {"policy_number": policy_number}, "agency-admin",
                lambda: claimsdb.compensation(claimsdb.by_policy(policy_number)))


@mcp.tool()
def get_employment_status(policy_number: str, ctx: Context = None) -> dict:
    """Group life only: employer, employment status, termination date, conversion/portability election."""
    return _run(ctx, "get_employment_status", {"policy_number": policy_number}, "group-admin",
                lambda: claimsdb.employment(claimsdb.by_policy(policy_number)))


@mcp.tool()
def get_service_request(request_id: str, ctx: Context = None) -> dict:
    """Annuity administration: an in-force service request with its contract, owner, consumer profile and documents."""
    return _run(ctx, "get_service_request", {"request_id": request_id}, "annuity-admin",
                lambda: claimsdb.service_record(claimsdb.get_service(request_id)))


@mcp.tool()
def screen_parties(claim_id: str, ctx: Context = None) -> dict:
    """Sanctions (OFAC) screen of the insured and every beneficiary on the claim."""
    return _run(ctx, "screen_parties", {"claim_id": claim_id}, "compliance-screening",
                lambda: claimsdb.screening(claimsdb.get(claim_id)))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
