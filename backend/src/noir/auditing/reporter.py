"""Audit report generation — JSON and Markdown formats.

Generates factual reports from persisted structured data.
Redacts credentials, passwords, keys, and unnecessary paths.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from noir.domain.config import NoirConfig, get_config
from noir.infrastructure.database.repositories import (
    AnalysisRepository,
    ApprovalRepository,
    BuildRepository,
    EventRepository,
    ManualSessionRepository,
    PatchRepository,
    PlanRepository,
    ProjectRepository,
    ValidationRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace
from noir.patches.engine import BINARY_OPERATIONS


class AuditReporter:
    """Generates factual audit reports."""

    def __init__(self, config: NoirConfig | None = None):
        self.config = config or get_config()
        self.project_repo = ProjectRepository()
        self.analysis_repo = AnalysisRepository()
        self.plan_repo = PlanRepository()
        self.patch_repo = PatchRepository()
        self.approval_repo = ApprovalRepository()
        self.build_repo = BuildRepository()
        self.event_repo = EventRepository()
        self.validation_repo = ValidationRepository()

    def generate(self, project_id: str) -> dict[str, Any]:
        """Generate structured audit data."""
        project = self.project_repo.get(project_id)
        if not project:
            raise ValueError(f"Project not found: {project_id}")

        analysis = self.analysis_repo.get(project_id)
        plans = self.plan_repo.list_by_project(project_id)
        patches = self.patch_repo.list_by_project(project_id)
        approvals = self.approval_repo.list_by_project(project_id)
        builds = self.build_repo.list_by_project(project_id)
        events = self.event_repo.list_by_project(project_id, limit=1000)
        validation = self.validation_repo.get_latest(project_id)

        report: dict[str, Any] = {
            "report_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "project": {
                "id": project.id,
                "status": project.status.value,
                "original_filename": project.original_filename,
                "original_size": project.original_size,
                "original_sha256": project.original_sha256,
                "authorization_acknowledged": project.authorization_acknowledged,
                "workspace_revision": project.workspace_revision,
                "created_at": project.created_at.isoformat() if project.created_at else None,
            },
            "input": {
                "package_name": analysis.package_name if analysis else "",
                "version_name": analysis.version_name if analysis else "",
                "version_code": analysis.version_code if analysis else "",
                "min_sdk": analysis.min_sdk if analysis else None,
                "target_sdk": analysis.target_sdk if analysis else None,
                "permissions": analysis.permissions if analysis else [],
                "components_count": len(analysis.components) if analysis else 0,
                "smali_classes_count": len(analysis.smali_classes) if analysis else 0,
                "runtime": analysis.runtime if analysis else "dalvik",
                "runtimes": sorted(analysis.runtimes) if analysis else ["dalvik"],
                "managed_assemblies": analysis.managed_assemblies if analysis else [],
                "native_abis": analysis.native_abis if analysis else [],
            },
            "plans": [
                {
                    "plan_id": p.plan_id,
                    "user_request": p.user_request,
                    "provider": p.provider,
                    "model": p.model,
                    "intended_outcome": p.intended_outcome,
                    "file_changes": [fc.model_dump() for fc in p.file_changes],
                    "permission_changes": p.permission_changes,
                    "component_changes": p.component_changes,
                    "network_destinations": p.network_destinations,
                    "risks": p.risks,
                    "native_runtime": p.native_runtime,
                    "binary_targets": p.binary_targets,
                    "binary_risks": p.binary_risks,
                    "discovery_transcript": p.discovery_transcript,
                    "discovery_api_calls": p.discovery_api_calls,
                    "discovery_stop_reason": p.discovery_stop_reason,
                    "plan_hash": p.compute_hash(),
                }
                for p in plans
            ],
            "patches": [
                {
                    "patch_id": p.patch_id,
                    "plan_id": p.plan_id,
                    "provenance": p.provenance.value,
                    "operations_count": len(p.operations),
                    "patch_hash": p.compute_hash(),
                    "binary_operations": self._binary_operations(project_id, p),
                }
                for p in patches
            ],
            "manual_sessions": [
                {
                    "session_id": session.session_id,
                    "active": session.active,
                    "workspace_revision_start": session.workspace_revision_start,
                    "changed_files": session.detected_changes,
                    "message": self._redact(session.message),
                    "started_at": session.started_at.isoformat(),
                    "finished_at": session.finished_at.isoformat() if session.finished_at else None,
                }
                for session in ManualSessionRepository().list_by_project(project_id)
            ],
            "approvals": [
                {
                    "approval_id": a.approval_id,
                    "scope": a.scope.value,
                    "target_id": a.target_id,
                    "target_hash": a.target_hash,
                    "status": a.status.value,
                    "actor": a.actor,
                    "timestamp": a.created_at.isoformat() if a.created_at else None,
                }
                for a in approvals
            ],
            "validation": None,
            "builds": [
                {
                    "build_id": b.build_id,
                    "success": b.success,
                    "error": b.error_message,
                    "apktool_version": b.apktool_version,
                    "build_tools_version": b.build_tools_version,
                    "unsigned_hash": b.unsigned_apk_hash,
                    "aligned_hash": b.aligned_apk_hash,
                    "signed_hash": b.signed_apk_hash,
                }
                for b in builds
            ],
            "events": [
                {
                    "event_id": e.event_id,
                    "stage": e.stage.value if e.stage else None,
                    "severity": e.severity.value,
                    "message": self._redact(e.message),
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in events[:500]
            ],
            "warnings": [],
        }

        if validation:
            report["validation"] = {
                "passed": validation.passed,
                "error_count": validation.error_count,
                "warning_count": validation.warning_count,
                "findings": [f.model_dump() for f in validation.findings],
            }

        # Add warnings
        if analysis and analysis.obfuscation_indicators:
            report["warnings"].extend(analysis.obfuscation_indicators)
        if analysis and analysis.compatibility_warnings:
            report["warnings"].extend(analysis.compatibility_warnings)

        return report

    def _binary_operations(self, project_id: str, patch) -> list[dict[str, Any]]:
        workspace = ProjectWorkspace(project_id, self.config)
        journal_path = workspace.changes_dir / "journal" / f"journal_{patch.patch_id}.json"
        journal = {}
        if journal_path.is_file():
            try:
                journal = json.loads(journal_path.read_text())
            except (OSError, json.JSONDecodeError):
                journal = {}
        entries = {
            entry.get("path"): entry
            for entry in journal.get("files", [])
            if isinstance(entry, dict)
        }
        result = []
        for operation in patch.operations:
            if operation.operation not in BINARY_OPERATIONS:
                continue
            entry = entries.get(operation.relative_path, {})
            offset = operation.native_offset
            length = operation.native_length
            abi = operation.native_abi
            correlation_error = None
            if operation.operation.value.startswith("il2cpp_") and (
                offset is None or length is None or abi is None
            ):
                try:
                    from noir.infrastructure.il2cpp.metadata import Il2CppMetadata

                    metadata = sorted(workspace.decoded_dir.rglob("global-metadata.dat"))
                    target = workspace.safe_path(operation.relative_path)
                    reference = Il2CppMetadata(metadata[0], target).find_method(
                        operation.il2cpp_type_full_name or "",
                        operation.il2cpp_method_signature or "",
                    )
                    offset = reference.file_offset if offset is None else offset
                    length = reference.size if length is None else length
                    abi = reference.abi if abi is None else abi
                except Exception as exc:
                    correlation_error = (
                        "Unable to reconstruct the approved IL2CPP range: "
                        f"{exc}"
                    )
            record = {
                "operation": operation.operation.value,
                "target": operation.relative_path,
                "abi": abi,
                "skipped_abis": operation.native_skipped_abis,
                "skip_reason": operation.native_skip_reason,
                "affected_scope": operation.affected_scope,
                "whole_file_preimage_hash": operation.expected_preimage_hash,
                "whole_file_postimage_hash": entry.get("after_hash"),
                "method_il_preimage_hash": operation.expected_method_il_hash,
                "native_range_preimage_hash": (
                    operation.expected_native_bytes_hash
                    or operation.expected_function_bytes_hash
                ),
                "offset": offset,
                "length": length,
                "correlation_error": correlation_error,
                "before_disassembly": [],
                "after_disassembly": [],
            }
            if offset is not None and length:
                self._add_disassembly_audit(
                    workspace,
                    entry,
                    operation.relative_path,
                    offset,
                    length,
                    abi,
                    record,
                )
            result.append(record)
        return result

    @staticmethod
    def _add_disassembly_audit(
        workspace, entry, relative_path, offset, length, abi, record
    ) -> None:
        try:
            from noir.infrastructure.native.adapter import disassemble_range, range_hash

            backup = workspace.changes_dir / "journal" / entry["backup"]
            current = (
                workspace.changes_dir / "journal" / entry["after_backup"]
                if entry.get("after_backup")
                else workspace.safe_path(relative_path)
            )
            record["before_disassembly"] = disassemble_range(
                backup,
                offset,
                length,
                abi=abi,
            )
            record["after_disassembly"] = disassemble_range(
                current,
                offset,
                length,
                abi=abi,
            )
            record["native_range_postimage_hash"] = range_hash(current, offset, length)
        except Exception as exc:
            record["disassembly_note"] = f"Unavailable: {exc}"

    def generate_json(self, project_id: str) -> str:
        """Generate JSON audit report."""
        data = self.generate(project_id)
        return json.dumps(data, indent=2, default=str)

    def generate_markdown(self, project_id: str) -> str:
        """Generate Markdown audit report."""
        data = self.generate(project_id)
        proj = data["project"]
        inp = data["input"]

        lines: list[str] = []
        lines.append("# NOIR Audit Report")
        lines.append("")
        lines.append(f"**Generated:** {data['generated_at']}")
        lines.append(f"**Project ID:** {proj['id']}")
        lines.append(f"**Status:** {proj['status']}")
        lines.append("")

        lines.append("## Original APK")
        lines.append("")
        lines.append("| Property | Value |")
        lines.append("|----------|-------|")
        lines.append(f"| Filename | {proj['original_filename']} |")
        lines.append(f"| Size | {proj['original_size']:,} bytes |")
        lines.append(f"| SHA-256 | `{proj['original_sha256']}` |")
        lines.append(f"| Package | {inp['package_name']} |")
        lines.append(f"| Version | {inp['version_name']} ({inp['version_code']}) |")
        lines.append(f"| Min SDK | {inp['min_sdk']} |")
        lines.append(f"| Target SDK | {inp['target_sdk']} |")
        lines.append(f"| Runtime | {inp['runtime']} |")
        authorized = "Acknowledged" if proj["authorization_acknowledged"] else "Not acknowledged"
        lines.append(f"| Authorization | {authorized} |")
        lines.append("")

        if inp["permissions"]:
            lines.append("### Permissions")
            lines.append("")
            for perm in inp["permissions"]:
                lines.append(f"- `{perm}`")
            lines.append("")

        if data["plans"]:
            lines.append("## Change Plans")
            lines.append("")
            for plan in data["plans"]:
                lines.append(f"### Plan: {plan['plan_id']}")
                lines.append("")
                lines.append(f"- **Request:** {plan['user_request']}")
                lines.append(f"- **Provider:** {plan.get('provider', 'N/A')}")
                lines.append(f"- **Outcome:** {plan['intended_outcome']}")
                lines.append(f"- **Hash:** `{plan['plan_hash'][:32]}...`")
                if plan["permission_changes"]:
                    lines.append(
                        f"- **Permission changes:** {', '.join(plan['permission_changes'])}"
                    )
                if plan["risks"]:
                    lines.append(f"- **Risks:** {', '.join(plan['risks'])}")
                if plan["binary_targets"]:
                    lines.append(f"- **Binary targets:** {', '.join(plan['binary_targets'])}")
                if plan["binary_risks"]:
                    lines.append(f"- **Binary risks:** {', '.join(plan['binary_risks'])}")
                lines.append(
                    f"- **Discovery API calls:** {plan.get('discovery_api_calls', 0)}"
                )
                if plan.get("discovery_stop_reason"):
                    lines.append(
                        f"- **Discovery stop reason:** {plan['discovery_stop_reason']}"
                    )
                discovery = plan.get("discovery_transcript", [])
                if discovery:
                    lines.append("")
                    lines.append("#### Evidence Gathered")
                    lines.append("")
                    lines.append("| Tool | Arguments | Bytes | Summary |")
                    lines.append("|------|-----------|-------|---------|")
                    for record in discovery:
                        tool = record.get("tool_name", "?")
                        args = json.dumps(record.get("arguments", {}), separators=(",", ":"))
                        if len(args) > 60:
                            args = args[:57] + "..."
                        nbytes = record.get("bytes_returned", 0)
                        summary = record.get("result_summary", "")[:80]
                        lines.append(f"| {tool} | `{args}` | {nbytes:,} | {summary} |")
                else:
                    lines.append("- **Evidence selection:** static (no discovery)")
                lines.append("")

        binary_operations = [
            operation
            for patch in data["patches"]
            for operation in patch.get("binary_operations", [])
        ]
        if binary_operations:
            lines.extend(["## Binary Patches", ""])
            for operation in binary_operations:
                lines.append(
                    f"- `{operation['operation']}` → `{operation['target']}`"
                    + (f" ({operation['abi']})" if operation.get("abi") else "")
                )
                lines.append(
                    f"  - Preimage: `{operation.get('whole_file_preimage_hash') or 'N/A'}`"
                )
                lines.append(
                    f"  - Postimage: `{operation.get('whole_file_postimage_hash') or 'N/A'}`"
                )
                if operation.get("before_disassembly"):
                    lines.append("  - Instruction diff recorded in the JSON audit report")
            lines.append("")

        if data["manual_sessions"]:
            lines.extend(["## Manual Edits", ""])
            for session in data["manual_sessions"]:
                state = "Active — not recorded" if session["active"] else "Recorded"
                lines.append(f"### Session: {session['session_id']}")
                lines.append("")
                lines.append(f"- **Status:** {state}")
                lines.append(f"- **Starting revision:** {session['workspace_revision_start']}")
                if session["message"]:
                    lines.append(f"- **Note:** {session['message']}")
                for path in session["changed_files"]:
                    lines.append(f"- **Changed file:** `{path}`")
                lines.append("")

        if data["approvals"]:
            lines.append("## Approvals")
            lines.append("")
            lines.append("| Scope | Target | Status | Actor | Time |")
            lines.append("|-------|--------|--------|-------|------|")
            for a in data["approvals"]:
                lines.append(
                    f"| {a['scope']} | {a['target_id'][:12]}... | "
                    f"{a['status']} | {a['actor']} | {a['timestamp']} |"
                )
            lines.append("")

        if data["validation"]:
            v = data["validation"]
            lines.append("## Validation")
            lines.append("")
            lines.append(f"- **Passed:** {'✅ Yes' if v['passed'] else '❌ No'}")
            lines.append(f"- **Errors:** {v['error_count']}")
            lines.append(f"- **Warnings:** {v['warning_count']}")
            lines.append("")

        if data["builds"]:
            lines.append("## Builds")
            lines.append("")
            for b in data["builds"]:
                lines.append(f"### Build: {b['build_id']}")
                lines.append("")
                lines.append(f"- **Success:** {'✅' if b['success'] else '❌'}")
                if b["error"]:
                    lines.append(f"- **Error:** {b['error']}")
                lines.append(f"- **APKTool version:** {b.get('apktool_version', 'N/A')}")
                if b.get("unsigned_hash"):
                    lines.append(f"- **Unsigned hash:** `{b['unsigned_hash'][:32]}...`")
                if b.get("signed_hash"):
                    lines.append(f"- **Signed hash:** `{b['signed_hash'][:32]}...`")
                lines.append("")

        if data["warnings"]:
            lines.append("## Warnings")
            lines.append("")
            for w in data["warnings"]:
                lines.append(f"- ⚠️ {w}")
            lines.append("")

        lines.append("---")
        lines.append("*Report generated by NOIR v0.1.0*")

        return "\n".join(lines)

    def save_reports(self, project_id: str) -> dict[str, str]:
        """Save both JSON and Markdown reports to the project workspace."""
        workspace = ProjectWorkspace(project_id, self.config)
        reports_dir = workspace.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)

        json_path = reports_dir / "audit_report.json"
        md_path = reports_dir / "audit_report.md"

        json_content = self.generate_json(project_id)
        md_content = self.generate_markdown(project_id)

        json_path.write_text(json_content)
        md_path.write_text(md_content)

        return {
            "json": str(json_path),
            "markdown": str(md_path),
        }

    def _redact(self, text: str) -> str:
        """Redact potential secrets from text."""
        # Redact things that look like API keys, passwords, tokens
        text = re.sub(
            r"(?i)(password|passwd|secret|token|key)\s*[:=]\s*\S+", r"\1=***REDACTED***", text
        )
        text = re.sub(r"AIza[A-Za-z0-9_-]{35}", "***REDACTED_KEY***", text)
        return text
