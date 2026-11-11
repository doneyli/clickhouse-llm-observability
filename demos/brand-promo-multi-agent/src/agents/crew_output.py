"""Preserve every crew task's result for downstream promotion planning."""

from crewai.crews.crew_output import CrewOutput


def format_crew_output(result: CrewOutput) -> str:
    return (
        "\n\n".join(f"## {task.name or task.agent}\n{task.raw}" for task in result.tasks_output)
        or result.raw
    )
