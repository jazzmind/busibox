#!/usr/bin/env python3
"""
Cleanup test data left in the database by integration tests.

Run this script to remove spurious test entries (agents, tools, workflows)
that were created by tests but not properly cleaned up.

Usage:
    python scripts/cleanup_test_agents.py [--dry-run]

Options:
    --dry-run    Show what would be deleted without actually deleting
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, delete, or_
from app.db.session import get_session_context
from app.models.domain import AgentDefinition, ToolDefinition, WorkflowDefinition


async def cleanup_test_tools(session, dry_run: bool = False) -> int:
    """Remove test tools from the database."""
    print("\n🔧 Cleaning up test tools...")
    
    # Find tools with names matching test patterns
    stmt = select(ToolDefinition).where(
        or_(
            ToolDefinition.name.like("custom_test_tool_%"),
            ToolDefinition.name.like("builtin_test_tool_%"),
            ToolDefinition.name.like("unused_test_tool_%"),
        )
    )
    result = await session.execute(stmt)
    test_tools = result.scalars().all()
    
    if not test_tools:
        print("  ✅ No test tools found")
        return 0
    
    print(f"  Found {len(test_tools)} test tool(s):")
    for tool in test_tools:
        print(f"    - {tool.name} (builtin={tool.is_builtin}) [id: {tool.id}]")
    
    if dry_run:
        print(f"  🔍 DRY RUN: Would delete {len(test_tools)} test tool(s)")
        return 0
    
    # Delete test tools
    delete_stmt = delete(ToolDefinition).where(
        or_(
            ToolDefinition.name.like("custom_test_tool_%"),
            ToolDefinition.name.like("builtin_test_tool_%"),
            ToolDefinition.name.like("unused_test_tool_%"),
        )
    )
    result = await session.execute(delete_stmt)
    return result.rowcount


async def cleanup_test_workflows(session, dry_run: bool = False) -> int:
    """Remove test workflows from the database."""
    print("\n📋 Cleaning up test workflows...")
    
    # Find workflows with names matching test patterns
    stmt = select(WorkflowDefinition).where(
        or_(
            WorkflowDefinition.name.like("custom_test_workflow_%"),
            WorkflowDefinition.name.like("unused_test_workflow_%"),
        )
    )
    result = await session.execute(stmt)
    test_workflows = result.scalars().all()
    
    if not test_workflows:
        print("  ✅ No test workflows found")
        return 0
    
    print(f"  Found {len(test_workflows)} test workflow(s):")
    for workflow in test_workflows:
        print(f"    - {workflow.name} [id: {workflow.id}]")
    
    if dry_run:
        print(f"  🔍 DRY RUN: Would delete {len(test_workflows)} test workflow(s)")
        return 0
    
    # Delete test workflows
    delete_stmt = delete(WorkflowDefinition).where(
        or_(
            WorkflowDefinition.name.like("custom_test_workflow_%"),
            WorkflowDefinition.name.like("unused_test_workflow_%"),
        )
    )
    result = await session.execute(delete_stmt)
    return result.rowcount


async def cleanup_test_agents(session, dry_run: bool = False) -> int:
    """Remove test agents from the database."""
    print("\n🤖 Cleaning up test agents...")
    
    # Find agents with names matching test patterns
    test_display_names = [
        "Built-in Test Agent",
        "Test Chat Agent",
    ]
    
    stmt = select(AgentDefinition).where(
        or_(
            AgentDefinition.display_name.in_(test_display_names),
            AgentDefinition.name.like("builtin-test-agent-%"),
            AgentDefinition.name.like("user-a-research-assistant-%"),
            AgentDefinition.name.like("user-b-assistant-%"),
            AgentDefinition.name.like("test-chat-agent-%"),
            AgentDefinition.name.like("agent-using-tool-%"),
        )
    )
    result = await session.execute(stmt)
    test_agents = result.scalars().all()
    
    if not test_agents:
        print("  ✅ No test agents found")
        return 0
    
    print(f"  Found {len(test_agents)} test agent(s):")
    for agent in test_agents:
        print(f"    - {agent.name} ({agent.display_name}) [id: {agent.id}]")
    
    if dry_run:
        print(f"  🔍 DRY RUN: Would delete {len(test_agents)} test agent(s)")
        return 0
    
    # Delete test agents
    delete_stmt = delete(AgentDefinition).where(
        or_(
            AgentDefinition.display_name.in_(test_display_names),
            AgentDefinition.name.like("builtin-test-agent-%"),
            AgentDefinition.name.like("user-a-research-assistant-%"),
            AgentDefinition.name.like("user-b-assistant-%"),
            AgentDefinition.name.like("test-chat-agent-%"),
            AgentDefinition.name.like("agent-using-tool-%"),
        )
    )
    result = await session.execute(delete_stmt)
    return result.rowcount


async def cleanup_all_test_data(dry_run: bool = False):
    """Remove all test data from the database."""
    print("=" * 60)
    print("🧹 Cleaning up test data from database")
    print("=" * 60)
    
    async with get_session_context() as session:
        try:
            # Cleanup agents first (they may reference tools)
            agents_deleted = await cleanup_test_agents(session, dry_run)
            
            # Then cleanup tools
            tools_deleted = await cleanup_test_tools(session, dry_run)
            
            # Then cleanup workflows
            workflows_deleted = await cleanup_test_workflows(session, dry_run)
            
            if not dry_run:
                await session.commit()
                print("\n" + "=" * 60)
                print("✨ Cleanup complete!")
                print(f"   Agents deleted:   {agents_deleted}")
                print(f"   Tools deleted:    {tools_deleted}")
                print(f"   Workflows deleted: {workflows_deleted}")
                print("=" * 60)
            else:
                print("\n" + "=" * 60)
                print("🔍 DRY RUN complete - no changes made")
                print("=" * 60)
            
        except Exception as e:
            print(f"\n❌ Error cleaning up test data: {e}")
            await session.rollback()
            raise


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(cleanup_all_test_data(dry_run=dry_run))
