"""Tests for multimodal content in BaseStreamingAgent._call_structured_output."""

import json

import pytest

from app.agents.record_extractor_agent import RecordExtractorAgent

SCHEMA = {
    "name": "receipt_fields",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["merchant"],
        "properties": {"merchant": {"type": ["string", "null"]}},
    },
}


class _FakeMessage:
    content = json.dumps({"merchant": "Starbucks"})


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    choices = [_FakeChoice()]


class _FakeCompletions:
    def __init__(self, captured):
        self._captured = captured

    async def create(self, **kwargs):
        self._captured.append(kwargs)
        return _FakeResponse()


class _FakeChat:
    def __init__(self, captured):
        self.completions = _FakeCompletions(captured)


class _FakeAsyncOpenAI:
    captured = []

    def __init__(self, *args, **kwargs):
        self.chat = _FakeChat(_FakeAsyncOpenAI.captured)


@pytest.fixture(autouse=True)
def _patch_openai(monkeypatch):
    _FakeAsyncOpenAI.captured = []
    monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)
    yield


async def test_text_only_user_message_unchanged():
    agent = RecordExtractorAgent()
    result = await agent._call_structured_output(
        prompt="extract", system_prompt="sys", response_schema=SCHEMA, max_tokens=100
    )
    assert json.loads(result) == {"merchant": "Starbucks"}
    user_msg = _FakeAsyncOpenAI.captured[0]["messages"][-1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], str)
    assert user_msg["content"].endswith("extract")


async def test_images_become_content_parts():
    agent = RecordExtractorAgent()
    images = [{"media_type": "image/jpeg", "data": "aGVsbG8="}]
    await agent._call_structured_output(
        prompt="extract", system_prompt="sys", response_schema=SCHEMA,
        max_tokens=100, images=images,
    )
    user_msg = _FakeAsyncOpenAI.captured[0]["messages"][-1]
    content = user_msg["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"].endswith("extract")
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,aGVsbG8="},
    }


class _FlakyCompletions:
    """Returns schema-invalid JSON on call 1, valid JSON on call 2."""

    def __init__(self, captured):
        self._captured = captured
        self.calls = 0

    async def create(self, **kwargs):
        self._captured.append(kwargs)
        self.calls += 1

        class _Msg:
            content = json.dumps({"wrong_field": 1}) if self.calls == 1 else json.dumps({"merchant": "Starbucks"})

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


class _FlakyAsyncOpenAI:
    captured = []

    def __init__(self, *args, **kwargs):
        class _Chat:
            completions = _FlakyCompletions(_FlakyAsyncOpenAI.captured)

        self.chat = _Chat()


async def test_retry_resends_image_content(monkeypatch):
    _FlakyAsyncOpenAI.captured = []
    monkeypatch.setattr("openai.AsyncOpenAI", _FlakyAsyncOpenAI)
    agent = RecordExtractorAgent()
    images = [{"media_type": "image/jpeg", "data": "aGVsbG8="}]
    result = await agent._call_structured_output(
        prompt="extract", system_prompt="sys", response_schema=SCHEMA,
        max_tokens=100, images=images,
    )
    assert json.loads(result) == {"merchant": "Starbucks"}
    assert len(_FlakyAsyncOpenAI.captured) == 2
    retry_messages = _FlakyAsyncOpenAI.captured[1]["messages"]
    user_turns = [m for m in retry_messages if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(user_turns) == 1
    assert user_turns[0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,aGVsbG8="


async def test_malformed_image_entries_are_skipped():
    agent = RecordExtractorAgent()
    images = [
        42,
        {"data": "aGVsbG8="},
        {"media_type": "application/pdf", "data": "aGVsbG8="},
        {"media_type": "image/jpeg", "data": ""},
    ]
    await agent._call_structured_output(
        prompt="extract", system_prompt="sys", response_schema=SCHEMA,
        max_tokens=100, images=images,
    )
    user_msg = _FakeAsyncOpenAI.captured[0]["messages"][-1]
    assert isinstance(user_msg["content"], str)
