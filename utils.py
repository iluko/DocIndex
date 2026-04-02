"""Shared helpers for LLM calls, token counting, progress reporting, and patches.

This is the most cross-cutting module in the project. It contains:
- generic tree/JSON helpers
- provider/model configuration
- compatibility shims for Azure and GPT-5-family models
- runtime patches that adapt PageIndex without editing its source code
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Union

logger = logging.getLogger(__name__)

try:
    import tiktoken
except ImportError:  # pragma: no cover - optional in some test environments
    tiktoken = None

try:
    from openai import AsyncOpenAI, OpenAI
except ImportError:  # pragma: no cover - optional in some test environments
    AsyncOpenAI = None
    OpenAI = None


TEXT_FIELD_NAMES = {"node_text", "text"}
_DOC_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
ConversationContext = Union[str, list[dict[str, Any]], None]


def atomic_write_text(path: Path, content: str) -> None:
    """Write *content* to *path* atomically.

    Writes to a sibling ``.tmp`` file first, then calls ``os.replace()``
    (POSIX ``rename(2)``). Readers always see either the old complete file or
    the new complete file — never a half-written one.  If the write itself
    fails the temp file is cleaned up and the exception re-raised.
    """
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
TOKENIZER_FALLBACK_PREFIXES = {
    "gpt-5": "o200k_base",
    "o4": "o200k_base",
}
TEMPERATURE_UNSUPPORTED_PREFIXES = ("gpt-5", "o1", "o3", "o4")
INGESTION_REASONING_EFFORT = "high"
REASONING_EFFORT_OPTIONS = ("minimal", "low", "medium", "high")
MASTER_TOP_SECTIONS_DEFAULT = 4
MASTER_TOP_SECTIONS_MAX = 12
_PROGRESS_STATE = threading.local()
_LLM_USAGE_TRACKER: contextvars.ContextVar[LLMUsageTotals | None] = contextvars.ContextVar(
    "llm_usage_tracker",
    default=None,
)


@dataclass(frozen=True)
class LLMConfig:
    """Normalized LLM connection settings regardless of provider."""

    provider: str
    api_key: str
    base_url: str | None
    default_model: str


@dataclass
class LLMUsageTotals:
    """Aggregated token usage across one higher-level workflow.

    We track prompt/completion/total tokens plus how many LLM calls contributed
    to the totals. When an SDK response omits usage metadata we fall back to
    estimates, so callers can surface that caveat to users.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    estimated_calls: int = 0


def validate_doc_id(doc_id: str) -> None:
    """Raise ValueError if doc_id contains characters unsafe for filesystem paths.

    Only letters, digits, underscores, and hyphens are allowed. The first
    character must be a letter or digit. This prevents path-traversal attacks
    (../, absolute paths, null bytes) when doc_id is used to build file paths.
    """
    if not _DOC_ID_RE.match(doc_id):
        raise ValueError(
            f"Invalid doc_id '{doc_id}'. "
            "Use only letters, digits, underscores, and hyphens, "
            "starting with a letter or digit (e.g. 'auth_spec_v2')."
        )


def clamp_master_top_sections_target(value: int) -> int:
    """Clamp the master-node top-section target into the supported range."""
    return max(1, min(MASTER_TOP_SECTIONS_MAX, int(value)))


def get_master_top_sections_target(value: int | None = None) -> int:
    """Resolve the ingestion-time top-section target from override or env.

    The default target is 4, which implies a prompt range of 3-5 sections.
    Larger values widen routing coverage but also make the master-tree prompt
    heavier at query time.
    """
    if value is not None:
        return clamp_master_top_sections_target(value)

    raw = os.getenv("MASTER_TOP_SECTIONS_TARGET", "").strip()
    if not raw:
        return MASTER_TOP_SECTIONS_DEFAULT
    try:
        return clamp_master_top_sections_target(int(raw))
    except ValueError:
        logger.warning(
            "Invalid MASTER_TOP_SECTIONS_TARGET=%r; falling back to default %d.",
            raw,
            MASTER_TOP_SECTIONS_DEFAULT,
        )
        return MASTER_TOP_SECTIONS_DEFAULT


def get_master_top_sections_range(target: int | None = None) -> tuple[int, int]:
    """Return the inclusive `(min, max)` range used in the master-node prompt."""
    resolved = get_master_top_sections_target(target)
    return max(1, resolved - 1), min(MASTER_TOP_SECTIONS_MAX, resolved + 1)


def strip_text_fields(value: Any) -> Any:
    """Recursively remove full-text fields from tree payloads."""
    if isinstance(value, dict):
        return {
            key: strip_text_fields(item)
            for key, item in value.items()
            if key not in TEXT_FIELD_NAMES
        }
    if isinstance(value, list):
        return [strip_text_fields(item) for item in value]
    return value


def ensure_tiktoken_model_aliases() -> None:
    """Teach `tiktoken` about newer model prefixes when the local package lags."""
    if tiktoken is None:  # pragma: no cover - optional dependency
        return

    model_module = getattr(tiktoken, "model", None)
    prefix_map = getattr(model_module, "MODEL_PREFIX_TO_ENCODING", None)
    if not isinstance(prefix_map, dict):
        return

    for model_prefix, encoding_name in TOKENIZER_FALLBACK_PREFIXES.items():
        prefix_map.setdefault(model_prefix, encoding_name)


def render_conversation_context(context: ConversationContext) -> str:
    """Normalize caller-supplied conversation context into prompt-ready text.

    The reusable core accepts either:
    - a raw string prepared by the caller, or
    - a list of chat turns for convenience.

    This keeps the library conversation-aware without making it the owner of
    any session lifecycle.
    """
    if context is None:
        return ""

    if isinstance(context, str):
        return context.strip()

    lines = ["Prior conversation context:"]
    for turn in context[-6:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {turn.get('content', '')}")
    return "\n".join(lines)


def format_chat_history(history: list[dict] | None) -> str:
    """Backward-compatible wrapper for callers still using chat-turn lists."""
    return render_conversation_context(history)


def iter_tree_nodes(tree: Any) -> Iterator[dict]:
    """Yield every node in a nested PageIndex-style tree structure."""
    if isinstance(tree, dict):
        if "node_id" in tree:
            yield tree
        for value in tree.values():
            yield from iter_tree_nodes(value)
    elif isinstance(tree, list):
        for item in tree:
            yield from iter_tree_nodes(item)


def collect_node_ids(tree: Any) -> set[str]:
    """Collect all node ids from a tree into a set for fast validation."""
    return {
        str(node["node_id"])
        for node in iter_tree_nodes(tree)
        if "node_id" in node and node["node_id"] is not None
    }


def find_tree_node(tree: Any, node_id: str) -> dict | None:
    """Find one node by id anywhere inside a nested tree."""
    target = str(node_id)
    for node in iter_tree_nodes(tree):
        if str(node.get("node_id")) == target:
            return node
    return None


def find_parent_node(tree: Any, target_node_id: str) -> dict | None:
    """Return the direct parent node of ``target_node_id`` in a nested tree.

    Traverses depth-first. Returns ``None`` if the node is a root-level node
    or if ``target_node_id`` is not present in the tree.
    """
    target = str(target_node_id)

    def _search(nodes: list) -> dict | None:
        for node in nodes:
            children: list = node.get("nodes") or []
            for child in children:
                if str(child.get("node_id")) == target:
                    return node
            found = _search(children)
            if found is not None:
                return found
        return None

    # PageIndex trees use either "structure" (top-level JSON) or "nodes" as the
    # root collection key depending on how they were serialised.
    root: list = []
    if isinstance(tree, dict):
        root = tree.get("structure") or tree.get("nodes") or []
    elif isinstance(tree, list):
        root = tree

    return _search(root)


def get_domain_name() -> str | None:
    """Return the user-configured domain name for answer prompt personalisation.

    When set, the answer LLM is introduced as an expert assistant for that
    domain (e.g. "You are an expert assistant for Acme Legal Knowledge Base.").
    When unset or blank, a generic introduction is used so the system works
    out-of-the-box without any configuration.
    """
    return os.getenv("DOMAIN_NAME", "").strip() or None


def get_navigator_verification_enabled() -> bool:
    """Return whether the post-navigation self-correction pass is active.

    Set ``NAVIGATOR_VERIFICATION=true`` in the environment to enable.
    Disabled by default because it adds one extra LLM call per selected document.
    """
    return os.getenv("NAVIGATOR_VERIFICATION", "false").strip().lower() in {
        "true", "1", "yes"
    }


def parse_json_response(content: str) -> Any:
    """Parse JSON from plain text, including common LLM markdown wrappers.

    Junior note:
    LLMs often wrap JSON in ```json fences or add a short preamble. This helper
    strips those common extras before handing the string to `json.loads(...)`.
    """
    text = content.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    if text and text[0] not in "[{":
        start_positions = [pos for pos in (text.find("{"), text.find("[")) if pos >= 0]
        if start_positions:
            start = min(start_positions)
            if text[start] == "{":
                end = text.rfind("}")
            else:
                end = text.rfind("]")
            if end >= start:
                text = text[start : end + 1]

    return json.loads(text)


def compact_json(data: Any) -> str:
    """Serialize JSON without extra whitespace to save prompt tokens."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def get_progress_callback() -> Callable[[dict[str, Any]], None] | None:
    """Return the current thread-local progress callback, if one is active."""
    return getattr(_PROGRESS_STATE, "callback", None)


def emit_progress(event: str, message: str, **payload: Any) -> None:
    """Send one structured progress event to the active callback."""
    callback = get_progress_callback()
    if callback is None:
        return
    callback(
        {
            "event": event,
            "message": message,
            **payload,
        }
    )


@contextmanager
def progress_context(callback: Callable[[dict[str, Any]], None] | None):
    """Temporarily install a thread-local progress callback.

    Junior note:
    `@contextmanager` lets us write setup/teardown logic around a `with` block
    without defining a full class.
    """
    previous = get_progress_callback()
    _PROGRESS_STATE.callback = callback
    try:
        yield
    finally:
        if previous is None:
            if hasattr(_PROGRESS_STATE, "callback"):
                delattr(_PROGRESS_STATE, "callback")
        else:
            _PROGRESS_STATE.callback = previous


def extract_llm_text(response: Any) -> str:
    """Normalize several SDK/JSON response shapes into plain text."""
    if isinstance(response, str):
        return response.strip()

    if isinstance(response, dict):
        if "choices" in response and response["choices"]:
            message = response["choices"][0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, list):
                return "".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                ).strip()
            return str(content or "").strip()

        if "output_text" in response:
            return str(response.get("output_text", "")).strip()

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text.strip()

    choices = getattr(response, "choices", None)
    if choices:
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        if message is not None:
            content = getattr(message, "content", "")
            if isinstance(content, list):
                return "".join(
                    getattr(item, "text", str(item)) if not isinstance(item, dict) else item.get("text", "")
                    for item in content
                ).strip()
            return str(content or "").strip()

    raise TypeError(
        f"Unsupported LLM response type: {type(response).__name__}. "
        "Expected a string, dict, or SDK response object with text content."
    )


def extract_finish_reason(response: Any) -> str | None:
    """Read the completion finish reason from either dict or SDK objects."""
    if isinstance(response, dict):
        choices = response.get("choices") or []
        if choices:
            return choices[0].get("finish_reason")
        return None

    choices = getattr(response, "choices", None)
    if choices:
        return getattr(choices[0], "finish_reason", None)

    return None


def extract_usage(response: Any) -> tuple[int, int, int] | None:
    """Extract ``(prompt_tokens, completion_tokens, total_tokens)`` when present.

    Supports plain dict payloads plus SDK objects whose ``usage`` field exposes
    either attributes or a dict-like shape.
    """
    usage = None
    if isinstance(response, dict):
        usage = response.get("usage")
    else:
        usage = getattr(response, "usage", None)

    if usage is None:
        return None

    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
    else:
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)

    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None

    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    total = int(total_tokens or (prompt + completion))
    return prompt, completion, total


def _message_content_to_text(content: Any) -> str:
    """Flatten chat-message content into plain text for coarse token estimates."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(getattr(item, "text", item)))
        return "".join(parts)
    return str(content or "")


def estimate_message_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate prompt tokens for a chat completion from its messages.

    This is intentionally approximate. We add a small fixed overhead per
    message so totals are not unrealistically low when exact provider usage is
    unavailable.
    """
    total = 0
    for message in messages:
        total += 4  # coarse per-message framing overhead
        total += estimate_tokens(str(message.get("role", "")))
        total += estimate_tokens(_message_content_to_text(message.get("content", "")))
    return total + 2  # coarse assistant priming overhead


def get_llm_usage_tracker() -> LLMUsageTotals | None:
    """Return the active query-scoped usage accumulator, if one is installed."""
    return _LLM_USAGE_TRACKER.get()


@contextmanager
def llm_usage_context() -> Iterator[LLMUsageTotals]:
    """Collect token usage across nested LLM calls within a workflow."""
    tracker = LLMUsageTotals()
    token = _LLM_USAGE_TRACKER.set(tracker)
    try:
        yield tracker
    finally:
        _LLM_USAGE_TRACKER.reset(token)


def record_llm_usage(
    *,
    messages: list[dict[str, Any]],
    response: Any | None = None,
    completion_text: str | None = None,
    usage: tuple[int, int, int] | None = None,
) -> None:
    """Add one chat-completion's usage into the active tracker, if any exists.

    Exact provider-reported usage is preferred. When absent, we fall back to a
    coarse estimate based on prompt text and completion text length.
    """
    tracker = get_llm_usage_tracker()
    if tracker is None:
        return

    measured = usage if usage is not None else extract_usage(response)
    estimated = measured is None
    if measured is None:
        prompt_tokens = estimate_message_tokens(messages)
        if completion_text is None:
            try:
                completion_text = extract_llm_text(response) if response is not None else ""
            except Exception:
                completion_text = ""
        completion_tokens = estimate_tokens(completion_text or "")
        total_tokens = prompt_tokens + completion_tokens
    else:
        prompt_tokens, completion_tokens, total_tokens = measured

    tracker.prompt_tokens += int(prompt_tokens)
    tracker.completion_tokens += int(completion_tokens)
    tracker.total_tokens += int(total_tokens)
    tracker.llm_calls += 1
    if estimated:
        tracker.estimated_calls += 1


def model_supports_explicit_temperature(model: str | None) -> bool:
    """Return whether a model family accepts an explicit `temperature` value."""
    if not model:
        return True

    normalized = model.strip().lower()
    return not normalized.startswith(TEMPERATURE_UNSUPPORTED_PREFIXES)


def build_chat_completion_kwargs(
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build the keyword arguments passed to `client.chat.completions.create(...)`."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        **kwargs,
    }
    if temperature is not None and model_supports_explicit_temperature(model):
        payload["temperature"] = temperature
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def is_temperature_unsupported_error(exc: Exception) -> bool:
    """Detect provider/model errors that specifically reject `temperature`."""
    message = str(exc).lower()
    return (
        "temperature" in message
        and (
            "does not support" in message
            or "unsupported_value" in message
            or "default (1) value is supported" in message
        )
    )


def is_reasoning_effort_unsupported_error(exc: Exception) -> bool:
    """Detect provider/model errors that specifically reject `reasoning_effort`."""
    message = str(exc).lower()
    return (
        "reasoning_effort" in message
        and (
            "unsupported" in message
            or "does not support" in message
            or "not supported" in message
            or "unknown parameter" in message
            or "extra inputs are not permitted" in message
        )
    )


def is_stream_options_unsupported_error(exc: Exception) -> bool:
    """Detect providers that reject ``stream_options`` on streaming requests."""
    message = str(exc).lower()
    return (
        "stream_options" in message
        and (
            "unsupported" in message
            or "unknown parameter" in message
            or "extra inputs are not permitted" in message
            or "not supported" in message
        )
    )


def normalize_reasoning_effort(value: str | None) -> str | None:
    """Convert UI/CLI input like `auto` or `High` into the API-ready value."""
    if value is None:
        return None

    normalized = value.strip().lower()
    if not normalized or normalized == "auto":
        return None
    if normalized not in REASONING_EFFORT_OPTIONS:
        raise ValueError(
            f"Unsupported reasoning effort '{value}'. "
            f"Use one of: auto, {', '.join(REASONING_EFFORT_OPTIONS)}."
        )
    return normalized


def _get_summary_tracker() -> dict[str, Any] | None:
    """Return the current thread-local PageIndex summary-progress tracker."""
    return getattr(_PROGRESS_STATE, "summary_tracker", None)


def _summary_label(node: Any) -> tuple[str | None, str]:
    """Extract a friendly `(node_id, title)` pair from a tree node."""
    if not isinstance(node, dict):
        return None, "Untitled section"
    node_id = node.get("node_id")
    title = str(node.get("title") or "Untitled section")
    return str(node_id) if node_id is not None else None, title


def _summary_prompt(node_text: str) -> str:
    """Rebuild PageIndex's summary prompt so we can estimate input tokens."""
    return f"""You are given a part of a document, your task is to generate a description of the partial document about what are main points covered in the partial document.

    Partial Document Text: {node_text}
    
    Directly return the description, do not include any other text.
    """


@contextmanager
def _summary_progress_tracker(source: str, total_nodes: int):
    """Track summary progress and aggregate token estimates across all nodes."""
    previous = _get_summary_tracker()
    tracker = {
        "source": source,
        "total_nodes": total_nodes,
        "started_nodes": 0,
        "completed_nodes": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }
    _PROGRESS_STATE.summary_tracker = tracker
    emit_progress(
        "pageindex_summary_batch_started",
        f"Generating summaries for {total_nodes} nodes.",
        source=source,
        total_nodes=total_nodes,
        completed_nodes=0,
        total_input_tokens=0,
        total_output_tokens=0,
    )
    try:
        yield tracker
    finally:
        emit_progress(
            "pageindex_summary_batch_completed",
            f"Finished generating summaries for {tracker['completed_nodes']} of {total_nodes} nodes.",
            source=source,
            total_nodes=total_nodes,
            completed_nodes=tracker["completed_nodes"],
            total_input_tokens=tracker["total_input_tokens"],
            total_output_tokens=tracker["total_output_tokens"],
        )
        if previous is None:
            if hasattr(_PROGRESS_STATE, "summary_tracker"):
                delattr(_PROGRESS_STATE, "summary_tracker")
        else:
            _PROGRESS_STATE.summary_tracker = previous


def _summary_started(node: Any) -> None:
    """Emit a progress event when one node summary begins."""
    tracker = _get_summary_tracker()
    if tracker is None:
        return
    tracker["started_nodes"] += 1
    node_id, title = _summary_label(node)
    emit_progress(
        "pageindex_summary_node_started",
        f"Summarizing node {tracker['started_nodes']} of {tracker['total_nodes']}: {title}",
        source=tracker["source"],
        total_nodes=tracker["total_nodes"],
        started_nodes=tracker["started_nodes"],
        completed_nodes=tracker["completed_nodes"],
        node_id=node_id,
        title=title,
        total_input_tokens=tracker["total_input_tokens"],
        total_output_tokens=tracker["total_output_tokens"],
    )


def _summary_completed(
    node: Any,
    summary_text: str | None = None,
    *,
    used_model: bool = True,
    estimated_input_tokens: int | None = None,
    estimated_output_tokens: int | None = None,
) -> None:
    """Emit a progress event when one node summary finishes."""
    tracker = _get_summary_tracker()
    if tracker is None:
        return
    input_tokens = max(0, estimated_input_tokens or 0)
    output_tokens = max(0, estimated_output_tokens or 0)
    tracker["total_input_tokens"] += input_tokens
    tracker["total_output_tokens"] += output_tokens
    tracker["completed_nodes"] += 1
    node_id, title = _summary_label(node)
    emit_progress(
        "pageindex_summary_node_completed",
        f"Completed summary {tracker['completed_nodes']} of {tracker['total_nodes']}: {title}",
        source=tracker["source"],
        total_nodes=tracker["total_nodes"],
        started_nodes=tracker["started_nodes"],
        completed_nodes=tracker["completed_nodes"],
        node_id=node_id,
        title=title,
        used_model=used_model,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_total_tokens=input_tokens + output_tokens,
        total_input_tokens=tracker["total_input_tokens"],
        total_output_tokens=tracker["total_output_tokens"],
        summary_preview=(summary_text or "")[:140],
    )


def create_chat_completion(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    **kwargs: Any,
) -> Any:
    """Run a synchronous chat completion with compatibility fallbacks.

    Junior note:
    The `while True` loop here is deliberate. We try the request, strip only the
    unsupported parameters reported by the provider, and retry the same request
    shape until it succeeds or fails for a real reason.
    """
    request_kwargs = build_chat_completion_kwargs(
        model=model,
        messages=messages,
        temperature=temperature,
        reasoning_effort=normalize_reasoning_effort(reasoning_effort),
        **kwargs,
    )
    while True:
        try:
            response = client.chat.completions.create(**request_kwargs)
            record_llm_usage(messages=messages, response=response)
            return response
        except Exception as exc:
            stripped = False
            if "temperature" in request_kwargs and is_temperature_unsupported_error(exc):
                request_kwargs.pop("temperature", None)
                stripped = True
            if "reasoning_effort" in request_kwargs and is_reasoning_effort_unsupported_error(exc):
                request_kwargs.pop("reasoning_effort", None)
                stripped = True
            if not stripped:
                raise


async def create_chat_completion_async(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    **kwargs: Any,
) -> Any:
    """Async equivalent of `create_chat_completion` with the same fallbacks."""
    request_kwargs = build_chat_completion_kwargs(
        model=model,
        messages=messages,
        temperature=temperature,
        reasoning_effort=normalize_reasoning_effort(reasoning_effort),
        **kwargs,
    )
    while True:
        try:
            response = await client.chat.completions.create(**request_kwargs)
            record_llm_usage(messages=messages, response=response)
            return response
        except Exception as exc:
            stripped = False
            if "temperature" in request_kwargs and is_temperature_unsupported_error(exc):
                request_kwargs.pop("temperature", None)
                stripped = True
            if "reasoning_effort" in request_kwargs and is_reasoning_effort_unsupported_error(exc):
                request_kwargs.pop("reasoning_effort", None)
                stripped = True
            if not stripped:
                raise


def _normalize_base_url(value: str) -> str:
    """Normalize base URLs so downstream client construction stays consistent."""
    return value.rstrip("/") + "/"


RETRIEVAL_MODE_OPTIONS = ("hybrid", "pageindex")


def get_retrieval_mode() -> str:
    """Return the active retrieval mode from the environment.

    - ``hybrid``    (default) — deterministic two-hop pipeline:
                    router → navigator → fetcher → answer LLM.
    - ``pageindex`` — agentic tool-use loop: the LLM calls
                    get_document_structure / get_node_content iteratively
                    until it has enough context to answer.
    """
    mode = os.getenv("RETRIEVAL_MODE", "hybrid").strip().lower()
    return mode if mode in RETRIEVAL_MODE_OPTIONS else "hybrid"


def detect_llm_provider() -> str:
    """Infer whether the runtime should behave like OpenAI or Azure OpenAI."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider in {"openai", "azure"}:
        return provider

    azure_markers = (
        os.getenv("AZURE_OPENAI_BASE_URL"),
        os.getenv("AZURE_OPENAI_ENDPOINT"),
        os.getenv("AZURE_OPENAI_API_KEY"),
    )
    if any(marker for marker in azure_markers):
        return "azure"

    return "openai"


def get_default_model() -> str:
    """Resolve the default model/deployment name from environment variables."""
    explicit_model = os.getenv("LLM_MODEL", "").strip()
    if explicit_model:
        return explicit_model

    provider = detect_llm_provider()
    if provider == "azure":
        return os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "").strip() or os.getenv(
            "OPENAI_MODEL", "gpt-4o-2024-11-20"
        )

    return os.getenv("OPENAI_MODEL", "gpt-4o-2024-11-20")


def get_llm_config() -> LLMConfig:
    """Build the normalized provider config used for all API clients."""
    provider = detect_llm_provider()
    default_model = get_default_model()

    if provider == "azure":
        api_key = (
            os.getenv("AZURE_OPENAI_API_KEY")
            or os.getenv("CHATGPT_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        if not api_key:
            raise EnvironmentError(
                "Azure OpenAI is selected but no API key was found. "
                "Set AZURE_OPENAI_API_KEY."
            )

        base_url = os.getenv("AZURE_OPENAI_BASE_URL", "").strip()
        if not base_url:
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
            if endpoint:
                base_url = f"{endpoint.rstrip('/')}/openai/v1/"

        if not base_url:
            raise EnvironmentError(
                "Azure OpenAI is selected but no endpoint/base URL was found. "
                "Set AZURE_OPENAI_BASE_URL or AZURE_OPENAI_ENDPOINT."
            )

        return LLMConfig(
            provider="azure",
            api_key=api_key,
            base_url=_normalize_base_url(base_url),
            default_model=default_model,
        )

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("CHATGPT_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "OpenAI is selected but no API key was found. "
            "Set OPENAI_API_KEY or CHATGPT_API_KEY."
        )

    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    return LLMConfig(
        provider="openai",
        api_key=api_key,
        base_url=_normalize_base_url(base_url) if base_url else None,
        default_model=default_model,
    )


def ensure_pageindex_environment() -> None:
    """Backfill the environment variables PageIndex expects to read directly."""
    config = get_llm_config()

    os.environ["CHATGPT_API_KEY"] = config.api_key
    os.environ["OPENAI_API_KEY"] = config.api_key

    if config.base_url:
        os.environ["OPENAI_BASE_URL"] = config.base_url
    else:
        os.environ.pop("OPENAI_BASE_URL", None)


def get_sync_client(api_key: str | None = None) -> "OpenAI":
    """Create a synchronous OpenAI-compatible client for the active provider."""
    if OpenAI is None:  # pragma: no cover - depends on installed deps
        raise ImportError("openai is required for LLM-backed operations.")

    config = get_llm_config()
    client_kwargs = {"api_key": api_key or config.api_key}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url

    return OpenAI(**client_kwargs)


def get_async_client(api_key: str | None = None) -> "AsyncOpenAI":
    """Create an async OpenAI-compatible client for the active provider."""
    if AsyncOpenAI is None:  # pragma: no cover - depends on installed deps
        raise ImportError("openai is required for LLM-backed operations.")

    config = get_llm_config()
    client_kwargs = {"api_key": api_key or config.api_key}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url

    return AsyncOpenAI(**client_kwargs)


def _pageindex_messages(prompt: str, chat_history: list[dict] | None = None) -> list[dict]:
    """Build the message list expected by PageIndex's OpenAI helper functions."""
    if chat_history:
        # `dict(turn)` creates a shallow copy so we do not mutate the caller's history.
        messages = [dict(turn) for turn in chat_history]
        messages.append({"role": "user", "content": prompt})
        return messages
    return [{"role": "user", "content": prompt}]


def patch_pageindex_llm_helpers(pageindex_utils_module: Any, *target_modules: Any) -> None:
    """Replace PageIndex's direct OpenAI helpers with our compatibility layer.

    This is how we keep GPT-5/Azure quirks, reasoning effort, and retry behavior
    centralized in our own code instead of modifying the vendored dependency.

    PageIndex uses ``from .utils import *`` in its module files, so after the
    wildcard import the functions live directly in each module's own namespace.
    We therefore patch every target module individually in addition to utils.
    """
    def llm_completion(
        model: str,
        prompt: str,
        chat_history: list[dict] | None = None,
        return_finish_reason: bool = False,
    ) -> "str | tuple[str, str]":
        """Patched sync helper — mirrors PageIndex's llm_completion signature."""
        max_retries = 10
        for attempt in range(max_retries):
            try:
                client = get_sync_client()
                response = create_chat_completion(
                    client=client,
                    model=model,
                    messages=_pageindex_messages(prompt, chat_history),
                    temperature=0,
                    reasoning_effort=INGESTION_REASONING_EFFORT,
                )
                if return_finish_reason:
                    finish_reason = extract_finish_reason(response)
                    if finish_reason == "length":
                        return extract_llm_text(response), "max_output_reached"
                    return extract_llm_text(response), "finished"
                return extract_llm_text(response)
            except Exception as exc:  # pragma: no cover - network/runtime behavior
                logger.warning(
                    "PageIndex LLM call attempt %d/%d failed, retrying in 1 s: %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    raise RuntimeError(
                        f"PageIndex LLM call failed after {max_retries} retries: {exc}"
                    )

    async def llm_acompletion(
        model: str,
        prompt: str,
    ) -> str:
        """Patched async helper — mirrors PageIndex's llm_acompletion signature."""
        max_retries = 10
        for attempt in range(max_retries):
            try:
                async with get_async_client() as client:
                    response = await create_chat_completion_async(
                        client=client,
                        model=model,
                        messages=_pageindex_messages(prompt),
                        temperature=0,
                        reasoning_effort=INGESTION_REASONING_EFFORT,
                    )
                    return extract_llm_text(response)
            except Exception as exc:  # pragma: no cover - network/runtime behavior
                logger.warning(
                    "PageIndex LLM call attempt %d/%d failed, retrying in 1 s: %s",
                    attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    raise RuntimeError(
                        f"PageIndex LLM call failed after {max_retries} retries: {exc}"
                    )

    _EXPECTED_PAGEINDEX_ATTRS = ("llm_completion", "llm_acompletion")
    missing = [a for a in _EXPECTED_PAGEINDEX_ATTRS if not hasattr(pageindex_utils_module, a)]
    if missing:
        raise RuntimeError(
            f"PageIndex interface has changed — expected attributes not found in "
            f"pageindex.utils: {missing}. "
            "Update the compatibility patch in utils.py to match the new interface."
        )

    # Patch utils first, then each target module individually.  page_index.py
    # and page_index_md.py both do `from .utils import *`, which copies the
    # function references into their own namespaces at import time, so patching
    # utils alone is not sufficient.
    for mod in (pageindex_utils_module, *target_modules):
        mod.llm_completion = llm_completion
        mod.llm_acompletion = llm_acompletion


def patch_pageindex_progress_hooks(
    pageindex_utils_module: Any,
    page_index_module: Any,
    page_index_md_module: Any,
) -> None:
    """Patch PageIndex summary helpers so the UI can observe live progress."""
    if getattr(pageindex_utils_module, "__hybrid_progress_hooks_patched__", False):
        return

    original_generate_node_summary = pageindex_utils_module.generate_node_summary
    original_generate_summaries_for_structure = (
        pageindex_utils_module.generate_summaries_for_structure
    )
    original_md_get_node_summary = page_index_md_module.get_node_summary
    original_md_generate_summaries = (
        page_index_md_module.generate_summaries_for_structure_md
    )

    async def wrapped_generate_node_summary(node, model=None):
        """Wrap PageIndex PDF summary generation with progress/token reporting."""
        _summary_started(node)
        try:
            summary = await original_generate_node_summary(node, model=model)
            node_text = node.get("text", "") if isinstance(node, dict) else ""
            _summary_completed(
                node,
                summary_text=summary,
                used_model=True,
                estimated_input_tokens=estimate_tokens(_summary_prompt(str(node_text))),
                estimated_output_tokens=estimate_tokens(summary),
            )
            return summary
        finally:
            pass

    async def wrapped_generate_summaries_for_structure(structure, model=None):
        """Wrap PageIndex PDF batch-summary generation with a tracker context."""
        total_nodes = len(pageindex_utils_module.structure_to_list(structure))
        with _summary_progress_tracker("pdf", total_nodes):
            return await original_generate_summaries_for_structure(structure, model=model)

    async def wrapped_md_get_node_summary(node, summary_token_threshold=400, model=None):
        """Wrap markdown summary generation, including the "skip if short" branch."""
        _summary_started(node)
        try:
            node_text = str(node.get("text", "")) if isinstance(node, dict) else ""
            source_tokens = estimate_tokens(node_text)
            summary = await original_md_get_node_summary(
                node,
                summary_token_threshold=summary_token_threshold,
                model=model,
            )
            used_model = source_tokens >= (summary_token_threshold or 0)
            _summary_completed(
                node,
                summary_text=summary,
                used_model=used_model,
                estimated_input_tokens=(
                    estimate_tokens(_summary_prompt(node_text)) if used_model else 0
                ),
                estimated_output_tokens=estimate_tokens(summary) if used_model else 0,
            )
            return summary
        finally:
            pass

    async def wrapped_md_generate_summaries_for_structure(
        structure, summary_token_threshold, model=None
    ):
        """Wrap markdown batch-summary generation with a tracker context."""
        total_nodes = len(pageindex_utils_module.structure_to_list(structure))
        with _summary_progress_tracker("markdown", total_nodes):
            return await original_md_generate_summaries(
                structure,
                summary_token_threshold=summary_token_threshold,
                model=model,
            )

    pageindex_utils_module.generate_node_summary = wrapped_generate_node_summary
    pageindex_utils_module.generate_summaries_for_structure = (
        wrapped_generate_summaries_for_structure
    )
    page_index_md_module.get_node_summary = wrapped_md_get_node_summary
    page_index_md_module.generate_summaries_for_structure_md = (
        wrapped_md_generate_summaries_for_structure
    )

    if hasattr(page_index_module, "generate_summaries_for_structure"):
        page_index_module.generate_summaries_for_structure = (
            wrapped_generate_summaries_for_structure
        )
    if hasattr(page_index_module, "generate_node_summary"):
        page_index_module.generate_node_summary = wrapped_generate_node_summary

    pageindex_utils_module.__hybrid_progress_hooks_patched__ = True


def estimate_tokens(text: str) -> int:
    """Estimate token count cheaply, with a coarse fallback if tokenizers fail."""
    if not text:
        return 0

    if tiktoken is not None:
        try:
            ensure_tiktoken_model_aliases()
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            pass

    return max(1, len(text) // 4)


ensure_tiktoken_model_aliases()
