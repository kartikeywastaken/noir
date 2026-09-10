"""AI provider factories shared by HTTP, CLI, and build-repair flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from noir.infrastructure.ai.gemini import GeminiProvider


def create_ai_provider(
    config, *, purpose: Literal["default", "discovery", "generation"] = "generation"
) -> GeminiProvider:
    """Create the configured plan/patch provider without changing its contract."""
    if config.ai_provider == "adk":
        from noir.infrastructure.ai.adk import AdkGeminiProvider

        return AdkGeminiProvider(config=config, purpose=purpose)
    if config.ai_provider == "gemini":
        from noir.infrastructure.ai.gemini import GeminiProvider

        return GeminiProvider(config=config, purpose=purpose)
    raise ValueError("AI provider is disabled")
