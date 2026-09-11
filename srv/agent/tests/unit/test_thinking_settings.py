"""
Unit tests for cloud-routed thinking settings.

Covers:
- CLOUD_ROUTED_ALIASES default includes agent/default
- _routes_to_cloud detection for cloud aliases
- Frontier path injects reasoning_effort (no Qwen max_thinking_tokens)
- Local MLX path still sets max_thinking_tokens when not cloud-routed
"""

from unittest.mock import patch

from app.agents.base_agent import AgentConfig, BaseStreamingAgent
from app.config.settings import Settings


def _bare_agent(model: str) -> BaseStreamingAgent:
    """Build a BaseStreamingAgent without running __init__ side effects."""
    agent = object.__new__(BaseStreamingAgent)
    agent.config = AgentConfig(
        name="thinking-test",
        display_name="Thinking Test",
        instructions="test",
        tools=[],
        model=model,
    )
    return agent


class TestCloudRoutedAliasesDefault:
    def test_default_includes_agent_and_default(self):
        aliases = {
            a.strip()
            for a in Settings.model_fields["cloud_routed_aliases"].default.split(",")
            if a.strip()
        }
        assert "agent" in aliases
        assert "default" in aliases
        assert "chat" in aliases
        assert "research" in aliases


class TestRoutesToCloud:
    @patch("app.agents.base_agent.get_settings")
    def test_agent_and_default_are_cloud_routed(self, mock_settings):
        mock_settings.return_value.cloud_routed_aliases = (
            "agent,default,chat,research,frontier,frontier-fast,fallback"
        )
        assert BaseStreamingAgent._routes_to_cloud("agent") is True
        assert BaseStreamingAgent._routes_to_cloud("default") is True
        assert BaseStreamingAgent._routes_to_cloud("research") is True

    @patch("app.agents.base_agent.get_settings")
    def test_local_alias_not_cloud_when_omitted(self, mock_settings):
        # Simulate an override that omits agent (legacy deploy env).
        mock_settings.return_value.cloud_routed_aliases = "chat,research,frontier"
        assert BaseStreamingAgent._routes_to_cloud("agent") is False
        assert BaseStreamingAgent._routes_to_cloud("chat") is True

    @patch("app.agents.base_agent.get_settings")
    def test_frontier_prefix_always_cloud(self, mock_settings):
        mock_settings.return_value.cloud_routed_aliases = ""
        assert BaseStreamingAgent._routes_to_cloud("claude-sonnet-4-6") is True
        assert BaseStreamingAgent._routes_to_cloud("frontier") is True


class TestInjectThinkingSettings:
    @patch("app.agents.base_agent.get_settings")
    def test_agent_cloud_gets_medium_effort(self, mock_settings):
        mock_settings.return_value.cloud_routed_aliases = (
            "agent,default,chat,research,frontier,frontier-fast,fallback"
        )
        mock_settings.return_value.llm_backend = "cloud"

        agent = _bare_agent("agent")
        settings: dict = {}
        agent._inject_thinking_settings(settings)

        assert settings["reasoning_effort"] == "medium"
        assert "max_thinking_tokens" not in settings.get("extra_body", {})

    @patch("app.agents.base_agent.get_settings")
    def test_research_cloud_gets_high_effort(self, mock_settings):
        mock_settings.return_value.cloud_routed_aliases = (
            "agent,default,chat,research,frontier,frontier-fast,fallback"
        )
        mock_settings.return_value.llm_backend = "cloud"

        agent = _bare_agent("research")
        settings: dict = {}
        agent._inject_thinking_settings(settings)

        assert settings["reasoning_effort"] == "high"
        assert "max_thinking_tokens" not in settings.get("extra_body", {})

    @patch("app.agents.base_agent.get_settings")
    def test_default_cloud_gets_medium_effort(self, mock_settings):
        mock_settings.return_value.cloud_routed_aliases = (
            "agent,default,chat,research,frontier,frontier-fast,fallback"
        )
        mock_settings.return_value.llm_backend = "cloud"

        agent = _bare_agent("default")
        settings: dict = {}
        agent._inject_thinking_settings(settings)

        assert settings["reasoning_effort"] == "medium"

    @patch("app.agents.base_agent.get_settings")
    def test_local_mlx_sets_thinking_budget(self, mock_settings):
        mock_settings.return_value.cloud_routed_aliases = "chat,research,frontier"
        mock_settings.return_value.llm_backend = "mlx"

        agent = _bare_agent("agent")
        settings: dict = {}
        agent._inject_thinking_settings(settings)

        assert "reasoning_effort" not in settings
        assert settings["extra_body"]["max_thinking_tokens"] == 1024

    @patch("app.agents.base_agent.get_settings")
    def test_local_chat_disables_thinking(self, mock_settings):
        # chat not cloud-routed in this override → local disable path
        mock_settings.return_value.cloud_routed_aliases = "frontier"
        mock_settings.return_value.llm_backend = "mlx"

        agent = _bare_agent("chat")
        settings: dict = {}
        agent._inject_thinking_settings(settings)

        assert settings["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
        assert "reasoning_effort" not in settings
        assert "max_thinking_tokens" not in settings.get("extra_body", {})
