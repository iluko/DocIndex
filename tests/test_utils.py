"""Tests for shared utility helpers and runtime compatibility patches."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import utils


class _FakeResponse:
    """Minimal fake chat-completion response used by utility tests."""

    def __init__(self, content: str = "ok", finish_reason: str = "stop") -> None:
        """Create a minimal response object with the fields the code reads."""
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]


class _FakeSyncCompletions:
    """Fake synchronous completions endpoint that can fail on specific params."""

    def __init__(
        self,
        calls: list[dict],
        fail_once_on_temperature: bool,
        fail_once_on_reasoning_effort: bool = False,
    ) -> None:
        """Remember which simulated failures this fake endpoint should trigger."""
        self._calls = calls
        self._fail_once_on_temperature = fail_once_on_temperature
        self._fail_once_on_reasoning_effort = fail_once_on_reasoning_effort

    def create(self, **kwargs):
        """Record the call and optionally fail on selected unsupported params."""
        self._calls.append(dict(kwargs))
        if self._fail_once_on_temperature and "temperature" in kwargs:
            self._fail_once_on_temperature = False
            raise Exception(
                "Unsupported value: 'temperature' does not support 0 with this model. "
                "Only the default (1) value is supported."
            )
        if self._fail_once_on_reasoning_effort and "reasoning_effort" in kwargs:
            self._fail_once_on_reasoning_effort = False
            raise Exception("Unsupported parameter: 'reasoning_effort'")
        return _FakeResponse()


class _FakeAsyncCompletions:
    """Async version of the fake completions endpoint."""

    def __init__(
        self,
        calls: list[dict],
        fail_once_on_temperature: bool,
        fail_once_on_reasoning_effort: bool = False,
    ) -> None:
        """Remember which simulated failures this fake async endpoint should trigger."""
        self._calls = calls
        self._fail_once_on_temperature = fail_once_on_temperature
        self._fail_once_on_reasoning_effort = fail_once_on_reasoning_effort

    async def create(self, **kwargs):
        """Async version of the fake completions endpoint."""
        self._calls.append(dict(kwargs))
        if self._fail_once_on_temperature and "temperature" in kwargs:
            self._fail_once_on_temperature = False
            raise Exception(
                "Unsupported value: 'temperature' does not support 0 with this model. "
                "Only the default (1) value is supported."
            )
        if self._fail_once_on_reasoning_effort and "reasoning_effort" in kwargs:
            self._fail_once_on_reasoning_effort = False
            raise Exception("Unsupported parameter: 'reasoning_effort'")
        return _FakeResponse()


class _FakeSyncClient:
    """Fake sync client exposing the same nested `.chat.completions` shape."""

    def __init__(
        self,
        calls: list[dict],
        fail_once_on_temperature: bool = False,
        fail_once_on_reasoning_effort: bool = False,
    ) -> None:
        """Expose the same nested attribute shape as the real sync SDK client."""
        self.chat = SimpleNamespace(
            completions=_FakeSyncCompletions(
                calls,
                fail_once_on_temperature,
                fail_once_on_reasoning_effort,
            )
        )


class _FakeAsyncClient:
    """Fake async client that also supports `async with` blocks."""

    def __init__(
        self,
        calls: list[dict],
        fail_once_on_temperature: bool = False,
        fail_once_on_reasoning_effort: bool = False,
    ) -> None:
        """Expose the same nested attribute shape as the real async SDK client."""
        self.chat = SimpleNamespace(
            completions=_FakeAsyncCompletions(
                calls,
                fail_once_on_temperature,
                fail_once_on_reasoning_effort,
            )
        )

    async def __aenter__(self):
        """Support `async with` in the code under test."""
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Do not suppress exceptions raised inside the context block."""
        return False


def test_build_chat_completion_kwargs_omits_temperature_for_gpt5_prefix() -> None:
    """Known GPT-5-style names should omit explicit temperature immediately."""
    gpt5_kwargs = utils.build_chat_completion_kwargs(
        model="gpt-5.2-chat",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0,
    )
    assert "temperature" not in gpt5_kwargs

    gpt4_kwargs = utils.build_chat_completion_kwargs(
        model="gpt-4.1",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0,
    )
    assert gpt4_kwargs["temperature"] == 0


def test_create_chat_completion_retries_without_temperature_on_unsupported_error() -> None:
    """Unsupported-temperature errors should trigger one automatic retry."""
    calls: list[dict] = []
    client = _FakeSyncClient(calls, fail_once_on_temperature=True)

    response = utils.create_chat_completion(
        client=client,
        model="custom-azure-deployment",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0,
    )

    assert utils.extract_llm_text(response) == "ok"
    assert len(calls) == 2
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]


def test_create_chat_completion_retries_without_reasoning_effort_on_unsupported_error() -> None:
    """Unsupported reasoning-effort errors should also trigger one retry."""
    calls: list[dict] = []
    client = _FakeSyncClient(calls, fail_once_on_reasoning_effort=True)

    response = utils.create_chat_completion(
        client=client,
        model="gpt-4.1",
        messages=[{"role": "user", "content": "hello"}],
        reasoning_effort="high",
    )

    assert utils.extract_llm_text(response) == "ok"
    assert len(calls) == 2
    assert calls[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in calls[1]


def test_patch_pageindex_llm_helpers_uses_temperature_fallback(monkeypatch) -> None:
    """The PageIndex patch should inherit our LLM compatibility logic."""
    sync_calls: list[dict] = []
    async_calls: list[dict] = []

    monkeypatch.setattr(
        utils,
        "get_sync_client",
        lambda api_key=None: _FakeSyncClient(sync_calls, fail_once_on_temperature=True),
    )
    monkeypatch.setattr(
        utils,
        "get_async_client",
        lambda api_key=None: _FakeAsyncClient(async_calls, fail_once_on_temperature=True),
    )

    # Seed the namespace with the attributes the interface guard expects.
    pageindex_utils = SimpleNamespace(
        ChatGPT_API_with_finish_reason=None,
        ChatGPT_API=None,
        ChatGPT_API_async=None,
    )
    pageindex_module = SimpleNamespace(ChatGPT_API_async=None)

    utils.patch_pageindex_llm_helpers(pageindex_utils, pageindex_module)

    assert pageindex_utils.ChatGPT_API("custom-azure-deployment", "prompt") == "ok"
    assert "temperature" in sync_calls[0]
    assert "temperature" not in sync_calls[1]

    async_result = asyncio.run(
        pageindex_module.ChatGPT_API_async("custom-azure-deployment", "prompt")
    )
    assert async_result == "ok"
    assert "temperature" in async_calls[0]
    assert "temperature" not in async_calls[1]
    assert sync_calls[0]["reasoning_effort"] == utils.INGESTION_REASONING_EFFORT
    assert async_calls[0]["reasoning_effort"] == utils.INGESTION_REASONING_EFFORT


def test_patch_pageindex_progress_hooks_emits_summary_events() -> None:
    """The PageIndex progress patch should emit summary lifecycle events."""
    async def original_generate_node_summary(node, model=None):
        """Return a deterministic summary string for one node."""
        return f"summary for {node['title']}"

    async def original_generate_summaries_for_structure(structure, model=None):
        """Simulate PageIndex's batch summary flow by calling the patched helper."""
        summaries = await asyncio.gather(
            *[
                pageindex_utils.generate_node_summary(node, model=model)
                for node in pageindex_utils.structure_to_list(structure)
            ]
        )
        for node, summary in zip(pageindex_utils.structure_to_list(structure), summaries):
            node["summary"] = summary
        return structure

    async def original_md_get_node_summary(node, summary_token_threshold=200, model=None):
        """Return a deterministic markdown summary string for one node."""
        return f"md summary for {node['title']}"

    async def original_md_generate_summaries_for_structure(structure, summary_token_threshold, model=None):
        """Simulate markdown batch summary generation through the patched helper."""
        summaries = await asyncio.gather(
            *[
                page_index_md_module.get_node_summary(
                    node,
                    summary_token_threshold=summary_token_threshold,
                    model=model,
                )
                for node in pageindex_utils.structure_to_list(structure)
            ]
        )
        for node, summary in zip(pageindex_utils.structure_to_list(structure), summaries):
            node["summary"] = summary
        return structure

    pageindex_utils = SimpleNamespace(
        generate_node_summary=original_generate_node_summary,
        generate_summaries_for_structure=original_generate_summaries_for_structure,
        structure_to_list=lambda structure: structure["nodes"],
    )
    page_index_module = SimpleNamespace(
        generate_node_summary=original_generate_node_summary,
        generate_summaries_for_structure=original_generate_summaries_for_structure,
    )
    page_index_md_module = SimpleNamespace(
        get_node_summary=original_md_get_node_summary,
        generate_summaries_for_structure_md=original_md_generate_summaries_for_structure,
    )

    utils.patch_pageindex_progress_hooks(
        pageindex_utils,
        page_index_module,
        page_index_md_module,
    )

    events: list[dict] = []
    structure = {
        "nodes": [
            {"node_id": "0001", "title": "Intro"},
            {"node_id": "0002", "title": "Auth Flow"},
        ]
    }

    with utils.progress_context(events.append):
        asyncio.run(pageindex_utils.generate_summaries_for_structure(structure, model="test-model"))

    event_names = [event["event"] for event in events]
    assert event_names[0] == "pageindex_summary_batch_started"
    assert event_names[-1] == "pageindex_summary_batch_completed"
    assert event_names.count("pageindex_summary_node_started") == 2
    assert event_names.count("pageindex_summary_node_completed") == 2
    completed_events = [
        event for event in events if event["event"] == "pageindex_summary_node_completed"
    ]
    assert completed_events[0]["estimated_input_tokens"] > 0
    assert completed_events[0]["estimated_output_tokens"] > 0
    assert completed_events[-1]["total_input_tokens"] >= completed_events[0]["estimated_input_tokens"]
    assert completed_events[-1]["total_output_tokens"] >= completed_events[0]["estimated_output_tokens"]
