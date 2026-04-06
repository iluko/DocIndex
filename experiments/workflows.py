"""Reusable workflow helpers for experiment builds and comparison runs."""

from __future__ import annotations

from typing import Callable

from experiments.builds.presets import (
    pageindex_base_preset,
    pageindex_related_basic_preset,
    pageindex_related_enhanced_preset,
    rag_standard_preset,
    rag_vector_preset,
)
from experiments.models import BuildManifest, RunEntrySpec
from experiments.profiles import (
    aligned_default_answer_profile,
    hybrid_advanced_profile,
    hybrid_profile,
    pageindex_advanced_profile,
    pageindex_profile,
    rag_standard_profile,
    rag_vector_profile,
    rag_vector_rerank_profile,
    rag_vector_rrf_profile,
)


BuildPresetFactory = Callable[..., object]


def build_preset_catalog() -> dict[str, BuildPresetFactory]:
    """Return the supported build presets exposed by the experiments UI."""
    return {
        "rag_standard": rag_standard_preset,
        "rag_vector": rag_vector_preset,
        "pageindex_base": pageindex_base_preset,
        "pageindex_related_basic": pageindex_related_basic_preset,
        "pageindex_related_enhanced": pageindex_related_enhanced_preset,
    }


def retrieval_profile_catalog(
    *,
    reasoning_effort: str | None = None,
) -> dict[str, object]:
    """Return the supported retrieval profiles keyed by stable profile id."""
    return {
        "rag_standard": rag_standard_profile(reasoning_effort=reasoning_effort),
        "rag_vector": rag_vector_profile(reasoning_effort=reasoning_effort),
        "rag_vector_rrf": rag_vector_rrf_profile(reasoning_effort=reasoning_effort),
        "rag_vector_rerank": rag_vector_rerank_profile(reasoning_effort=reasoning_effort),
        "hybrid": hybrid_profile(reasoning_effort=reasoning_effort),
        "hybrid_advanced": hybrid_advanced_profile(reasoning_effort=reasoning_effort),
        "pageindex": pageindex_profile(reasoning_effort=reasoning_effort),
        "pageindex_advanced": pageindex_advanced_profile(reasoning_effort=reasoning_effort),
    }


def compatible_profile_ids_for_artifact_family(
    artifact_family: str,
    *,
    reasoning_effort: str | None = None,
) -> list[str]:
    """Return retrieval profile ids that can operate on one artifact family."""
    profile_map = retrieval_profile_catalog(reasoning_effort=reasoning_effort)
    return [
        profile_id
        for profile_id, profile in profile_map.items()
        if profile.artifact_family == artifact_family
    ]


def create_run_entries(
    *,
    builds: list[BuildManifest],
    selected_profile_ids: list[str],
    retrieval_reasoning_effort: str | None = None,
    answer_reasoning_effort: str | None = None,
) -> list[RunEntrySpec]:
    """Create compatible comparison entries across selected builds and profiles."""
    profile_map = retrieval_profile_catalog(reasoning_effort=retrieval_reasoning_effort)
    answer_profile = aligned_default_answer_profile(reasoning_effort=answer_reasoning_effort)

    entries: list[RunEntrySpec] = []
    for build in builds:
        compatible_ids = set(
            compatible_profile_ids_for_artifact_family(
                build.artifact_family,
                reasoning_effort=retrieval_reasoning_effort,
            )
        )
        for profile_id in selected_profile_ids:
            if profile_id not in compatible_ids:
                continue
            profile = profile_map[profile_id]
            entries.append(
                RunEntrySpec(
                    label=f"{build.build_label} · {profile.profile_id}",
                    build_id=build.build_id,
                    retrieval_profile=profile,
                    answer_profile=answer_profile,
                )
            )
    return entries
