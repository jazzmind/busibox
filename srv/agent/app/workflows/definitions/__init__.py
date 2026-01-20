"""
Workflow definitions module.

Contains factory functions for creating predefined workflows.
"""

from .web_research import create_web_research_workflow, WEB_RESEARCH_WORKFLOW_DEFINITION

__all__ = [
    "create_web_research_workflow",
    "WEB_RESEARCH_WORKFLOW_DEFINITION",
]
