"""Domain models and enums for deterministic preflight gates (G1–G4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DeliverableForm(StrEnum):
    """Deliverable form classification (Gate 1)."""

    REBUILT_APK = "rebuilt_apk"
    MODULE = "module"
    RPC = "rpc"
    REPORT = "report"


class ExecutionLayer(StrEnum):
    """Code location and execution layer (Gate 3)."""

    MANIFEST_RESOURCE = "manifest_resource"
    DEX = "dex"
    NATIVE_ELF = "native_elf"
    FLUTTER_DART = "flutter_dart"
    UNITY_IL2CPP = "unity_il2cpp"
    UNITY_MONO = "unity_mono"
    REACT_NATIVE_HERMES = "react_native_hermes"
    XAMARIN_DOTNET = "xamarin_dotnet"


class GateStatus(StrEnum):
    """Evaluation status of a gate."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class GateResult(BaseModel):
    """Result of an individual gate evaluation."""

    gate_id: str
    name: str
    passed: bool
    status: GateStatus
    deliverable_form: DeliverableForm | None = None
    is_zero_ai: bool = False
    execution_layer: ExecutionLayer | None = None
    message: str = ""
    diagnostics: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreflightReport(BaseModel):
    """Comprehensive report produced by the PreflightGateEngine."""

    all_passed: bool
    results: list[GateResult] = Field(default_factory=list)
    deliverable_form: DeliverableForm = DeliverableForm.REBUILT_APK
    is_zero_ai: bool = False
    execution_layer: ExecutionLayer = ExecutionLayer.DEX
    failed_gate: GateResult | None = None
    summary: str = ""
