from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.utils import translator
from src.utils.translator import (
    AnthropicBackend,
    OpenAIBackend,
    TranslationResult,
    TranslatorBackend,
)


@pytest.fixture(autouse=True)
def _reset_backend_singleton():
    """Each test starts with a fresh lazy backend so patches stick."""
    translator._backend = None
    translator._backend_initialized = False
    yield
    translator._backend = None
    translator._backend_initialized = False


class FakeBackend(TranslatorBackend):
    """In-test backend that records calls and returns a canned translation."""

    name = "fake"

    def __init__(self, output: str = "translated", error: Exception | None = None) -> None:
        super().__init__(model="fake-model")
        self.output = output
        self.error = error
        self.calls: list[str] = []

    async def translate(self, text: str) -> TranslationResult:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return TranslationResult(text=self.output, input_tokens=5, output_tokens=7)


def _install(backend: TranslatorBackend | None) -> None:
    """Pin a backend instance so the lazy getter returns it without rebuilding."""
    translator._backend = backend
    translator._backend_initialized = True


# ---------------------------------------------------------------------------
# translate_to_portuguese — provider-agnostic behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["pt", "PT", "pt-BR", "pt-br", "pt-PT"])
async def test_skips_when_already_portuguese(lang: str):
    fake = FakeBackend()
    _install(fake)

    result = await translator.translate_to_portuguese("olá", lang)

    assert result == "olá"
    assert fake.calls == []


async def test_skips_when_no_backend_configured():
    with (
        patch.object(translator.settings, "anthropic_api_key", None),
        patch.object(translator.settings, "openai_api_key", None),
        patch.object(translator.settings, "translation_provider", None),
    ):
        result = await translator.translate_to_portuguese("hello", "en")

    assert result == "hello"


async def test_translates_non_portuguese_text():
    fake = FakeBackend(output="olá mundo")
    _install(fake)

    result = await translator.translate_to_portuguese("hello world", "en")

    assert result == "olá mundo"
    assert fake.calls == ["hello world"]


async def test_translates_when_lang_is_none():
    """When fxtwitter omits the lang field we still translate — /translate
    was explicitly requested, better to spend a fraction of a cent than
    silently no-op."""
    fake = FakeBackend(output="traduzido")
    _install(fake)

    result = await translator.translate_to_portuguese("something", None)

    assert result == "traduzido"
    assert fake.calls == ["something"]


async def test_returns_original_on_backend_failure():
    fake = FakeBackend(error=RuntimeError("network down"))
    _install(fake)

    result = await translator.translate_to_portuguese("hello", "en")

    assert result == "hello"


async def test_returns_original_on_empty_response():
    fake = FakeBackend(output="")
    _install(fake)

    result = await translator.translate_to_portuguese("hello", "en")

    assert result == "hello"


async def test_empty_input_returns_empty():
    fake = FakeBackend()
    _install(fake)

    result = await translator.translate_to_portuguese("", "en")

    assert result == ""
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


async def test_autodetect_prefers_anthropic_when_both_keys_set():
    with (
        patch.object(translator.settings, "anthropic_api_key", "sk-ant"),
        patch.object(translator.settings, "openai_api_key", "sk-oai"),
        patch.object(translator.settings, "translation_provider", None),
        patch.object(translator.settings, "translation_model", None),
    ):
        backend = translator._build_backend()

    assert isinstance(backend, AnthropicBackend)
    assert backend.model == translator.DEFAULT_MODELS["anthropic"]


async def test_autodetect_falls_back_to_openai_when_only_openai_key():
    with (
        patch.object(translator.settings, "anthropic_api_key", None),
        patch.object(translator.settings, "openai_api_key", "sk-oai"),
        patch.object(translator.settings, "translation_provider", None),
        patch.object(translator.settings, "translation_model", None),
    ):
        backend = translator._build_backend()

    assert isinstance(backend, OpenAIBackend)
    assert backend.model == translator.DEFAULT_MODELS["openai"]


async def test_explicit_provider_overrides_autodetect():
    """User wants to spend OpenAI credits even though an Anthropic key is set."""
    with (
        patch.object(translator.settings, "anthropic_api_key", "sk-ant"),
        patch.object(translator.settings, "openai_api_key", "sk-oai"),
        patch.object(translator.settings, "translation_provider", "openai"),
        patch.object(translator.settings, "translation_model", None),
    ):
        backend = translator._build_backend()

    assert isinstance(backend, OpenAIBackend)


async def test_explicit_provider_missing_key_returns_none():
    with (
        patch.object(translator.settings, "anthropic_api_key", None),
        patch.object(translator.settings, "openai_api_key", None),
        patch.object(translator.settings, "translation_provider", "openai"),
        patch.object(translator.settings, "translation_model", None),
    ):
        backend = translator._build_backend()

    assert backend is None


async def test_unknown_provider_returns_none():
    with (
        patch.object(translator.settings, "anthropic_api_key", "sk-ant"),
        patch.object(translator.settings, "openai_api_key", "sk-oai"),
        patch.object(translator.settings, "translation_provider", "cohere"),
        patch.object(translator.settings, "translation_model", None),
    ):
        backend = translator._build_backend()

    assert backend is None


async def test_translation_model_override_applied():
    with (
        patch.object(translator.settings, "anthropic_api_key", "sk-ant"),
        patch.object(translator.settings, "openai_api_key", None),
        patch.object(translator.settings, "translation_provider", None),
        patch.object(translator.settings, "translation_model", "claude-opus-4-7"),
    ):
        backend = translator._build_backend()

    assert isinstance(backend, AnthropicBackend)
    assert backend.model == "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Backend wire-format tests — ensure each backend calls its SDK correctly and
# normalises the response into TranslationResult.
# ---------------------------------------------------------------------------


async def test_anthropic_backend_parses_response():
    backend = AnthropicBackend(api_key="sk-test", model="claude-haiku-4-5")
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="olá")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=4),
    )
    backend._client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=response))
    )

    result = await backend.translate("hello")

    assert result == TranslationResult(text="olá", input_tokens=10, output_tokens=4)
    kwargs = backend._client.messages.create.await_args.kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert "Brazilian Portuguese" in kwargs["system"]


async def test_openai_backend_parses_response():
    backend = OpenAIBackend(api_key="sk-test", model="gpt-4o-mini")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="olá"))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
    )
    backend._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response)))
    )

    result = await backend.translate("hello")

    assert result == TranslationResult(text="olá", input_tokens=12, output_tokens=3)
    kwargs = backend._client.chat.completions.create.await_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"][0]["role"] == "system"
    assert "Brazilian Portuguese" in kwargs["messages"][0]["content"]
    assert kwargs["messages"][1] == {"role": "user", "content": "hello"}
