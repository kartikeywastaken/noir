"""Zero-AI discovery provider using static workspace heuristics.

Returns immediately with a static-fallback DiscoveryResult, delegating all
file selection to AiContextTools.build_context(). No API calls are made.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from noir.infrastructure.ai.discovery import DiscoveryResult
from noir.infrastructure.ai.discovery_provider import DiscoveryProvider

if TYPE_CHECKING:
    from noir.domain.config import NoirConfig
    from noir.domain.models import AnalysisResult
    from noir.infrastructure.ai.context import AiContextTools

logger = logging.getLogger(__name__)


class LocalDiscoveryProvider(DiscoveryProvider):
    """Zero-AI discovery using static workspace heuristics.

    This provider makes no API calls at all. It simply signals that the
    downstream code should use AiContextTools.build_context() for file
    selection — the same behavior as the current static fallback path.
    """

    def __init__(self, config: NoirConfig):
        super().__init__(config)

    def discover(
        self,
        user_request: str,
        context_tools: AiContextTools,
        analysis: AnalysisResult,
    ) -> DiscoveryResult:
        logger.debug("Local discovery provider: using static selection only")
        return DiscoveryResult(used_static_fallback=True, stop_reason="local_only")
