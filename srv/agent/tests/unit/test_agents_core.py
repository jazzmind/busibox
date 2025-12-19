"""
Unit tests for core Pydantic AI agents and tools.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.agents.core import (
    BusiboxDeps,
    ChatOutput,
    RagOutput,
    SearchOutput,
    SearchToolResult,
    chat_agent,
    ingest_tool,
    rag_agent,
    rag_tool,
    search_agent,
    search_tool,
)
from app.clients.busibox import BusiboxClient
from app.schemas.auth import Principal


@pytest.fixture
def mock_principal():
    """Create a mock principal for testing."""
    return Principal(
        sub="test-user",
        email="test@example.com",
        roles=["user", "admin"],
        scopes=["search.read", "ingest.write"],
        token="test-token",
    )


@pytest.fixture
def mock_busibox_client():
    """Create a mock Busibox client for testing."""
    client = AsyncMock(spec=BusiboxClient)
    return client


@pytest.fixture
def mock_deps(mock_principal, mock_busibox_client):
    """Create mock dependencies for agent execution."""
    return BusiboxDeps(principal=mock_principal, busibox_client=mock_busibox_client)


# ============================================================================
# Output Model Tests
# ============================================================================


def test_chat_output_validation():
    """Test ChatOutput validates message is not empty."""
    # Valid output
    output = ChatOutput(message="Hello world", tool_calls=[])
    assert output.message == "Hello world"
    assert output.tool_calls == []

    # Empty message should fail
    with pytest.raises(ValidationError):
        ChatOutput(message="", tool_calls=[])

    # Whitespace-only message should fail
    with pytest.raises(ValidationError):
        ChatOutput(message="   ", tool_calls=[])


def test_search_output_validation():
    """Test SearchOutput validates query is not empty."""
    # Valid output
    output = SearchOutput(query="test query", hits=[{"id": "1"}], total_hits=1)
    assert output.query == "test query"
    assert output.total_hits == 1

    # Empty query should fail
    with pytest.raises(ValidationError):
        SearchOutput(query="", hits=[], total_hits=0)


def test_rag_output_validation():
    """Test RagOutput validates answer and confidence."""
    # Valid output
    output = RagOutput(answer="Test answer", citations=[{"source": "doc1"}], confidence=0.95)
    assert output.answer == "Test answer"
    assert output.confidence == 0.95

    # Empty answer should fail
    with pytest.raises(ValidationError):
        RagOutput(answer="", citations=[])

    # Invalid confidence should fail
    with pytest.raises(ValidationError):
        RagOutput(answer="Test", citations=[], confidence=1.5)


# ============================================================================
# Tool Tests
# ============================================================================


@pytest.mark.asyncio
async def test_search_tool_success(mock_deps):
    """Test search_tool executes successfully with valid inputs."""
    # Mock search response
    mock_deps.busibox_client.search.return_value = {
        "hits": [{"id": "doc1", "score": 0.95}],
        "total": 1,
    }

    # Create mock context
    ctx = MagicMock()
    ctx.deps = mock_deps

    # Execute tool
    result = await search_tool(ctx, query="test query", top_k=5)

    # Verify result
    assert isinstance(result, SearchToolResult)
    assert result.query == "test query"
    assert result.total == 1
    assert len(result.hits) == 1

    # Verify client was called correctly
    mock_deps.busibox_client.search.assert_called_once_with(query="test query", top_k=5)


@pytest.mark.asyncio
async def test_search_tool_validates_inputs(mock_deps):
    """Test search_tool validates query and top_k parameters."""
    ctx = MagicMock()
    ctx.deps = mock_deps

    # Empty query should fail
    with pytest.raises(ValueError, match="query cannot be empty"):
        await search_tool(ctx, query="", top_k=5)

    # Invalid top_k should fail
    with pytest.raises(ValueError, match="top_k must be between"):
        await search_tool(ctx, query="test", top_k=0)

    with pytest.raises(ValueError, match="top_k must be between"):
        await search_tool(ctx, query="test", top_k=100)


@pytest.mark.asyncio
async def test_ingest_tool_success(mock_deps):
    """Test ingest_tool executes successfully with valid inputs."""
    # Mock ingest response
    mock_deps.busibox_client.ingest_document.return_value = {
        "document_id": "doc123",
        "status": "success",
    }

    # Create mock context
    ctx = MagicMock()
    ctx.deps = mock_deps

    # Execute tool
    result = await ingest_tool(ctx, path="/path/to/doc.pdf", metadata={"author": "test"})

    # Verify result
    assert result["document_id"] == "doc123"
    assert result["status"] == "success"

    # Verify client was called correctly
    mock_deps.busibox_client.ingest_document.assert_called_once_with(
        path="/path/to/doc.pdf", metadata={"author": "test"}
    )


@pytest.mark.asyncio
async def test_ingest_tool_validates_path(mock_deps):
    """Test ingest_tool validates path parameter."""
    ctx = MagicMock()
    ctx.deps = mock_deps

    # Empty path should fail
    with pytest.raises(ValueError, match="path cannot be empty"):
        await ingest_tool(ctx, path="", metadata=None)


@pytest.mark.asyncio
async def test_rag_tool_success(mock_deps):
    """Test rag_tool executes successfully with valid inputs."""
    # Mock RAG response
    mock_deps.busibox_client.rag_query.return_value = {
        "answer": "Test answer",
        "chunks": [{"text": "relevant chunk", "score": 0.9}],
    }

    # Create mock context
    ctx = MagicMock()
    ctx.deps = mock_deps

    # Execute tool
    result = await rag_tool(ctx, database="docs", query="test question", top_k=5)

    # Verify result
    assert result["answer"] == "Test answer"
    assert len(result["chunks"]) == 1

    # Verify client was called correctly
    mock_deps.busibox_client.rag_query.assert_called_once_with(
        database="docs", query="test question", top_k=5
    )


@pytest.mark.asyncio
async def test_rag_tool_validates_inputs(mock_deps):
    """Test rag_tool validates database, query, and top_k parameters."""
    ctx = MagicMock()
    ctx.deps = mock_deps

    # Empty database should fail
    with pytest.raises(ValueError, match="Database name cannot be empty"):
        await rag_tool(ctx, database="", query="test", top_k=5)

    # Empty query should fail
    with pytest.raises(ValueError, match="Query cannot be empty"):
        await rag_tool(ctx, database="docs", query="", top_k=5)

    # Invalid top_k should fail
    with pytest.raises(ValueError, match="top_k must be between"):
        await rag_tool(ctx, database="docs", query="test", top_k=0)


# ============================================================================
# Agent Definition Tests
# ============================================================================


def test_chat_agent_has_all_tools():
    """Test chat_agent is configured with all expected tools."""
    # Verify agent has tools registered (Pydantic AI 1.29.0 uses _function_toolset)
    assert chat_agent._function_toolset is not None
    tools = chat_agent._function_toolset.tools
    assert tools is not None
    assert len(tools) == 3

    # Verify tool names
    tool_names = list(tools.keys())
    assert "search_tool" in tool_names
    assert "ingest_tool" in tool_names
    assert "rag_tool" in tool_names


def test_rag_agent_has_search_and_rag_tools():
    """Test rag_agent is configured with search and RAG tools."""
    assert rag_agent._function_toolset is not None
    tools = rag_agent._function_toolset.tools
    assert tools is not None
    assert len(tools) == 2

    tool_names = list(tools.keys())
    assert "search_tool" in tool_names
    assert "rag_tool" in tool_names


def test_search_agent_has_search_tool():
    """Test search_agent is configured with search tool only."""
    assert search_agent._function_toolset is not None
    tools = search_agent._function_toolset.tools
    assert tools is not None
    assert len(tools) == 1

    tool_names = list(tools.keys())
    assert "search_tool" in tool_names


def test_agents_have_instructions():
    """Test all agents have non-empty instructions."""
    assert chat_agent.instructions is not None
    assert len(str(chat_agent.instructions)) > 0

    assert rag_agent.instructions is not None
    assert len(str(rag_agent.instructions)) > 0

    assert search_agent.instructions is not None
    assert len(str(search_agent.instructions)) > 0








