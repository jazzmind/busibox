#!/usr/bin/env python3
"""
Cleanup test agents left in the database by integration tests.

Run this script to remove spurious "Built-in Test Agent" entries
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

from sqlalchemy import select, delete
from app.config.database import get_session
from app.models.domain import AgentDefinition


async def cleanup_test_agents(dry_run: bool = False):
    """Remove test agents from the database."""
    async for session in get_session():
        try:
            print("🧹 Cleaning up test agents...")
            
            # Find agents with names matching test patterns
            test_patterns = [
                # Pattern: builtin-test-agent-<uuid>
                "builtin-test-agent-%",
                # Pattern: user-a-research-assistant-<uuid>
                "user-a-research-assistant-%",
                "user-b-assistant-%",
                # Pattern: test-chat-agent-<uuid>
                "test-chat-agent-%",
                # Any agent with "test" in the name and a uuid suffix
            ]
            
            # Also find by display name
            test_display_names = [
                "Built-in Test Agent",
                "Test Chat Agent",
            ]
            
            # Count before deletion
            stmt = select(AgentDefinition).where(
                AgentDefinition.display_name.in_(test_display_names)
            )
            result = await session.execute(stmt)
            test_agents = result.scalars().all()
            
            if not test_agents:
                print("  ✅ No test agents found")
                return
            
            print(f"  Found {len(test_agents)} test agent(s):")
            for agent in test_agents:
                print(f"    - {agent.name} ({agent.display_name}) [id: {agent.id}]")
            
            if dry_run:
                print(f"\n🔍 DRY RUN: Would delete {len(test_agents)} test agent(s)")
                return
            
            # Delete by display name
            delete_stmt = delete(AgentDefinition).where(
                AgentDefinition.display_name.in_(test_display_names)
            )
            result = await session.execute(delete_stmt)
            deleted_count = result.rowcount
            
            await session.commit()
            print(f"\n✨ Deleted {deleted_count} test agent(s)")
            
        except Exception as e:
            print(f"\n❌ Error cleaning up test agents: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
            break  # Only use first session


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(cleanup_test_agents(dry_run=dry_run))
