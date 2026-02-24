"""Tool/span usage — powered by ClickHouse."""

from fastapi import APIRouter, Query

from clickhouse_client import get_tools

router = APIRouter()


@router.get("/api/tools")
async def tools_endpoint(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
):
    tools = await get_tools(from_ts=from_ts, to_ts=to_ts)
    return {"tools": tools}
