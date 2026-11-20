"""
Run one agent as an A2A service.

    .venv/bin/python -m handoff.serve --agent billing          # the real agent
    .venv/bin/python -m handoff.serve --agent billing --stub   # deterministic stand-in
"""

from __future__ import annotations

import argparse
import logging

from handoff import config

DESCRIPTIONS = {
    "tech-support": ("Northwind Mobile technical support: connectivity, outages, device and line diagnostics.",
                     "network-ops"),
    "billing": ("Northwind Mobile billing: invoices, plans, outage credits and refunds.", "billing"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True, choices=sorted(config.PORTS))
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    config.get_langfuse(args.agent)  # bind the process's one client before anything else
    from handoff.a2a_service import build_app

    if args.stub:
        from handoff.stubs import HANDLERS

        handler, skills = HANDLERS[args.agent], [{"id": "stub", "name": "stub", "description": "stub", "tags": ["stub"]}]
    else:
        from handoff.agents.registry import service_handler, skills_for

        handler, skills = service_handler(args.agent), skills_for(args.agent)
    description, team = DESCRIPTIONS[args.agent]
    app = build_app(args.agent, handler, description=description, skills=skills, team=team,
                    url=config.agent_url(args.agent))

    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORTS[args.agent], log_level="warning")


if __name__ == "__main__":
    main()
