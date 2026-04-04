"""Tests for the PageIndex agentic retrieval engine."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import retrieval.pageindex_engine as engine_module
from retrieval.fetcher import RetrievedChunk
from retrieval.pageindex_engine import (
    PageIndexEngineResult,
    _AgentState,
    _assistant_message_from_response,
    _dispatch_tool,
    _execute_get_node_content,
    _extract_tool_calls,
    run_pageindex_retrieval,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dict_response(content: str | None, tool_calls: list | None = None) -> dict:
    """Build a minimal dict-format response like the OpenAI API would return."""
    return {
        "choices": [
            {
                "message": {"content": content, "tool_calls": tool_calls},
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ]
    }


def _sdk_response(content: str | None, tool_calls=None):
    """Build a SimpleNamespace that mimics the OpenAI SDK response shape."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


def _minimal_storage(tree: dict | None = None):
    """Return a storage stub that serves a single minimal tree."""
    base_tree = tree or {"nodes": [{"node_id": "0001", "title": "Section"}]}

    def load_doc_tree(doc_id: str) -> dict:
        return base_tree

    def load_doc_source_path(doc_id: str) -> str:
        return "/fake/path.md"

    return SimpleNamespace(load_doc_tree=load_doc_tree, load_doc_source_path=load_doc_source_path)


# ── _extract_tool_calls ───────────────────────────────────────────────────────

def test_extract_tool_calls_from_dict_with_tools() -> None:
    """Dict response carrying tool_calls should have them extracted."""
    tc = {"id": "call_1", "function": {"name": "get_document_structure", "arguments": "{}"}}
    response = _dict_response(None, tool_calls=[tc])
    result = _extract_tool_calls(response)
    assert len(result) == 1
    assert result[0]["function"]["name"] == "get_document_structure"


def test_extract_tool_calls_from_dict_no_tools() -> None:
    """Dict response with no tool_calls should return an empty list."""
    response = _dict_response("The answer.")
    assert _extract_tool_calls(response) == []


def test_extract_tool_calls_from_dict_empty_choices() -> None:
    """A dict with an empty choices list should return an empty list."""
    assert _extract_tool_calls({"choices": []}) == []


def test_extract_tool_calls_from_sdk_object_with_tools() -> None:
    """SDK-object response carrying tool_calls should have them extracted."""
    tc = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_node_content", arguments='{"doc_id":"d","node_id":"n"}'),
    )
    response = _sdk_response(None, tool_calls=[tc])
    result = _extract_tool_calls(response)
    assert len(result) == 1
    assert result[0].function.name == "get_node_content"


def test_extract_tool_calls_from_sdk_object_no_tools() -> None:
    """SDK object with no tool_calls should return an empty list."""
    response = _sdk_response("The answer.")
    assert _extract_tool_calls(response) == []


# ── _assistant_message_from_response ─────────────────────────────────────────

def test_assistant_message_from_dict_no_tools() -> None:
    """A dict response with no tool calls should produce a clean assistant turn."""
    msg = _assistant_message_from_response(_dict_response("Hello"))
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hello"
    assert msg.get("tool_calls") is None


def test_assistant_message_from_dict_with_tools() -> None:
    """A dict response with tool calls should include the tool_calls list."""
    tc = {"id": "c1", "function": {"name": "get_document_structure", "arguments": "{}"}}
    msg = _assistant_message_from_response(_dict_response(None, tool_calls=[tc]))
    assert msg["tool_calls"] is not None
    assert msg["tool_calls"][0]["function"]["name"] == "get_document_structure"


def test_assistant_message_from_sdk_object() -> None:
    """SDK-object responses should produce the same dict shape."""
    msg = _assistant_message_from_response(_sdk_response("Hi there"))
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hi there"


# ── _dispatch_tool ────────────────────────────────────────────────────────────

def test_dispatch_tool_unknown_name_returns_error_json() -> None:
    """An unrecognised tool name should return an error JSON, not raise."""
    state = _AgentState()
    result = _dispatch_tool("does_not_exist", "{}", SimpleNamespace(), state)
    parsed = json.loads(result)
    assert "error" in parsed
    assert "does_not_exist" in parsed["error"]


def test_dispatch_tool_invalid_json_args_returns_error() -> None:
    """Malformed argument JSON should return an error JSON, not raise."""
    state = _AgentState()
    result = _dispatch_tool("get_document_structure", "not valid json", SimpleNamespace(), state)
    parsed = json.loads(result)
    assert "error" in parsed


def test_dispatch_tool_get_document_structure_missing_doc() -> None:
    """Requesting structure for a missing document should return an error JSON."""

    def load_doc_tree_raises(doc_id):
        raise FileNotFoundError(f"No tree for {doc_id}")

    storage = SimpleNamespace(load_doc_tree=load_doc_tree_raises)
    state = _AgentState()
    result = _dispatch_tool(
        "get_document_structure", '{"doc_id": "missing"}', storage, state
    )
    parsed = json.loads(result)
    assert "error" in parsed


# ── _execute_get_node_content / token budget ──────────────────────────────────

def test_content_budget_exceeded_returns_budget_exhausted(monkeypatch) -> None:
    """When accumulated tokens would exceed the budget the tool returns a soft stop."""
    big_chunk = RetrievedChunk(
        node_ref="doc1::0001",
        doc_id="doc1",
        node_id="0001",
        title="Section",
        start_index=0,
        end_index=5,
        text="Some content",
        estimated_tokens=5000,
    )
    monkeypatch.setattr(engine_module, "_build_retrieved_chunk", lambda *a: big_chunk)

    state = _AgentState(content_token_budget=100)  # tiny budget
    result = _execute_get_node_content("doc1", "0001", _minimal_storage(), state)
    parsed = json.loads(result)

    assert parsed["budget_exhausted"] is True
    assert state.content_tokens == 0  # budget was not consumed


def test_content_budget_tracks_accumulated_tokens(monkeypatch) -> None:
    """Token count on state should grow with each successful retrieval."""
    chunk = RetrievedChunk(
        node_ref="doc1::0001",
        doc_id="doc1",
        node_id="0001",
        title="Section",
        start_index=0,
        end_index=5,
        text="Some content",
        estimated_tokens=40,
    )
    monkeypatch.setattr(engine_module, "_build_retrieved_chunk", lambda *a: chunk)

    state = _AgentState(content_token_budget=100)
    result = _execute_get_node_content("doc1", "0001", _minimal_storage(), state)

    assert result == "Some content"
    assert state.content_tokens == 40


# ── run_pageindex_retrieval ───────────────────────────────────────────────────

def _patch_engine(monkeypatch, responses: list[dict]) -> None:
    """Wire fake LLM responses into the engine under test."""
    it = iter(responses)

    async def fake_create_chat_completion(**kwargs):
        return next(it)

    monkeypatch.setattr(engine_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(engine_module, "create_chat_completion_async", fake_create_chat_completion)
    monkeypatch.setattr(engine_module, "get_default_model", lambda: "test-model")


def test_run_pageindex_retrieval_direct_answer(monkeypatch) -> None:
    """When the first response has no tool calls the answer is returned immediately."""
    _patch_engine(monkeypatch, [_dict_response("The direct answer.")])

    storage = SimpleNamespace(load_doc_tree=lambda d: {}, load_doc_source_path=lambda d: "")
    master_tree_store = SimpleNamespace(get_node=lambda d: None)


    answer, accessed_nodes, combined_context, engine_result = asyncio.run(
        run_pageindex_retrieval(
            user_query="What is X?",
            selected_doc_ids=["doc1"],
            master_tree_store=master_tree_store,
            storage=storage,

        )
    )

    assert answer == "The direct answer."
    assert accessed_nodes == []
    assert combined_context == ""
    assert engine_result.tool_calls_made == 0


def test_run_pageindex_retrieval_tool_call_then_answer(monkeypatch) -> None:
    """One tool call followed by a final answer should be handled correctly."""
    tc = {"id": "c1", "function": {"name": "get_document_structure", "arguments": '{"doc_id": "doc1"}'}}
    responses = [
        _dict_response(None, tool_calls=[tc]),
        _dict_response("Final answer after tool use."),
    ]
    _patch_engine(monkeypatch, responses)

    def load_doc_tree_raises(doc_id):
        raise FileNotFoundError("no tree")

    storage = SimpleNamespace(
        load_doc_tree=load_doc_tree_raises,
        load_doc_source_path=lambda d: "",
    )
    master_tree_store = SimpleNamespace(get_node=lambda d: None)


    answer, accessed_nodes, combined_context, engine_result = asyncio.run(
        run_pageindex_retrieval(
            user_query="Tell me about doc1.",
            selected_doc_ids=["doc1"],
            master_tree_store=master_tree_store,
            storage=storage,

        )
    )

    assert answer == "Final answer after tool use."
    assert accessed_nodes == []  # get_document_structure calls don't count as accessed
    assert engine_result.tool_calls_made == 1  # one get_document_structure call


def test_run_pageindex_retrieval_max_tool_calls_prompts_final_answer(monkeypatch) -> None:
    """When max_tool_calls is hit the engine issues one more call for a final answer."""
    tc = {"id": "c1", "function": {"name": "get_document_structure", "arguments": '{"doc_id": "doc1"}'}}
    # Every response keeps requesting a tool call, then one final answer response.
    tool_response = _dict_response(None, tool_calls=[tc])
    final_response = _dict_response("Best answer with limited context.")

    call_count = 0

    async def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        # After max_tool_calls (2 here) exhausted, return the final answer.
        if call_count > 2:
            return final_response
        return tool_response

    monkeypatch.setattr(engine_module, "get_async_client", lambda: SimpleNamespace())
    monkeypatch.setattr(engine_module, "create_chat_completion_async", fake_create)
    monkeypatch.setattr(engine_module, "get_default_model", lambda: "test-model")

    def load_doc_tree_raises(doc_id):
        raise FileNotFoundError("no tree")

    storage = SimpleNamespace(
        load_doc_tree=load_doc_tree_raises,
        load_doc_source_path=lambda d: "",
    )
    master_tree_store = SimpleNamespace(get_node=lambda d: None)


    answer, _, _, engine_result = asyncio.run(
        run_pageindex_retrieval(
            user_query="What is X?",
            selected_doc_ids=["doc1"],
            master_tree_store=master_tree_store,
            storage=storage,

            max_tool_calls=2,
        )
    )

    assert answer == "Best answer with limited context."
    # max_tool_calls=2 loop + 1 forced final call = 3 total LLM calls
    assert call_count == 3
    assert engine_result.tool_budget_exhausted is True
    assert engine_result.tool_call_budget == 2


# ── PageIndexEngineResult ─────────────────────────────────────────────────────

def test_engine_result_fields_on_direct_answer(monkeypatch) -> None:
    """A direct-answer run should produce zeroed-out engine metrics."""
    _patch_engine(monkeypatch, [_dict_response("Direct.")])
    storage = SimpleNamespace(load_doc_tree=lambda d: {}, load_doc_source_path=lambda d: "")
    master_tree_store = SimpleNamespace(get_node=lambda d: None)

    _, _, _, er = asyncio.run(
        run_pageindex_retrieval(
            user_query="Q?",
            selected_doc_ids=["doc1"],
            master_tree_store=master_tree_store,
            storage=storage,
        )
    )

    assert isinstance(er, PageIndexEngineResult)
    assert er.tool_calls_made == 0
    assert er.tool_call_budget == 12  # default
    assert er.content_tokens_used == 0
    assert er.explored_docs == []
    assert er.tool_budget_exhausted is False
    assert er.content_budget_exhausted is False


def test_explored_docs_tracked_on_get_document_structure(monkeypatch) -> None:
    """Calling get_document_structure should add the doc to explored_docs."""
    tc = {"id": "c1", "function": {"name": "get_document_structure", "arguments": '{"doc_id": "doc1"}'}}
    responses = [_dict_response(None, tool_calls=[tc]), _dict_response("Done.")]
    _patch_engine(monkeypatch, responses)

    def load_raises(doc_id):
        raise FileNotFoundError("no tree")

    storage = SimpleNamespace(load_doc_tree=load_raises, load_doc_source_path=lambda d: "")
    master_tree_store = SimpleNamespace(get_node=lambda d: None)

    _, _, _, er = asyncio.run(
        run_pageindex_retrieval(
            user_query="Q?",
            selected_doc_ids=["doc1"],
            master_tree_store=master_tree_store,
            storage=storage,
        )
    )

    assert "doc1" in er.explored_docs
    assert er.tool_calls_made == 1


def test_explored_docs_deduped(monkeypatch) -> None:
    """The same doc_id appearing in two get_document_structure calls counts once."""
    tc = {"id": "c1", "function": {"name": "get_document_structure", "arguments": '{"doc_id": "doc1"}'}}
    responses = [
        _dict_response(None, tool_calls=[tc]),
        _dict_response(None, tool_calls=[tc]),
        _dict_response("Done."),
    ]
    _patch_engine(monkeypatch, responses)

    def load_raises(doc_id):
        raise FileNotFoundError("no tree")

    storage = SimpleNamespace(load_doc_tree=load_raises, load_doc_source_path=lambda d: "")
    master_tree_store = SimpleNamespace(get_node=lambda d: None)

    _, _, _, er = asyncio.run(
        run_pageindex_retrieval(
            user_query="Q?",
            selected_doc_ids=["doc1"],
            master_tree_store=master_tree_store,
            storage=storage,
        )
    )

    assert er.explored_docs == ["doc1"]


def test_content_budget_exhausted_flag_set(monkeypatch) -> None:
    """Hitting the content token budget should set content_budget_exhausted."""
    big_chunk = RetrievedChunk(
        node_ref="doc1::0001", doc_id="doc1", node_id="0001",
        title="S", start_index=0, end_index=5, text="T", estimated_tokens=9999,
    )
    monkeypatch.setattr(engine_module, "_build_retrieved_chunk", lambda *a: big_chunk)

    tc = {"id": "c1", "function": {"name": "get_node_content", "arguments": '{"doc_id":"doc1","node_id":"0001"}'}}
    responses = [_dict_response(None, tool_calls=[tc]), _dict_response("Answer.")]
    _patch_engine(monkeypatch, responses)

    storage = SimpleNamespace(
        load_doc_tree=lambda d: {"nodes": [{"node_id": "0001", "title": "S"}]},
        load_doc_source_path=lambda d: "/fake/path.md",
    )
    master_tree_store = SimpleNamespace(get_node=lambda d: None)

    _, _, _, er = asyncio.run(
        run_pageindex_retrieval(
            user_query="Q?",
            selected_doc_ids=["doc1"],
            master_tree_store=master_tree_store,
            storage=storage,
            model_max_tokens=100,   # very small → tiny content_token_budget
        )
    )

    assert er.content_budget_exhausted is True


# ── Retrieval mode plumbing in query_engine ───────────────────────────────────

def test_dispatch_tool_tracks_explored_docs() -> None:
    """_dispatch_tool should add doc_id to state.explored_docs on get_document_structure."""
    state = _AgentState()
    storage = SimpleNamespace(load_doc_tree=lambda d: (_ for _ in ()).throw(FileNotFoundError("no tree")))

    def load_raises(doc_id):
        raise FileNotFoundError("no tree")

    storage2 = SimpleNamespace(load_doc_tree=load_raises)
    _dispatch_tool("get_document_structure", '{"doc_id":"alpha"}', storage2, state)

    assert "alpha" in state.explored_docs


def test_dispatch_tool_explored_docs_not_duplicated() -> None:
    """Calling get_document_structure twice for the same doc should only add once."""
    state = _AgentState()

    def load_raises(doc_id):
        raise FileNotFoundError("no tree")

    storage = SimpleNamespace(load_doc_tree=load_raises)
    _dispatch_tool("get_document_structure", '{"doc_id":"alpha"}', storage, state)
    _dispatch_tool("get_document_structure", '{"doc_id":"alpha"}', storage, state)

    assert state.explored_docs.count("alpha") == 1
