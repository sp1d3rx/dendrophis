"""Subagent handlers — actual implementations."""

from .code_reviewer import execute as code_reviewer_execute
from .code_writer import CodeWriterHandler
from .debugger import execute as debugger_execute
from .planner import execute as planner_execute
from .researcher import ResearcherHandler
from .test_runner import TestRunnerHandler

__all__ = [
    "CodeWriterHandler",
    "ResearcherHandler",
    "TestRunnerHandler",
    "code_reviewer_execute",
    "debugger_execute",
    "planner_execute",
]
