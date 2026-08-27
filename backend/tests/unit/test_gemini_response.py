"""Response-contract checks using an injected SDK boundary; no network requests."""

from types import SimpleNamespace

import pytest

from noir.domain.config import NoirConfig
from noir.infrastructure.ai.gemini import GeminiProvider, GeminiProviderError


@pytest.fixture
def provider():
    return GeminiProvider(
        config=NoirConfig(_env_file=None, gemini_api_key="unit-test-only", ai_provider="gemini")
    )


def inject_response(monkeypatch, provider, text):
    client = SimpleNamespace(
        models=SimpleNamespace(generate_content=lambda **kwargs: SimpleNamespace(text=text))
    )
    monkeypatch.setattr(provider, "_get_client", lambda: client)


@pytest.mark.parametrize("text", [None, "", "   ", 42, {"status": "ok"}])
def test_non_text_or_empty_response_rejected(monkeypatch, provider, text):
    inject_response(monkeypatch, provider, text)
    with pytest.raises(GeminiProviderError, match="Empty or non-text"):
        provider._call_model("test")


def test_valid_response_preserved(monkeypatch, provider):
    text = '{"status":"ok"}'
    inject_response(monkeypatch, provider, text)
    assert provider._call_model("test") == text


def test_oversized_response_rejected(monkeypatch, provider):
    provider.config.ai_max_output_size = 4
    inject_response(monkeypatch, provider, "too long")
    with pytest.raises(GeminiProviderError, match="oversized"):
        provider._call_model("test")
