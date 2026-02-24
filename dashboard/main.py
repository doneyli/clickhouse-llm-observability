"""LLM Observatory — Analytics dashboard for Langfuse observability data."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routes import kpi, sessions, activity, top_sessions, tools, scores, export, projects

app = FastAPI(title="LLM Observatory", version="1.0.0")

# API routes
app.include_router(kpi.router)
app.include_router(sessions.router)
app.include_router(activity.router)
app.include_router(top_sessions.router)
app.include_router(tools.router)
app.include_router(scores.router)
app.include_router(export.router)
app.include_router(projects.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# Static files (must be last — catches all remaining paths)
app.mount("/", StaticFiles(directory="static", html=True), name="static")
