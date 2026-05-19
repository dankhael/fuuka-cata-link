"""Translate scraped social-media captions to Brazilian Portuguese.

Pluggable LLM backend — Anthropic (Claude) and OpenAI (GPT) are both supported.
Provider is chosen by ``settings.translation_provider``; if unset, the first
available API key wins (Anthropic → OpenAI). Best-effort: a missing key or an
API failure logs a warning and returns the original text — translation never
blocks posting the media.

Add a new provider by subclassing :class:`TranslatorBackend` and wiring it into
:func:`_build_backend`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import structlog

from src.config import settings

logger = structlog.get_logger()

PORTUGUESE_LANGS: frozenset[str] = frozenset({"pt", "pt-br", "pt-pt"})

DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
}

_MAX_TOKENS = 600

_SYSTEM_PROMPT = (
    "You are a translator. Translate the user's social media post into Brazilian "
    "Portuguese (pt-BR). Preserve tone, slang, humor, and intent. Keep @mentions, "
    "#hashtags, URLs, emoji, and code unchanged. Do not add commentary, quotes, "
    "or labels — output only the translated text."
)


@dataclass
class TranslationResult:
    text: str
    input_tokens: int
    output_tokens: int


class TranslatorBackend(ABC):
    """One LLM provider configured to translate to pt-BR.

    Subclasses raise on API errors; the outer :func:`translate_to_portuguese`
    catches them and falls back to the original text.
    """

    name: str = "base"

    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    async def translate(self, text: str) -> TranslationResult: ...


class AnthropicBackend(TranslatorBackend):
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__(model)
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)

    async def translate(self, text: str) -> TranslationResult:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return TranslationResult(
            text="".join(parts).strip(),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class OpenAIBackend(TranslatorBackend):
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__(model)
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)

    async def translate(self, text: str) -> TranslationResult:
        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        message = response.choices[0].message.content or ""
        usage = response.usage
        return TranslationResult(
            text=message.strip(),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )


_backend: TranslatorBackend | None = None
_backend_initialized: bool = False


def _autodetect_provider() -> str | None:
    """Pick a provider from whichever API key is set. Anthropic wins ties."""
    if settings.anthropic_api_key:
        return "anthropic"
    if settings.openai_api_key:
        return "openai"
    return None


def _build_backend() -> TranslatorBackend | None:
    explicit = (settings.translation_provider or "").strip().lower() or None
    provider = explicit or _autodetect_provider()
    if provider is None:
        logger.warning("translation_no_provider_configured")
        return None

    model = settings.translation_model or DEFAULT_MODELS.get(provider)
    if model is None:
        logger.warning("translation_unknown_provider", provider=provider)
        return None

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning("translation_missing_key", provider="anthropic")
            return None
        return AnthropicBackend(settings.anthropic_api_key, model)

    if provider == "openai":
        if not settings.openai_api_key:
            logger.warning("translation_missing_key", provider="openai")
            return None
        return OpenAIBackend(settings.openai_api_key, model)

    logger.warning("translation_unknown_provider", provider=provider)
    return None


def _get_backend() -> TranslatorBackend | None:
    """Return the configured backend, lazily built once per process."""
    global _backend, _backend_initialized
    if _backend_initialized:
        return _backend
    _backend_initialized = True
    _backend = _build_backend()
    if _backend is not None:
        logger.info("translation_backend_ready", provider=_backend.name, model=_backend.model)
    return _backend


def _is_portuguese(lang: str | None) -> bool:
    return lang is not None and lang.lower() in PORTUGUESE_LANGS


async def translate_to_portuguese(text: str, source_lang: str | None) -> str:
    """Translate *text* to Brazilian Portuguese via the configured LLM backend.

    Returns the original text unchanged when *source_lang* is already Portuguese
    (to avoid wasting an API call), when no backend is configured, or when the
    API call fails or returns empty content. Failures are logged but never
    raised — translation is best-effort.
    """
    if not text:
        return text
    if _is_portuguese(source_lang):
        logger.debug("translation_skipped_portuguese", lang=source_lang, chars=len(text))
        return text

    backend = _get_backend()
    if backend is None:
        logger.warning("translation_skipped_no_backend", chars=len(text))
        return text

    try:
        result = await backend.translate(text)
    except Exception as exc:
        logger.warning(
            "translation_failed",
            error=str(exc),
            provider=backend.name,
            model=backend.model,
            source_lang=source_lang,
            chars=len(text),
        )
        return text

    if not result.text:
        logger.warning(
            "translation_empty_response",
            provider=backend.name,
            model=backend.model,
            source_lang=source_lang,
            chars=len(text),
        )
        return text

    logger.info(
        "translation_completed",
        provider=backend.name,
        model=backend.model,
        source_lang=source_lang,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        chars_in=len(text),
        chars_out=len(result.text),
    )
    return result.text
