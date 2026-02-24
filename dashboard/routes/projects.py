"""Projects endpoint — distinct project names from traces."""

from fastapi import APIRouter, Query

from data_models import ProjectResponse
from langfuse_client import fetch_all_traces

router = APIRouter()


@router.get("/api/projects", response_model=ProjectResponse)
async def get_projects(
    from_ts: str | None = Query(None, alias="from"),
    to_ts: str | None = Query(None, alias="to"),
):
    traces = await fetch_all_traces(from_ts=from_ts, to_ts=to_ts)

    projects = set()
    for t in traces:
        name = t.get("name")
        if name:
            projects.add(name)

    return ProjectResponse(projects=sorted(projects))
