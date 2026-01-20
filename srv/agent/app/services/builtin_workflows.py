"""
Discover and load built-in workflows from the workflows/definitions directory.

This module exposes built-in workflow definitions without requiring database entries,
following the same pattern as builtin_agents.py.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from app.schemas.definitions import WorkflowDefinitionRead


# Mapping of workflow IDs to their metadata
# These are built-in workflows defined in code
BUILTIN_WORKFLOW_METADATA: Dict[str, Dict[str, Any]] = {
    "web-research-workflow": {
        "name": "web-research-workflow",
        "description": "Deep web research with deduplication, gateway page handling, and document storage",
        "version": 2,  # Bumped to force reload of updated steps
        "trigger": {
            "type": "manual",
            "allowed_types": ["manual", "api", "task"],
        },
        "guardrails": {
            "request_limit": 50,
            "tool_calls_limit": 100,
            "total_tokens_limit": 100000,
            "max_cost_dollars": 2.0,
            "timeout_seconds": 300,
        },
        "config": {
            "deep": False,
            "min_results": 5,
            "recency": None,
            "scrape_depth": 1,
            "store_results": True,
        },
    },
    "web-research-simple": {
        "name": "web-research-simple",
        "description": "Quick web research without deduplication or storage",
        "version": 2,  # Bumped to force reload of updated steps
        "trigger": {
            "type": "manual",
            "allowed_types": ["manual", "api", "task"],
        },
        "guardrails": {
            "request_limit": 10,
            "tool_calls_limit": 20,
            "max_cost_dollars": 0.5,
            "timeout_seconds": 60,
        },
    },
}


def _get_workflow_steps(workflow_name: str) -> List[Dict[str, Any]]:
    """
    Get the steps for a built-in workflow.
    
    This imports the workflow definition dynamically to avoid circular imports.
    
    Args:
        workflow_name: Name of the workflow
        
    Returns:
        List of workflow steps
    """
    if workflow_name == "web-research-workflow":
        from app.workflows.definitions.web_research import WEB_RESEARCH_WORKFLOW_DEFINITION
        return WEB_RESEARCH_WORKFLOW_DEFINITION.get("steps", [])
    elif workflow_name == "web-research-simple":
        from app.workflows.definitions.web_research import WEB_RESEARCH_SIMPLE_WORKFLOW
        return WEB_RESEARCH_SIMPLE_WORKFLOW.get("steps", [])
    return []


def get_builtin_workflow_definitions() -> List[WorkflowDefinitionRead]:
    """
    Get workflow definitions for all built-in workflows.
    
    Returns:
        List of WorkflowDefinitionRead objects for built-in workflows
    """
    definitions = []
    
    for workflow_name, metadata in BUILTIN_WORKFLOW_METADATA.items():
        # Generate a deterministic UUID based on the workflow name
        workflow_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.workflow.{workflow_name}")
        
        # Get the steps for this workflow
        steps = _get_workflow_steps(workflow_name)
        
        # Use current timestamp
        now = datetime.now(timezone.utc)
        
        definition = WorkflowDefinitionRead(
            id=workflow_uuid,
            name=metadata["name"],
            description=metadata.get("description"),
            steps=steps,
            trigger=metadata.get("trigger", {}),
            guardrails=metadata.get("guardrails"),
            is_active=True,
            is_builtin=True,
            created_by=None,
            version=metadata.get("version", 1),
            created_at=now,
            updated_at=now,
        )
        definitions.append(definition)
    
    return definitions


def get_builtin_workflow_by_name(name: str) -> Optional[WorkflowDefinitionRead]:
    """
    Get a built-in workflow definition by name.
    
    Args:
        name: Workflow name (e.g., "web-research-workflow")
        
    Returns:
        WorkflowDefinitionRead or None if not found
    """
    definitions = get_builtin_workflow_definitions()
    for definition in definitions:
        if definition.name == name:
            return definition
    return None


def get_builtin_workflow_by_id(workflow_id: uuid.UUID) -> Optional[WorkflowDefinitionRead]:
    """
    Get a built-in workflow definition by UUID.
    
    Args:
        workflow_id: Workflow UUID
        
    Returns:
        WorkflowDefinitionRead or None if not found
    """
    definitions = get_builtin_workflow_definitions()
    for definition in definitions:
        if definition.id == workflow_id:
            return definition
    return None


def is_builtin_workflow(workflow_id: uuid.UUID) -> bool:
    """
    Check if a workflow ID corresponds to a built-in workflow.
    
    Args:
        workflow_id: Workflow UUID
        
    Returns:
        True if this is a built-in workflow
    """
    for workflow_name in BUILTIN_WORKFLOW_METADATA.keys():
        expected_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.workflow.{workflow_name}")
        if expected_uuid == workflow_id:
            return True
    return False
