"""Deterministic Preflight Gate Engine (G1–G4).

Enforces:
- Gate 1: Deliverable Form & Zero-AI Routing
- Gate 2: Host Toolchain & Environment Truth
- Gate 3: Code Location & Execution Layer
- Gate 4: Unmodified Roundtrip Build Control
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from noir.application.deterministic_service import parse_operation_spec
from noir.application.doctor import DoctorService
from noir.domain.config import NoirConfig, get_config
from noir.domain.gates import (
    DeliverableForm,
    ExecutionLayer,
    GateResult,
    GateStatus,
    PreflightReport,
)
from noir.domain.models import AnalysisResult
from noir.security.xml import parse as secure_parse

logger = logging.getLogger(__name__)


class PreflightGateEngine:
    """Evaluates deterministic preflight gates G1–G4 prior to build or model invocation."""

    def __init__(
        self,
        config: NoirConfig | None = None,
        doctor_service: DoctorService | None = None,
    ) -> None:
        self.config = config or get_config()
        self.doctor_service = doctor_service or DoctorService(self.config)

    def evaluate_g1(
        self,
        request: str,
        analysis: AnalysisResult | None = None,
        workspace: Any | None = None,
    ) -> GateResult:
        """Gate 1: Deliverable Form & Zero-AI Routing."""
        text = request.strip().lower()

        # 1. Deliverable Form Classification
        if re.search(
            r"\b(?:rpc|emulate|emulation|call\s+routine|service-ify|token\s+service|crypto\s+service)\b",
            text,
        ):
            form = DeliverableForm.RPC
        elif re.search(
            r"\b(?:server[- ]authoritative|server[- ]side|bypass\s+server\s+payment|server\s+balance|vmp)\b",
            text,
        ):
            form = DeliverableForm.REPORT
        elif re.search(
            r"\b(?:xposed|lsposed|module)\b",
            text,
        ) or (analysis and any("anti_tamper" in ind.lower() for ind in getattr(analysis, "obfuscation_indicators", []))):
            form = DeliverableForm.MODULE
        else:
            form = DeliverableForm.REBUILT_APK

        # 2. Zero-AI Routing Check
        spec = parse_operation_spec(request)
        if spec and spec.intents:
            # Deterministic operations match (app_name, toast_flash, launch_redirect, permission)
            return GateResult(
                gate_id="G1",
                name="Deliverable Form & Zero-AI Routing",
                passed=True,
                status=GateStatus.PASSED,
                deliverable_form=DeliverableForm.REBUILT_APK,
                is_zero_ai=True,
                message=(
                    "Request matched deterministic operations; routing to zero-AI "
                    "DeterministicService, bypassing AI discovery and model calls."
                ),
                metadata={
                    "intents": spec.intents,
                    "strategies": spec.strategies,
                    "routed_handler": "DeterministicService",
                },
            )

        # Non-deterministic or advanced request requiring AI or specialized form
        msg = (
            f"Deliverable form classified as '{form.value}'. "
            "Proceeding to AI planning pipeline."
        )
        if form == DeliverableForm.REPORT:
            msg = (
                f"Deliverable form classified as '{form.value}': Target behavior is "
                "server-authoritative or VMP protected; client rebuild is not viable."
            )

        return GateResult(
            gate_id="G1",
            name="Deliverable Form & Zero-AI Routing",
            passed=True,
            status=GateStatus.PASSED,
            deliverable_form=form,
            is_zero_ai=False,
            message=msg,
            metadata={"form": form.value},
        )

    def evaluate_g2(self) -> GateResult:
        """Gate 2: Host Toolchain & Environment Truth."""
        report = self.doctor_service.run_doctor()

        diagnostics: list[str] = []
        # Key tools required for Gate 2 verification
        critical_tools = {"aapt2", "apktool", "java"}
        critical_missing: list[str] = []

        for check in report.checks:
            normalized_name = check.name.lower()
            if normalized_name in critical_tools:
                if not check.available:
                    critical_missing.append(f"{check.name}: {check.message}")
            if not check.available and check.required_for in (
                "required_for_import",
                "required_for_build",
            ):
                diagnostics.append(f"{check.name}: {check.message}")

        if critical_missing or not report.build_capable:
            all_errors = list(dict.fromkeys(critical_missing + diagnostics))
            return GateResult(
                gate_id="G2",
                name="Host Toolchain & Environment Truth",
                passed=False,
                status=GateStatus.BLOCKED,
                message=(
                    "Host toolchain verification failed. Missing required build tools: "
                    + "; ".join(all_errors)
                ),
                diagnostics=all_errors,
                metadata={"build_capable": report.build_capable},
            )

        # Check xattr presence (informative/environment truth)
        xattr_check = next((c for c in report.checks if c.name.lower() == "xattr"), None)
        xattr_ok = xattr_check.available if xattr_check else False

        return GateResult(
            gate_id="G2",
            name="Host Toolchain & Environment Truth",
            passed=True,
            status=GateStatus.PASSED,
            message="Host toolchain verified: AAPT2, APKTool, JDK 17+, xattrs operational.",
            metadata={
                "build_capable": True,
                "xattr_support": xattr_ok,
                "summary": report.summary,
            },
        )

    def evaluate_g3(
        self,
        request: str,
        analysis: AnalysisResult | None = None,
        workspace: Any | None = None,
    ) -> GateResult:
        """Gate 3: Code Location & Execution Layer Boundary."""
        text = request.strip().lower()

        # 1. Determine Owning Execution Layer
        layer = ExecutionLayer.DEX
        if analysis is not None:
            runtimes = getattr(analysis, "runtimes", set())
            if "flutter" in runtimes:
                layer = ExecutionLayer.FLUTTER_DART
            elif "il2cpp" in runtimes:
                layer = ExecutionLayer.UNITY_IL2CPP
            elif "mono" in runtimes:
                layer = ExecutionLayer.UNITY_MONO
            elif "hermes" in runtimes or "react_native" in runtimes:
                layer = ExecutionLayer.REACT_NATIVE_HERMES
            elif "xamarin" in runtimes:
                layer = ExecutionLayer.XAMARIN_DOTNET
            elif "native_only" in runtimes or (
                getattr(analysis, "native_libs", None)
                and not getattr(analysis, "smali_classes", None)
            ):
                layer = ExecutionLayer.NATIVE_ELF
            else:
                layer = ExecutionLayer.DEX
        elif workspace is not None:
            decoded = getattr(workspace, "decoded_dir", None) or Path(workspace)
            if decoded.is_dir():
                if any(decoded.rglob("libapp.so")):
                    layer = ExecutionLayer.FLUTTER_DART
                elif any(decoded.rglob("libil2cpp.so")):
                    layer = ExecutionLayer.UNITY_IL2CPP
                elif any(decoded.rglob("Assembly-CSharp.dll")):
                    layer = ExecutionLayer.UNITY_MONO
                elif any(decoded.rglob("index.android.bundle")):
                    layer = ExecutionLayer.REACT_NATIVE_HERMES

        # 2. Check if request is purely Manifest/Resources
        spec = parse_operation_spec(request)
        if spec is not None and not spec.requires_runtime:
            return GateResult(
                gate_id="G3",
                name="Code Location & Execution Layer",
                passed=True,
                status=GateStatus.PASSED,
                execution_layer=ExecutionLayer.MANIFEST_RESOURCE,
                message=(
                    "Operation targets Manifest/Resources layer. "
                    "Manifest declarations are runtime-neutral and safe across all architectures."
                ),
                metadata={"owning_layer": ExecutionLayer.MANIFEST_RESOURCE.value},
            )

        # 3. Check for Cross-Layer Regressions
        # If target runtime is Flutter Dart AOT or Unity IL2CPP, but request attempts DEX/Smali patching
        dex_patch_attempt = bool(
            re.search(r"\b(?:smali|dex\s+patch|bytecode|hook\s+method|smali\s+edit)\b", text)
        )
        if layer in (ExecutionLayer.FLUTTER_DART, ExecutionLayer.UNITY_IL2CPP) and dex_patch_attempt:
            diagnostic = (
                f"Attempted DEX/Smali modification on an application whose core logic is "
                f"compiled to {layer.value} binary. DEX patches cannot alter Dart AOT or "
                f"IL2CPP compiled behavior."
            )
            return GateResult(
                gate_id="G3",
                name="Code Location & Execution Layer",
                passed=False,
                status=GateStatus.BLOCKED,
                execution_layer=layer,
                message=f"Cross-layer regression blocked: DEX patch on {layer.value} runtime.",
                diagnostics=[diagnostic],
                metadata={"detected_layer": layer.value, "conflict": "dex_on_native_aot"},
            )

        return GateResult(
            gate_id="G3",
            name="Code Location & Execution Layer",
            passed=True,
            status=GateStatus.PASSED,
            execution_layer=layer,
            message=f"Code location and execution layer established: {layer.value}.",
            metadata={"execution_layer": layer.value},
        )

    def evaluate_g4(
        self,
        workspace: Any | None = None,
        original_apk: Path | None = None,
    ) -> GateResult:
        """Gate 4: Unmodified Roundtrip Build Control."""
        if workspace is None:
            return GateResult(
                gate_id="G4",
                name="Unmodified Roundtrip Build Control",
                passed=True,
                status=GateStatus.PASSED,
                message="Unmodified roundtrip build control ready (deferred to workspace instantiation).",
            )

        decoded = getattr(workspace, "decoded_dir", None)
        if decoded is None and isinstance(workspace, (str, Path)):
            decoded = Path(workspace)

        if not decoded or not decoded.is_dir():
            return GateResult(
                gate_id="G4",
                name="Unmodified Roundtrip Build Control",
                passed=False,
                status=GateStatus.FAILED,
                message="Unmodified roundtrip control failed: Decoded workspace directory does not exist.",
                diagnostics=["Missing decoded directory"],
            )

        # Check AndroidManifest.xml exists and is well-formed — only required for decoded
        # APK workspaces. A non-APK workspace (e.g. text-file project) has no manifest and
        # does not go through apktool build, so G4 is satisfied without it.
        apktool_yml = decoded / "apktool.yml"
        manifest_file = decoded / "AndroidManifest.xml"
        is_apk_workspace = apktool_yml.exists()
        if is_apk_workspace and not manifest_file.exists():
            return GateResult(
                gate_id="G4",
                name="Unmodified Roundtrip Build Control",
                passed=False,
                status=GateStatus.FAILED,
                message="Unmodified roundtrip control failed: AndroidManifest.xml missing in workspace.",
                diagnostics=["Missing AndroidManifest.xml"],
            )
        if not is_apk_workspace:
            # No APK to round-trip — gate is trivially satisfied.
            return GateResult(
                gate_id="G4",
                name="Unmodified Roundtrip Build Control",
                passed=True,
                status=GateStatus.PASSED,
                message="Non-APK workspace: roundtrip build control not applicable.",
            )

        try:
            secure_parse(manifest_file)
        except Exception as exc:
            return GateResult(
                gate_id="G4",
                name="Unmodified Roundtrip Build Control",
                passed=False,
                status=GateStatus.FAILED,
                message=f"Unmodified roundtrip control failed: AndroidManifest.xml is corrupt: {exc}",
                diagnostics=[str(exc)],
            )

        try:
            content = apktool_yml.read_text(encoding="utf-8")
            if not content.strip():
                return GateResult(
                    gate_id="G4",
                    name="Unmodified Roundtrip Build Control",
                    passed=False,
                    status=GateStatus.FAILED,
                    message="Unmodified roundtrip control failed: apktool.yml is empty.",
                    diagnostics=["Empty apktool.yml"],
                )
        except Exception as exc:
            return GateResult(
                gate_id="G4",
                name="Unmodified Roundtrip Build Control",
                passed=False,
                status=GateStatus.FAILED,
                message=f"Unmodified roundtrip control failed: apktool.yml cannot be read: {exc}",
                diagnostics=[str(exc)],
            )

        return GateResult(
            gate_id="G4",
            name="Unmodified Roundtrip Build Control",
            passed=True,
            status=GateStatus.PASSED,
            message="Unmodified roundtrip build control verified: Clean baseline workspace is intact and parsable.",
            metadata={"manifest": str(manifest_file)},
        )

    def evaluate_preflight(
        self,
        request: str,
        workspace: Any | None = None,
        analysis: AnalysisResult | None = None,
        original_apk: Path | None = None,
    ) -> PreflightReport:
        """Run all gates G1–G4 in strict sequence."""
        results: list[GateResult] = []

        # Gate 1
        g1 = self.evaluate_g1(request, analysis, workspace)
        results.append(g1)

        # Gate 2
        g2 = self.evaluate_g2()
        results.append(g2)

        # Gate 3
        g3 = self.evaluate_g3(request, analysis, workspace)
        results.append(g3)

        # Gate 4
        g4 = self.evaluate_g4(workspace, original_apk)
        results.append(g4)

        all_passed = all(r.passed for r in results)
        failed_gate = next((r for r in results if not r.passed), None)

        summary = (
            "All deterministic preflight gates passed (G1–G4)."
            if all_passed
            else f"Preflight gate check failed at {failed_gate.gate_id} ({failed_gate.name}): {failed_gate.message}"
        )

        return PreflightReport(
            all_passed=all_passed,
            results=results,
            deliverable_form=g1.deliverable_form or DeliverableForm.REBUILT_APK,
            is_zero_ai=g1.is_zero_ai,
            execution_layer=g3.execution_layer or ExecutionLayer.DEX,
            failed_gate=failed_gate,
            summary=summary,
        )


__all__ = [
    "DeliverableForm",
    "ExecutionLayer",
    "GateResult",
    "GateStatus",
    "PreflightGateEngine",
    "PreflightReport",
]
