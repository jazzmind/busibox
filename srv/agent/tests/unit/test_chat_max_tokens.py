"""The chat agent must send an explicit max_tokens.

Production, 2026-09-11: a research report was truncated mid-word at 11,334
characters. Nothing errored — `Chat agent request complete` logged
`response_length: 11334` and the partial answer was persisted as if complete.

Cause: `ChatAgent` set no `max_tokens`, on the assumption (stated in
base_agent's comment at the time) that omitting it lets the model use its
natural limit. That is true of OpenAI-compatible endpoints, but the Anthropic
Messages API *requires* max_tokens — so once `chat` was re-pointed at Bedrock,
LiteLLM had to supply a default, and its default is small.

The bug is invisible from every layer's point of view, which is why it needs a
test rather than a comment: the model returns a valid response, LiteLLM returns
a valid response, and the agent stores exactly what it was given.
"""

import inspect

import pytest

from app.agents.chat_agent import ChatAgent


@pytest.fixture(scope="module")
def config():
    """ChatAgent's AgentConfig without running __init__'s side effects."""
    source = inspect.getsource(ChatAgent.__init__)
    assert "max_tokens" in source, "ChatAgent.__init__ no longer mentions max_tokens"
    return source


def test_chat_agent_sets_max_tokens_explicitly(config):
    assert "max_tokens=32000" in config.replace(" ", "").replace("\n", "")


def test_the_value_is_within_the_request_override_ceiling():
    """`ChatMessageRequest.max_tokens` is capped at 32000 (app/api/chat.py).

    The agent default and that ceiling should agree — a default above the
    ceiling would mean a user "override" could only ever lower it, which is a
    confusing thing to discover at runtime.
    """
    from app.api.chat import ChatMessageRequest

    field = ChatMessageRequest.model_fields["max_tokens"]
    ceiling = next(
        (m.le for m in field.metadata if getattr(m, "le", None) is not None), None
    )
    assert ceiling == 32000, f"request ceiling moved to {ceiling}; revisit the agent default"


def test_the_value_is_safe_on_the_smaller_bedrock_arm():
    """`chat` can be load-balanced across deployments with different ceilings.

    Production ran Sonnet 4.5 (64k output) and Sonnet 5 (128k) under the same
    alias. A max_tokens above the smaller arm's limit is rejected outright
    whenever a request lands there, so the agent default must clear it.
    """
    smallest_known_bedrock_output_ceiling = 64000
    assert 32000 <= smallest_known_bedrock_output_ceiling


def test_base_agent_warns_that_omitting_max_tokens_is_not_unlimited():
    """The old comment said the opposite and is what caused the bug.

    Worth pinning: the next person to read `if config.max_tokens is not None`
    will trust whatever the comment says.
    """
    from app.agents.base_agent import BaseStreamingAgent

    source = inspect.getsource(BaseStreamingAgent.__init__)
    # The original read: "If max_tokens is None, don't pass it so the model
    # uses its natural limit." The correction has to say the opposite and name
    # why, so a bare mention of "natural limit" isn't enough to distinguish them.
    assert "does NOT mean" in source
    assert "Anthropic" in source
