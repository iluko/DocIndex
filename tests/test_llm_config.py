"""Tests for provider/model configuration helpers."""

from __future__ import annotations

import os

from utils import detect_llm_provider, ensure_pageindex_environment, get_default_model, get_llm_config


def test_azure_llm_config_derives_base_url_and_model(monkeypatch) -> None:
    """Azure config should derive the OpenAI-compatible base URL correctly."""
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "sip-gpt4o")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    config = get_llm_config()

    assert detect_llm_provider() == "azure"
    assert config.base_url == "https://example.openai.azure.com/openai/v1/"
    assert config.default_model == "sip-gpt4o"


def test_pageindex_env_is_backfilled_for_azure(monkeypatch) -> None:
    """PageIndex compatibility env vars should be populated for Azure mode."""
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.delenv("CHATGPT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    ensure_pageindex_environment()

    assert os.environ["CHATGPT_API_KEY"] == "azure-key"
    assert os.environ["OPENAI_API_KEY"] == "azure-key"
    assert os.environ["OPENAI_BASE_URL"] == "https://example.openai.azure.com/openai/v1/"


def test_default_model_prefers_llm_model(monkeypatch) -> None:
    """`LLM_MODEL` should win over provider-specific fallback variables."""
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_MODEL", "preferred-deployment")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "fallback-deployment")

    assert get_default_model() == "preferred-deployment"
