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
    PatchRepository,
    PlanRepository,
    ProjectRepository,
    ValidationRepository,
)
from noir.infrastructure.filesystem.workspace import ProjectWorkspace


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
                }
                for p in patches
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
