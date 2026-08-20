"""Focused tests for admin model routing and user knowledge scope controls."""

from types import SimpleNamespace

import pytest

from app.agents import chat_agent as chat_agent_module
from app.services import platform_config
from app.services.agentic_dispatcher import create_chat_agent_for_routing_mode
from app.tools.document_search_tool import _resolve_knowledge_scope, search_documents


class FakeBusiboxClient:
    def __init__(self) -> None:
        self.requested_libraries: list[str] = []
        self.search_file_ids = None

    async def library_documents(self, library_id: str):
        self.requested_libraries.append(library_id)
        return {
            "library-a": [
                {"fileId": "file-1"},
                {"file_id": "file-2"},
                {"fileId": "file-1"},
            ],
            "library-b": [{"id": "file-3"}],
        }.get(library_id, [])

    async def search(self, **kwargs):
        self.search_file_ids = kwargs.get("file_ids")
        return {
            "results": [
                {
                    "file_id": "file-1",
                    "filename": "Policy.pdf",
                    "text": "Approved policy text",
                    "score": 0.95,
                    "page_number": 1,
                    "chunk_index": 0,
                }
            ]
        }


def make_context(metadata: dict):
    client = FakeBusiboxClient()
    context = SimpleNamespace(deps=SimpleNamespace(metadata=metadata, busibox_client=client))
    return context, client


@pytest.mark.parametrize(
    ("mode", "expected_model", "expected_fallback", "normalized_mode"),
    [
        ("local", "chat", False, "local"),
        ("auto", "chat", True, "auto"),
        ("frontier", "frontier", False, "frontier"),
        ("browser-supplied-invalid-model", "chat", False, "local"),
    ],
)
def test_admin_routing_mode_creates_isolated_chat_agent(
    mode: str,
    expected_model: str,
    expected_fallback: bool,
    normalized_mode: str,
) -> None:
    agent, normalized = create_chat_agent_for_routing_mode(mode)

    assert normalized == normalized_mode
    assert agent.config.model == expected_model
    assert agent.config.allow_frontier_fallback is expected_fallback
    assert agent.control_model == ("frontier" if normalized_mode == "frontier" else "fast")
    assert agent.planning_model == (
        "frontier" if normalized_mode == "frontier" else "tool_calling"
    )


def test_chat_agent_instances_do_not_share_per_request_model_state() -> None:
    local, _ = create_chat_agent_for_routing_mode("local")
    frontier, _ = create_chat_agent_for_routing_mode("frontier")

    assert local is not frontier
    assert local.synthesis_model is not frontier.synthesis_model
    assert local.config.model == "chat"
    assert frontier.config.model == "frontier"
    assert local.control_model == "fast"
    assert frontier.control_model == "frontier"
    assert local.planning_model == "tool_calling"
    assert frontier.planning_model == "frontier"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_model"),
    [("local", "fast"), ("auto", "fast"), ("frontier", "frontier")],
)
async def test_admin_policy_controls_the_simple_response_model(
    monkeypatch,
    mode: str,
    expected_model: str,
) -> None:
    requested_models: list[str] = []

    class FakeLLMClient:
        async def chat_completion(self, *, model: str, **_kwargs):
            requested_models.append(model)
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '{"action_type":"direct","needs_tools":false,'
                            '"response":"Hello!","confidence":0.99,'
                            '"complexity":"simple"}'
                        )
                    }
                }]
            }

    monkeypatch.setattr(chat_agent_module, "get_client", lambda: FakeLLMClient())
    agent, _ = create_chat_agent_for_routing_mode(mode)
    context = SimpleNamespace(
        compressed_history_summary=None,
        recent_messages=[],
        attachment_metadata=[],
        insights_enabled=False,
        pending_questions=[],
        missing_profile_fields=[],
    )

    decision = await agent._generate_fast_ack("Hello", context)

    assert decision.response == "Hello!"
    assert requested_models == [expected_model]


@pytest.mark.asyncio
async def test_platform_config_refreshes_valid_admin_routing_mode(monkeypatch) -> None:
    async def fake_config():
        return {"chat_model_routing_mode": "frontier"}

    monkeypatch.setattr(platform_config, "_fetch_public_config", fake_config)
    monkeypatch.setattr(platform_config, "_cached_chat_model_routing_mode", "local")

    await platform_config.refresh_platform_config()

    assert platform_config.get_chat_model_routing_mode() == "frontier"


@pytest.mark.asyncio
async def test_invalid_platform_routing_mode_keeps_safe_local_default(monkeypatch) -> None:
    async def fake_config():
        return {"chat_model_routing_mode": "untrusted-browser-value"}

    monkeypatch.setattr(platform_config, "_fetch_public_config", fake_config)
    monkeypatch.setattr(platform_config, "_cached_chat_model_routing_mode", "local")

    await platform_config.refresh_platform_config()

    assert platform_config.get_chat_model_routing_mode() == "local"


@pytest.mark.asyncio
async def test_all_scope_searches_all_accessible_documents() -> None:
    context, client = make_context({"knowledge_scope": "all"})

    file_ids, source = await _resolve_knowledge_scope(context, ["model-picked-file"])

    assert file_ids is None
    assert source == "all_accessible"
    assert client.requested_libraries == []


@pytest.mark.asyncio
async def test_attachment_scope_ignores_model_file_ids() -> None:
    context, _ = make_context({
        "knowledge_scope": "attachments",
        "attachment_file_ids": ["attached-1", "attached-2"],
    })

    file_ids, source = await _resolve_knowledge_scope(context, ["global-file"])

    assert file_ids == ["attached-1", "attached-2"]
    assert source == "attachments"


@pytest.mark.asyncio
async def test_library_scope_resolves_accessible_documents_and_deduplicates() -> None:
    context, client = make_context({
        "knowledge_scope": "libraries",
        "selected_library_ids": ["library-a", "library-b"],
    })

    file_ids, source = await _resolve_knowledge_scope(context, ["global-file"])

    assert file_ids == ["file-1", "file-2", "file-3"]
    assert source == "libraries"
    assert client.requested_libraries == ["library-a", "library-b"]


@pytest.mark.asyncio
async def test_prevalidated_library_scope_uses_server_resolved_file_ids() -> None:
    context, client = make_context({
        "knowledge_scope": "libraries",
        "selected_library_ids": ["untrusted-browser-library"],
        "selected_library_file_ids": ["validated-file-1", "validated-file-2"],
    })

    file_ids, source = await _resolve_knowledge_scope(context, None)

    assert file_ids == ["validated-file-1", "validated-file-2"]
    assert source == "libraries"
    assert client.requested_libraries == []


@pytest.mark.asyncio
async def test_empty_attachments_scope_returns_grounded_no_documents_response() -> None:
    context, client = make_context({
        "knowledge_scope": "attachments",
        "attachment_file_ids": [],
    })

    result = await search_documents(context, "What does the document say?")

    assert result.found is False
    assert result.result_count == 0
    assert result.context == "No searchable documents are available in the attachments scope."
    assert client.search_file_ids is None


@pytest.mark.asyncio
async def test_library_scope_passes_only_server_resolved_files_to_search() -> None:
    context, client = make_context({
        "knowledge_scope": "libraries",
        "selected_library_file_ids": ["file-1"],
    })

    result = await search_documents(context, "What is the policy?")

    assert result.found is True
    assert result.results[0].file_id == "file-1"
    assert client.search_file_ids == ["file-1"]
