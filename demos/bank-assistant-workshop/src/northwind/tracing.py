"""Lesson 02 solution. These three seams instrument the copied banking agent."""
from contextlib import contextmanager

ENABLED = True


def callbacks():
    from langfuse.langchain import CallbackHandler
    return [CallbackHandler()]


@contextmanager
def attributes(**kwargs):
    from langfuse import propagate_attributes
    with propagate_attributes(**kwargs):
        yield
