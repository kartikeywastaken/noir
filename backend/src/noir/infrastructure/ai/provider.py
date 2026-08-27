"""AI Provider interface and context boundary.

The AI provider must not:
- Access the host shell
- Choose arbitrary host paths
- Read unrelated files
- Approve its own changes
- Sign/install APKs
- Change policy
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from noir.domain.models import AnalysisResult, ChangePlan, PatchSet


class AiProvider(ABC):
    """Abstract interface for AI-assisted APK modification."""

    @abstractmethod
    def generate_plan(
        self,
        user_request: str,
        analysis: AnalysisResult,
        context: dict[str, Any],
        *,
        project_id: str,
    ) -> ChangePlan:
        """Generate a change plan from user request and analysis.

        Args:
            user_request: What the user wants to change.
            analysis: Static analysis results.
            context: Bounded workspace context (file snippets, etc.)
            project_id: The project ID.

        Returns:
            A structured ChangePlan for user review.
        """
        ...

    @abstractmethod
    def generate_patch(
        self,
        plan: ChangePlan,
        context: dict[str, Any],
    ) -> PatchSet:
        """Generate patch operations from an approved plan.

        Args:
            plan: The approved change plan.
            context: Bounded workspace context.

        Returns:
            A PatchSet with concrete operations.
        """
        ...

    @abstractmethod
    def diagnose_build_failure(
        self,
        error_log: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Diagnose a build failure.

        Args:
            error_log: Build error output.
            context: Bounded workspace context.

        Returns:
            Structured diagnosis with suggested fixes.
        """
        ...

    @abstractmethod
    def generate_summary(
        self,
        audit_data: dict[str, Any],
    ) -> str:
        """Generate a human-readable audit summary.

        Args:
            audit_data: Structured audit report data.

        Returns:
            Markdown summary text.
        """
        ...
