from pydantic import BaseModel


class KPIResponse(BaseModel):
    sessions_count: int = 0
    traces_count: int = 0
    projects_count: int = 0
    active_days: int = 0
    median_traces_per_session: float = 0
    p90_traces_per_session: float = 0
    avg_latency_ms: float = 0
    total_cost: float = 0
    avg_score: float | None = None


class SessionSummary(BaseModel):
    id: str
    trace_count: int = 0
    first_trace: str | None = None
    last_trace: str | None = None
    project: str | None = None
    total_cost: float = 0
    total_latency_ms: float = 0
    total_tokens: int = 0


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary]
    total: int = 0
    page: int = 1
    limit: int = 50


class TraceDetail(BaseModel):
    id: str
    name: str | None = None
    timestamp: str | None = None
    input: str | None = None
    output: str | None = None
    latency_ms: float = 0
    cost: float = 0
    tokens: int = 0
    scores: dict[str, float] = {}


class SessionDetailResponse(BaseModel):
    id: str
    traces: list[TraceDetail]
    total_cost: float = 0
    total_tokens: int = 0
    total_latency_ms: float = 0
    trace_count: int = 0


class HeatmapDay(BaseModel):
    date: str
    count: int


class HeatmapResponse(BaseModel):
    days: list[HeatmapDay]
    max_count: int = 0


class HourlyActivity(BaseModel):
    matrix: list[list[int]]  # 7 rows (Mon-Sun) x 24 cols (hours)
    day_totals: list[int]  # 7 day totals
    max_count: int = 0


class TopSession(BaseModel):
    id: str
    project: str | None = None
    trace_count: int = 0
    total_duration_ms: float = 0
    total_cost: float = 0
    first_trace: str | None = None


class TopSessionsResponse(BaseModel):
    sessions: list[TopSession]
    sort_by: str = "traces"


class ToolUsage(BaseModel):
    name: str
    count: int = 0
    avg_latency_ms: float = 0


class ToolsResponse(BaseModel):
    tools: list[ToolUsage]


class ScoreDistribution(BaseModel):
    name: str
    count: int = 0
    avg: float = 0
    min: float = 0
    max: float = 0
    values: list[float] = []


class ScoresResponse(BaseModel):
    scores: list[ScoreDistribution]


class ProjectResponse(BaseModel):
    projects: list[str]
