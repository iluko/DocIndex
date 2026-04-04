"""PageIndex-style agentic retrieval engine.

When RETRIEVAL_MODE=pageindex this module replaces the deterministic
navigator + fetcher pipeline with a self-directed tool-use loop:

  1. The LLM receives all selected documents' structures upfront.
  2. It calls ``get_document_structure`` / ``get_node_content`` as many times
     as it needs (up to ``max_tool_calls``).
  3. When it stops calling tools it emits the final answer.

Contrast with hybrid mode:
  - Hybrid: fixed 3 LLM calls (router, navigator×N, answer), fully traceable.
  - PageIndex: variable calls, LLM decides what to read next, can self-correct.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from dotenv import load_dotenv

from master_tree.master_tree import MasterTreeStore
from retrieval.fetcher import _build_retrieved_chunk, _split_node_ref
from storage.store import DocumentStore
from utils import (
    ConversationContext,
    collect_node_ids,
    create_chat_completion_async,
    extract_llm_text,
    get_async_client,
    get_default_model,
    iter_tree_nodes,
    render_conversation_context,
    strip_text_fields,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ── Tool schemas exposed to the LLM ──────────────────────────────────────────

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_document_structure",
            "description": (
                "Return the hierarchical section tree for a document. "
                "Each node shows its node_id, title, page range, and a short summary. "
                "Call this first to identify which sections are worth reading."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {
                        "type": "string",
                        "description": "The document ID to inspect.",
                    }
                },
                "required": ["doc_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_content",
            "description": (
                "Return the full text content of one section node. "
                "Use node_ids you obtained from get_document_structure. "
                "Prefer tight, specific nodes over broad top-level ones."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_id": {
                        "type": "string",
                        "description": "The document ID.",
                    },
                    "node_id": {
                        "type": "string",
                        "description": "The node_id from the document structure.",
                    },
                },
                "required": ["doc_id", "node_id"],
            },
        },
    },
]

# ── Internal helpers ──────────────────────────────────────────────────────────


@dataclass
class PageIndexEngineResult:
    """Exploration metrics from one PageIndex agentic retrieval run.

    Returned alongside the answer and accessed nodes so callers can populate
    query traces without coupling to internal ``_AgentState``.
    """

    tool_calls_made: int
    tool_call_budget: int
    content_tokens_used: int
    content_token_budget: int
    explored_docs: list[str]
    tool_budget_exhausted: bool
    content_budget_exhausted: bool


@dataclass
class _AgentState:
    """Mutable state threaded through the tool-use loop."""

    messages: list[dict] = field(default_factory=list)
    retrieved_texts: list[str] = field(default_factory=list)
    accessed_nodes: list[str] = field(default_factory=list)
    explored_docs: list[str] = field(default_factory=list)
    tool_calls_made: int = 0
    content_tokens: int = 0
    content_token_budget: int = 60000  # overridden at initialisation time
    content_budget_exhausted: bool = False


def _doc_summary_block(
    selected_doc_ids: list[str],
    master_tree_store: MasterTreeStore,
) -> str:
    """Build a short per-document inventory for the system prompt."""
    lines: list[str] = []
    for doc_id in selected_doc_ids:
        node = master_tree_store.get_node(doc_id)
        if node is None:
            lines.append(f"• {doc_id}")
            continue
        lines.append(
            f'• {doc_id} — "{node.doc_title}"\n'
            f"  {node.doc_summary[:180].strip()}"
        )
    return "\n".join(lines)


def _execute_get_document_structure(doc_id: str, storage: DocumentStore) -> str:
    """Tool handler: return tree structure JSON with text fields stripped."""
    try:
        tree = storage.load_doc_tree(doc_id)
    except FileNotFoundError:
        return json.dumps({"error": f"Document '{doc_id}' not found."})

    # Emit a flat readable list identical to what the navigator sees.
    lines: list[str] = []
    for node in iter_tree_nodes(tree):
        node_id = node.get("node_id", "")
        title = node.get("title", "")
        start = node.get("start_index", "")
        end = node.get("end_index", "")
        summary = node.get("summary") or node.get("prefix_summary") or ""

        page_info = f" [pages {start}–{end}]" if start and end else ""
        lines.append(f'Node {node_id}: "{title}"{page_info}')
        if summary:
            preview = summary.strip()[:220]
            if len(summary.strip()) > 220:
                preview += "…"
            lines.append(f"  → {preview}")

    return "\n".join(lines) if lines else json.dumps({"error": "Empty tree."})


def _execute_get_node_content(
    doc_id: str,
    node_id: str,
    storage: DocumentStore,
    state: _AgentState,
) -> str:
    """Tool handler: return raw text for one tree node, subject to a token budget."""
    try:
        tree = storage.load_doc_tree(doc_id)
        valid = collect_node_ids(tree)
        if node_id not in valid:
            return json.dumps(
                {"error": f"node_id '{node_id}' not found in '{doc_id}'."}
            )
        file_path = storage.load_doc_source_path(doc_id)
        node_ref = f"{doc_id}::{node_id}"
        chunk = _build_retrieved_chunk(node_ref, tree, file_path)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        return json.dumps({"error": str(exc)})

    if state.content_tokens + chunk.estimated_tokens > state.content_token_budget:
        state.content_budget_exhausted = True
        return json.dumps({
            "budget_exhausted": True,
            "message": (
                "Content token budget reached. "
                "Answer based on what you have already retrieved."
            ),
        })

    state.content_tokens += chunk.estimated_tokens
    return chunk.text


def _dispatch_tool(name: str, raw_args: str, storage: DocumentStore, state: _AgentState) -> str:
    """Parse tool arguments and route to the correct handler."""
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid tool arguments JSON."})

    if name == "get_document_structure":
        doc_id = args.get("doc_id", "")
        if doc_id and doc_id not in state.explored_docs:
            state.explored_docs.append(doc_id)
        return _execute_get_document_structure(doc_id, storage)

    if name == "get_node_content":
        doc_id = args.get("doc_id", "")
        node_id = args.get("node_id", "")
        return _execute_get_node_content(doc_id, node_id, storage, state)

    return json.dumps({"error": f"Unknown tool '{name}'."})


def _extract_tool_calls(response: object) -> list[object]:
    """Pull tool_calls out of either an SDK object or dict response."""
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if not choices:
            return []
        return choices[0].get("message", {}).get("tool_calls") or []

    choices = getattr(response, "choices", None)
    if not choices:
        return []
    message = getattr(choices[0], "message", None)
    if message is None:
        return []
    return getattr(message, "tool_calls", None) or []


def _assistant_message_from_response(response: object) -> dict:
    """Build the assistant turn dict including any tool_calls for the history."""
    if isinstance(response, dict):
        choices = response.get("choices") or []
        msg = choices[0].get("message", {}) if choices else {}
        return {
            "role": "assistant",
            "content": msg.get("content"),
            "tool_calls": [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", "{}"),
                    },
                }
                for tc in (msg.get("tool_calls") or [])
            ] or None,
        }

    choices = getattr(response, "choices", None) or []
    msg = getattr(choices[0], "message", None) if choices else None
    raw_tool_calls = getattr(msg, "tool_calls", None) or []
    content = getattr(msg, "content", None)

    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": getattr(tc, "id", ""),
                "type": "function",
                "function": {
                    "name": getattr(tc.function, "name", ""),
                    "arguments": getattr(tc.function, "arguments", "{}"),
                },
            }
            for tc in raw_tool_calls
        ] or None,
    }


# ── Public entry point ────────────────────────────────────────────────────────


async def run_pageindex_retrieval(
    user_query: str,
    selected_doc_ids: list[str],
    master_tree_store: MasterTreeStore,
    storage: DocumentStore,
    model: str | None = None,
    conversation_context: ConversationContext = None,
    reasoning_effort: str | None = None,
    max_tool_calls: int = 12,
    model_max_tokens: int = 100000,
) -> tuple[str, list[str], str, PageIndexEngineResult]:
    """Run PageIndex-style agentic retrieval for the pre-selected documents.

    Returns
    -------
    answer : str
        The final LLM-generated answer.
    accessed_nodes : list[str]
        Every ``doc_id::node_id`` the agent actually read.
    combined_context : str
        All node content the agent retrieved, concatenated.
    engine_result : PageIndexEngineResult
        Exploration metrics: tool call counts, budgets, explored docs, and
        whether any budget was exhausted during the run.
    """
    model = model or get_default_model()
    client = get_async_client()
    doc_block = _doc_summary_block(selected_doc_ids, master_tree_store)
    conversation_block = render_conversation_context(conversation_context)

    system_prompt = f"""
You are a document Q&A assistant. You have access to the following documents:

{doc_block}

Use the tools to explore document structure and fetch section content before
answering. Strategy:
1. Call get_document_structure(doc_id) on each document to see its sections.
2. Call get_node_content(doc_id, node_id) for the sections most likely to
   contain the answer. Prefer specific, narrow sections over broad ones.
3. Fetch from multiple documents if the query spans more than one.
4. Once you have sufficient context, answer directly. Do not over-fetch.

Answer based only on what the tools return. If the retrieved content does not
contain enough information, say so clearly.
""".strip()

    state = _AgentState()
    state.content_token_budget = int(model_max_tokens * 0.7)
    state.messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{user_query}\n\n{conversation_block}".strip()},
    ]

    final_answer = ""
    tool_budget_exhausted = False

    for iteration in range(max_tool_calls):
        response = await create_chat_completion_async(
            client=client,
            model=model,
            messages=state.messages,
            temperature=0,
            reasoning_effort=reasoning_effort,
            tools=_TOOLS,
            tool_choice="auto",
        )

        tool_calls = _extract_tool_calls(response)

        # No tool calls → the LLM has decided to answer.
        if not tool_calls:
            final_answer = extract_llm_text(response)
            break

        # Append the assistant's turn (may include tool_calls key).
        assistant_turn = _assistant_message_from_response(response)
        # Drop None tool_calls key to keep messages list clean.
        if assistant_turn.get("tool_calls") is None:
            assistant_turn.pop("tool_calls", None)
        state.messages.append(assistant_turn)

        # Execute every tool call and add results back to the conversation.
        for tc in tool_calls:
            tc_id = getattr(tc, "id", "") or tc.get("id", "")
            if isinstance(tc, dict):
                name = tc.get("function", {}).get("name", "")
                raw_args = tc.get("function", {}).get("arguments", "{}")
            else:
                name = getattr(tc.function, "name", "")
                raw_args = getattr(tc.function, "arguments", "{}")

            result = _dispatch_tool(name, raw_args, storage, state)
            state.tool_calls_made += 1

            # Track which nodes were actually read.
            if name == "get_node_content":
                try:
                    args = json.loads(raw_args or "{}")
                    doc_id = args.get("doc_id", "")
                    node_id = args.get("node_id", "")
                    if doc_id and node_id:
                        node_ref = f"{doc_id}::{node_id}"
                        if node_ref not in state.accessed_nodes:
                            state.accessed_nodes.append(node_ref)
                            state.retrieved_texts.append(result)
                except (json.JSONDecodeError, AttributeError):
                    pass

            state.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result,
                }
            )

        logger.debug(
            "PageIndex agent iteration %d/%d — tool calls: %d total so far.",
            iteration + 1,
            max_tool_calls,
            state.tool_calls_made,
        )
    else:
        # Hit the cap — ask for a final answer with whatever was retrieved.
        tool_budget_exhausted = True
        logger.warning(
            "PageIndex agent hit max_tool_calls=%d for query '%s…'. "
            "Requesting final answer.",
            max_tool_calls,
            user_query[:60],
        )
        state.messages.append(
            {
                "role": "user",
                "content": (
                    "You have reached the tool call limit. "
                    "Provide your best answer now based on what you have retrieved."
                ),
            }
        )
        final_response = await create_chat_completion_async(
            client=client,
            model=model,
            messages=state.messages,
            temperature=0,
            reasoning_effort=reasoning_effort,
        )
        final_answer = extract_llm_text(final_response)

    combined_context = "\n\n".join(state.retrieved_texts)
    engine_result = PageIndexEngineResult(
        tool_calls_made=state.tool_calls_made,
        tool_call_budget=max_tool_calls,
        content_tokens_used=state.content_tokens,
        content_token_budget=state.content_token_budget,
        explored_docs=list(state.explored_docs),
        tool_budget_exhausted=tool_budget_exhausted,
        content_budget_exhausted=state.content_budget_exhausted,
    )
    return final_answer, state.accessed_nodes, combined_context, engine_result
