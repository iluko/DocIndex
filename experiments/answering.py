"""Shared answer adapter for experiment retrieval comparisons."""

from __future__ import annotations

import time
from typing import Any

from experiments.models import AnswerProfileSpec
from utils import (
    create_chat_completion_async,
    extract_llm_text,
    get_async_client,
    managed_async_client,
    model_supports_explicit_temperature,
    normalize_reasoning_effort,
    render_conversation_context,
)


async def answer_from_context(
    *,
    query: str,
    retrieved_context: str,
    model: str,
    answer_profile: AnswerProfileSpec,
    conversation_context: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generate a final answer from retrieved context using a shared prompt family."""
    if not retrieved_context.strip():
        return (
            "No relevant context was retrieved for this query.",
            {"answer_time_seconds": 0.0, "ttft_seconds": 0.0},
        )

    prompt_family = answer_profile.prompt_family
    if prompt_family != "aligned_default":
        raise ValueError(f"Unsupported answer prompt_family '{prompt_family}'.")

    system_prompt = (
        "You are a document-comparison assistant. "
        "Answer the user's question using ONLY the retrieved context provided. "
        "Be precise, concise, and grounded. Use inline citations in square brackets "
        "that reference the chunk or node identifiers visible in the retrieved context "
        "when making specific claims. End with a short **Sources** block."
    )
    conversation_block = render_conversation_context(conversation_context)
    user_prompt = (
        f"Question: {query}\n\n"
        f"{conversation_block}\n\n"
        f"Retrieved Context:\n{retrieved_context}"
    ).strip()

    answer_started_at = time.perf_counter()
    async with managed_async_client(get_async_client()) as client:
        request_kwargs: dict[str, Any] = {
            "client": client,
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "reasoning_effort": answer_profile.reasoning_effort,
        }
        if model_supports_explicit_temperature(model):
            request_kwargs["temperature"] = 0.1
        response = await create_chat_completion_async(**request_kwargs)
    answer = extract_llm_text(response)
    answer_elapsed = max(0.0, time.perf_counter() - answer_started_at)
    return answer, {
        "answer_time_seconds": answer_elapsed,
        "ttft_seconds": answer_elapsed,
    }
