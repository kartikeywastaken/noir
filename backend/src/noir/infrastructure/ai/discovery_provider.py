"""Abstract discovery provider interface.

Decouples the evidence-discovery phase from any specific AI SDK so discovery
can run on OpenRouter, Gemini, a local heuristic, or any future provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noir.domain.config import NoirConfig
    from noir.domain.models import AnalysisResult
    from noir.infrastructure.ai.context import AiContextTools
    from noir.infrastructure.ai.discovery import DiscoveryResult


class DiscoveryProvider(ABC):
    """Abstract interface for the discovery phase only.

    Implementations run a bounded evidence-gathering loop and return a
    DiscoveryResult that downstream planning can consume unchanged.
    """

    def __init__(self, config: NoirConfig):
        self.config = config

    @abstractmethod
    def discover(
        self,
        user_request: str,
        context_tools: AiContextTools,
        analysis: AnalysisResult,
    ) -> DiscoveryResult:
        """Run bounded evidence discovery.

        Args:
            user_request: What the user wants to modify.
            context_tools: Constrained workspace access for reading files.
            analysis: Static analysis results for the decoded APK.

        Returns:
            A DiscoveryResult with discovered files, inspections, and transcript.
        """
        ...
