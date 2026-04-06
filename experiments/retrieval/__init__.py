"""Retrieval adapters for experiment runs."""

from experiments.retrieval.registry import get_retrieval_adapter

__all__ = ["get_retrieval_adapter"]
