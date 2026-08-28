"""NOIR data access repositories."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from noir.domain.enums import ApprovalScope, ApprovalStatus, JobState
from noir.domain.models import (
    AnalysisResult,
    ApiToken,
    ApprovalRecord,
    AuditEvent,
    BuildResult,
    ChangePlan,
    FileManifestEntry,
    JobInfo,
    ManualEditSession,
    PatchSet,
    ProjectInfo,
    SigningProfile,
    ValidationResult,
)
from noir.infrastructure.database.engine import (
    AnalysisRow,
    ApprovalRow,
    BuildRow,
    EventRow,
    FileManifestRow,
    JobRow,
    ManualSessionRow,
    PatchRow,
    PlanRow,
    ProjectRow,
    SigningProfileRow,
    TokenRow,
    ValidationRow,
    get_session,
)


class ProjectRepository:
    """Data access for projects."""

    def create(self, project: ProjectInfo) -> ProjectInfo:
        with get_session() as session:
            row = ProjectRow(
                id=project.id,
                status=project.status.value,
                original_filename=project.original_filename,
                original_size=project.original_size,
                original_sha256=project.original_sha256,
                package_name=project.package_name,
                version_name=project.version_name,
                version_code=project.version_code,
                authorization_acknowledged=project.authorization_acknowledged,
                authorization_timestamp=project.authorization_timestamp,
                workspace_revision=project.workspace_revision,
                dirty=project.dirty,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            session.add(row)
            session.commit()
        return project

    def get(self, project_id: str) -> ProjectInfo | None:
        with get_session() as session:
            row = session.get(ProjectRow, project_id)
            if not row:
                return None
            return self._to_model(row)

    def update(self, project: ProjectInfo) -> None:
        with get_session() as session:
            row = session.get(ProjectRow, project.id)
            if not row:
                return
            row.status = project.status.value
            row.original_filename = project.original_filename
            row.original_size = project.original_size
            row.original_sha256 = project.original_sha256
            row.package_name = project.package_name
            row.version_name = project.version_name
            row.version_code = project.version_code
            row.authorization_acknowledged = project.authorization_acknowledged
            row.authorization_timestamp = project.authorization_timestamp
            row.workspace_revision = project.workspace_revision
            row.dirty = project.dirty
            row.updated_at = datetime.now(UTC)
            session.commit()

    def list_all(self) -> list[ProjectInfo]:
        with get_session() as session:
            rows = session.query(ProjectRow).order_by(ProjectRow.created_at.desc()).all()
            return [self._to_model(r) for r in rows]

    def _to_model(self, row: ProjectRow) -> ProjectInfo:
        from noir.domain.enums import ProjectStatus

        return ProjectInfo(
            id=row.id,
            status=ProjectStatus(row.status),
            original_filename=row.original_filename or "",
            original_size=row.original_size or 0,
            original_sha256=row.original_sha256 or "",
            package_name=row.package_name or "",
            version_name=row.version_name or "",
            version_code=row.version_code or "",
            authorization_acknowledged=row.authorization_acknowledged or False,
            authorization_timestamp=row.authorization_timestamp,
            workspace_revision=row.workspace_revision or 0,
            dirty=row.dirty or False,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class JobRepository:
    """Data access for jobs."""

    def create(self, job: JobInfo) -> JobInfo:
        with get_session() as session:
            row = JobRow(
                job_id=job.job_id,
                project_id=job.project_id,
                stage=job.stage.value,
                state=job.state.value,
                attempt=job.attempt,
                max_attempts=job.max_attempts,
                error_message=job.error_message,
                result_data=job.result_data,
                created_at=job.created_at,
                updated_at=job.updated_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )
            session.add(row)
            session.commit()
        return job

    def get(self, job_id: str) -> JobInfo | None:
        with get_session() as session:
            row = session.get(JobRow, job_id)
            if not row:
                return None
            return self._to_model(row)

    def update(self, job: JobInfo) -> None:
        with get_session() as session:
            row = session.get(JobRow, job.job_id)
            if not row:
                return
            previous = row.result_data or {}
            if previous.get("cancel_requested"):
                job.result_data["cancel_requested"] = True
                if job.state in (JobState.SUCCEEDED, JobState.FAILED):
                    job.state = JobState.CANCELLED
            row.state = job.state.value
            row.stage = job.stage.value
            row.attempt = job.attempt
            row.error_message = job.error_message
            row.result_data = job.result_data
            row.updated_at = datetime.now(UTC)
            row.started_at = job.started_at
            row.finished_at = job.finished_at
            session.commit()

    def request_cancel(self, job_id: str) -> JobInfo:
        with get_session() as session:
            row = session.get(JobRow, job_id)
            if row is None:
                raise ValueError("Job not found")
            if row.state in ("succeeded", "failed", "cancelled", "interrupted"):
                raise ValueError("Job is already terminal")
            row.result_data = {**(row.result_data or {}), "cancel_requested": True}
            if row.state == "queued":
                row.state = "cancelled"
                row.finished_at = datetime.now(UTC)
            session.commit()
            return self._to_model(row)

    def list_by_project(self, project_id: str) -> list[JobInfo]:
        with get_session() as session:
            rows = (
                session.query(JobRow)
                .filter(JobRow.project_id == project_id)
                .order_by(JobRow.created_at.desc())
                .all()
            )
            return [self._to_model(r) for r in rows]

    def list_all(self) -> list[JobInfo]:
        with get_session() as session:
            rows = session.query(JobRow).order_by(JobRow.created_at.desc()).all()
            return [self._to_model(r) for r in rows]

    def _to_model(self, row: JobRow) -> JobInfo:
        from noir.domain.enums import WorkflowStage

        return JobInfo(
            job_id=row.job_id,
            project_id=row.project_id,
            stage=WorkflowStage(row.stage),
            state=JobState(row.state),
            attempt=row.attempt or 1,
            max_attempts=row.max_attempts or 3,
            error_message=row.error_message,
            result_data=row.result_data or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


class EventRepository:
    """Data access for audit events."""

    def create(self, evt: AuditEvent) -> AuditEvent:
        with get_session() as session:
            row = EventRow(
                event_id=evt.event_id,
                project_id=evt.project_id,
                job_id=evt.job_id,
                stage=evt.stage.value if evt.stage else None,
                severity=evt.severity.value,
                message=evt.message,
                metadata_json=json.dumps(evt.metadata),
                timestamp=evt.timestamp,
            )
            session.add(row)
            session.commit()
        return evt

    def list_by_project(
        self, project_id: str, after_id: str | None = None, limit: int = 100
    ) -> list[AuditEvent]:
        with get_session() as session:
            query = session.query(EventRow).filter(EventRow.project_id == project_id)
            if after_id:
                ref = session.get(EventRow, after_id)
                if ref:
                    query = query.filter(EventRow.timestamp > ref.timestamp)
            rows = query.order_by(EventRow.timestamp.asc()).limit(limit).all()
            return [self._to_model(r) for r in rows]

    def list_by_job(self, job_id: str) -> list[AuditEvent]:
        with get_session() as session:
            rows = (
                session.query(EventRow)
                .filter(EventRow.job_id == job_id)
                .order_by(EventRow.timestamp.asc())
                .all()
            )
            return [self._to_model(r) for r in rows]

    def _to_model(self, row: EventRow) -> AuditEvent:
        from noir.domain.enums import EventSeverity, WorkflowStage

        return AuditEvent(
            event_id=row.event_id,
            project_id=row.project_id,
            job_id=row.job_id,
            stage=WorkflowStage(row.stage) if row.stage else None,
            severity=EventSeverity(row.severity),
            message=row.message,
            metadata=json.loads(row.metadata_json) if row.metadata_json else {},
            timestamp=row.timestamp,
        )


class ApprovalRepository:
    """Data access for approvals."""

    def create(self, approval: ApprovalRecord) -> ApprovalRecord:
        with get_session() as session:
            row = ApprovalRow(
                approval_id=approval.approval_id,
                project_id=approval.project_id,
                scope=approval.scope.value,
                workspace_revision=approval.workspace_revision,
                target_hash=approval.target_hash,
                target_id=approval.target_id,
                status=approval.status.value,
                actor=approval.actor,
                risk_acknowledgments=approval.risk_acknowledgments,
                created_at=approval.created_at,
            )
            session.add(row)
            session.commit()
        return approval

    def find_valid(
        self,
        project_id: str,
        scope: ApprovalScope,
        target_hash: str,
        workspace_revision: int,
    ) -> ApprovalRecord | None:
        with get_session() as session:
            row = (
                session.query(ApprovalRow)
                .filter(
                    ApprovalRow.project_id == project_id,
                    ApprovalRow.scope == scope.value,
                    ApprovalRow.target_hash == target_hash,
                    ApprovalRow.workspace_revision == workspace_revision,
                    ApprovalRow.status == ApprovalStatus.APPROVED.value,
                )
                .first()
            )
            if not row:
                return None
            return self._to_model(row)

    def invalidate_for_project(self, project_id: str, scope: ApprovalScope) -> int:
        """Mark all approvals of given scope as stale."""
        with get_session() as session:
            count = (
                session.query(ApprovalRow)
                .filter(
                    ApprovalRow.project_id == project_id,
                    ApprovalRow.scope == scope.value,
                    ApprovalRow.status == ApprovalStatus.APPROVED.value,
                )
                .update({"status": ApprovalStatus.STALE.value})
            )
            session.commit()
            return count

    def list_by_project(self, project_id: str) -> list[ApprovalRecord]:
        with get_session() as session:
            rows = (
                session.query(ApprovalRow)
                .filter(ApprovalRow.project_id == project_id)
                .order_by(ApprovalRow.created_at.desc())
                .all()
            )
            return [self._to_model(r) for r in rows]

    def _to_model(self, row: ApprovalRow) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=row.approval_id,
            project_id=row.project_id,
            scope=ApprovalScope(row.scope),
            workspace_revision=row.workspace_revision,
            target_hash=row.target_hash,
            target_id=row.target_id,
            status=ApprovalStatus(row.status),
            actor=row.actor or "local_cli",
            risk_acknowledgments=row.risk_acknowledgments or [],
            created_at=row.created_at,
        )


class PlanRepository:
    """Data access for change plans."""

    def create(self, plan: ChangePlan) -> ChangePlan:
        with get_session() as session:
            row = PlanRow(
                plan_id=plan.plan_id,
                project_id=plan.project_id,
                workspace_revision=plan.workspace_revision,
                user_request=plan.user_request,
                plan_json=plan.model_dump_json(),
                plan_hash=plan.compute_hash(),
                provider=plan.provider,
                model=plan.model,
                created_at=plan.created_at,
            )
            session.add(row)
            session.commit()
        return plan

    def get(self, plan_id: str) -> ChangePlan | None:
        with get_session() as session:
            row = session.get(PlanRow, plan_id)
            if not row:
                return None
            return ChangePlan.model_validate_json(row.plan_json)

    def get_hash(self, plan_id: str) -> str | None:
        with get_session() as session:
            row = session.get(PlanRow, plan_id)
            return row.plan_hash if row else None

    def list_by_project(self, project_id: str) -> list[ChangePlan]:
        with get_session() as session:
            rows = (
                session.query(PlanRow)
                .filter(PlanRow.project_id == project_id)
                .order_by(PlanRow.created_at.desc())
                .all()
            )
            return [ChangePlan.model_validate_json(r.plan_json) for r in rows]


class PatchRepository:
    """Data access for patch sets."""

    def create(self, patch: PatchSet) -> PatchSet:
        with get_session() as session:
            row = PatchRow(
                patch_id=patch.patch_id,
                plan_id=patch.plan_id,
                project_id=patch.project_id,
                workspace_revision=patch.workspace_revision,
                provenance=patch.provenance.value,
                patch_json=patch.model_dump_json(),
                patch_hash=patch.compute_hash(),
                applied=False,
                undone=False,
                created_at=patch.created_at,
            )
            session.add(row)
            session.commit()
        return patch

    def get(self, patch_id: str) -> PatchSet | None:
        with get_session() as session:
            row = session.get(PatchRow, patch_id)
            if not row:
                return None
            return PatchSet.model_validate_json(row.patch_json)

    def get_hash(self, patch_id: str) -> str | None:
        with get_session() as session:
            row = session.get(PatchRow, patch_id)
            return row.patch_hash if row else None

    def mark_applied(self, patch_id: str) -> None:
        with get_session() as session:
            row = session.get(PatchRow, patch_id)
            if row:
                row.applied = True
                session.commit()

    def mark_undone(self, patch_id: str) -> None:
        with get_session() as session:
            row = session.get(PatchRow, patch_id)
            if row:
                row.undone = True
                row.applied = False
                session.commit()

    def is_applied(self, patch_id: str) -> bool:
        with get_session() as session:
            row = session.get(PatchRow, patch_id)
            return bool(row and row.applied)

    def list_by_project(self, project_id: str) -> list[PatchSet]:
        with get_session() as session:
            rows = (
                session.query(PatchRow)
                .filter(PatchRow.project_id == project_id)
                .order_by(PatchRow.created_at.desc())
                .all()
            )
            return [PatchSet.model_validate_json(r.patch_json) for r in rows]


class BuildRepository:
    """Data access for builds."""

    def create(self, build: BuildResult) -> BuildResult:
        with get_session() as session:
            row = BuildRow(
                build_id=build.build_id,
                project_id=build.project_id,
                workspace_revision=build.workspace_revision,
                unsigned_apk_path=build.unsigned_apk_path,
                unsigned_apk_hash=build.unsigned_apk_hash,
                aligned_apk_path=build.aligned_apk_path,
                aligned_apk_hash=build.aligned_apk_hash,
                signed_apk_path=build.signed_apk_path,
                signed_apk_hash=build.signed_apk_hash,
                success=build.success,
                error_message=build.error_message,
                apktool_version=build.apktool_version,
                build_tools_version=build.build_tools_version,
                tool_logs=build.tool_logs,
                created_at=build.created_at,
            )
            session.add(row)
            session.commit()
        return build

    def get(self, build_id: str) -> BuildResult | None:
        with get_session() as session:
            row = session.get(BuildRow, build_id)
            if not row:
                return None
            return self._to_model(row)

    def update(self, build: BuildResult) -> None:
        with get_session() as session:
            row = session.get(BuildRow, build.build_id)
            if not row:
                return
            row.unsigned_apk_path = build.unsigned_apk_path
            row.unsigned_apk_hash = build.unsigned_apk_hash
            row.aligned_apk_path = build.aligned_apk_path
            row.aligned_apk_hash = build.aligned_apk_hash
            row.signed_apk_path = build.signed_apk_path
            row.signed_apk_hash = build.signed_apk_hash
            row.success = build.success
            row.error_message = build.error_message
            row.tool_logs = build.tool_logs
            session.commit()

    def list_by_project(self, project_id: str) -> list[BuildResult]:
        with get_session() as session:
            rows = (
                session.query(BuildRow)
                .filter(BuildRow.project_id == project_id)
                .order_by(BuildRow.created_at.desc())
                .all()
            )
            return [self._to_model(r) for r in rows]

    def _to_model(self, row: BuildRow) -> BuildResult:
        return BuildResult(
            build_id=row.build_id,
            project_id=row.project_id,
            workspace_revision=row.workspace_revision,
            unsigned_apk_path=row.unsigned_apk_path,
            unsigned_apk_hash=row.unsigned_apk_hash,
            aligned_apk_path=row.aligned_apk_path,
            aligned_apk_hash=row.aligned_apk_hash,
            signed_apk_path=row.signed_apk_path,
            signed_apk_hash=row.signed_apk_hash,
            success=row.success or False,
            error_message=row.error_message,
            apktool_version=row.apktool_version,
            build_tools_version=row.build_tools_version,
            tool_logs=row.tool_logs or "",
            created_at=row.created_at,
        )


class SigningProfileRepository:
    """Data access for signing profiles."""

    def create(self, profile: SigningProfile) -> SigningProfile:
        with get_session() as session:
            row = SigningProfileRow(
                name=profile.name,
                profile_type=profile.profile_type.value,
                keystore_path=profile.keystore_path,
                key_alias=profile.key_alias,
                certificate_fingerprint_sha256=profile.certificate_fingerprint_sha256,
                created_at=profile.created_at,
            )
            session.merge(row)
            session.commit()
        return profile

    def get(self, name: str) -> SigningProfile | None:
        with get_session() as session:
            row = session.get(SigningProfileRow, name)
            if not row:
                return None
            return SigningProfile(
                name=row.name,
                profile_type=row.profile_type,
                keystore_path=row.keystore_path,
                key_alias=row.key_alias,
                certificate_fingerprint_sha256=row.certificate_fingerprint_sha256,
                created_at=row.created_at,
            )

    def list_all(self) -> list[SigningProfile]:
        with get_session() as session:
            rows = session.query(SigningProfileRow).all()
            return [
                SigningProfile(
                    name=r.name,
                    profile_type=r.profile_type,
                    keystore_path=r.keystore_path,
                    key_alias=r.key_alias,
                    certificate_fingerprint_sha256=r.certificate_fingerprint_sha256,
                    created_at=r.created_at,
                )
                for r in rows
            ]


class ManualSessionRepository:
    """Data access for manual edit sessions."""

    def create(self, session_model: ManualEditSession) -> ManualEditSession:
        with get_session() as session:
            row = ManualSessionRow(
                session_id=session_model.session_id,
                project_id=session_model.project_id,
                workspace_revision_start=session_model.workspace_revision_start,
                active=session_model.active,
                declared_files=session_model.declared_files,
                detected_changes=session_model.detected_changes,
                message=session_model.message,
                started_at=session_model.started_at,
                finished_at=session_model.finished_at,
            )
            session.add(row)
            session.commit()
        return session_model

    def get_active(self, project_id: str) -> ManualEditSession | None:
        with get_session() as session:
            row = (
                session.query(ManualSessionRow)
                .filter(
                    ManualSessionRow.project_id == project_id,
                    ManualSessionRow.active,
                )
                .first()
            )
            if not row:
                return None
            return self._to_model(row)

    def list_by_project(self, project_id: str) -> list[ManualEditSession]:
        with get_session() as session:
            rows = (
                session.query(ManualSessionRow)
                .filter(ManualSessionRow.project_id == project_id)
                .order_by(ManualSessionRow.started_at.desc())
                .all()
            )
            return [self._to_model(row) for row in rows]

    def finish(self, session_id: str, detected_changes: list[str], message: str) -> None:
        with get_session() as session:
            row = session.get(ManualSessionRow, session_id)
            if row:
                row.active = False
                row.detected_changes = detected_changes
                row.message = message
                row.finished_at = datetime.now(UTC)
                session.commit()

    def _to_model(self, row: ManualSessionRow) -> ManualEditSession:
        return ManualEditSession(
            session_id=row.session_id,
            project_id=row.project_id,
            workspace_revision_start=row.workspace_revision_start,
            active=row.active,
            declared_files=row.declared_files or [],
            detected_changes=row.detected_changes or [],
            message=row.message or "",
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


class AnalysisRepository:
    """Data access for analysis results."""

    def save(self, analysis: AnalysisResult) -> None:
        with get_session() as session:
            existing = (
                session.query(AnalysisRow)
                .filter(AnalysisRow.project_id == analysis.project_id)
                .first()
            )
            if existing:
                existing.analysis_json = analysis.model_dump_json()
                existing.analyzed_at = analysis.analyzed_at
            else:
                row = AnalysisRow(
                    project_id=analysis.project_id,
                    analysis_json=analysis.model_dump_json(),
                    analyzed_at=analysis.analyzed_at,
                )
                session.add(row)
            session.commit()

    def get(self, project_id: str) -> AnalysisResult | None:
        with get_session() as session:
            row = session.query(AnalysisRow).filter(AnalysisRow.project_id == project_id).first()
            if not row:
                return None
            return AnalysisResult.model_validate_json(row.analysis_json)


class TokenRepository:
    """Data access for API tokens."""

    def create(self, token: ApiToken) -> ApiToken:
        with get_session() as session:
            row = TokenRow(
                token_id=token.token_id,
                token_hash=token.token_hash,
                name=token.name,
                created_at=token.created_at,
            )
            session.add(row)
            session.commit()
        return token

    def find_by_hash(self, token_hash: str) -> ApiToken | None:
        with get_session() as session:
            row = session.query(TokenRow).filter(TokenRow.token_hash == token_hash).first()
            if not row:
                return None
            return ApiToken(
                token_id=row.token_id,
                token_hash=row.token_hash,
                name=row.name,
                created_at=row.created_at,
            )

    def list_all(self) -> list[ApiToken]:
        with get_session() as session:
            rows = session.query(TokenRow).all()
            return [
                ApiToken(
                    token_id=r.token_id,
                    token_hash=r.token_hash,
                    name=r.name,
                    created_at=r.created_at,
                )
                for r in rows
            ]


class FileManifestRepository:
    """Data access for file manifests."""

    def save(self, project_id: str, revision: int, entries: list[FileManifestEntry]) -> None:
        with get_session() as session:
            row = FileManifestRow(
                project_id=project_id,
                workspace_revision=revision,
                manifest_json=json.dumps([e.model_dump() for e in entries]),
                created_at=datetime.now(UTC),
            )
            session.add(row)
            session.commit()

    def get_latest(self, project_id: str) -> list[FileManifestEntry] | None:
        with get_session() as session:
            row = (
                session.query(FileManifestRow)
                .filter(FileManifestRow.project_id == project_id)
                .order_by(FileManifestRow.workspace_revision.desc())
                .first()
            )
            if not row:
                return None
            data = json.loads(row.manifest_json)
            return [FileManifestEntry(**e) for e in data]

    def get_by_revision(self, project_id: str, revision: int) -> list[FileManifestEntry] | None:
        with get_session() as session:
            row = (
                session.query(FileManifestRow)
                .filter(
                    FileManifestRow.project_id == project_id,
                    FileManifestRow.workspace_revision == revision,
                )
                .first()
            )
            if not row:
                return None
            data = json.loads(row.manifest_json)
            return [FileManifestEntry(**e) for e in data]


class ValidationRepository:
    """Data access for validation results."""

    def save(self, result: ValidationResult) -> None:
        with get_session() as session:
            row = ValidationRow(
                validation_id=result.validation_id,
                project_id=result.project_id,
                workspace_revision=result.workspace_revision,
                findings_json=json.dumps([f.model_dump() for f in result.findings]),
                passed=result.passed,
                error_count=result.error_count,
                warning_count=result.warning_count,
                created_at=result.created_at,
            )
            session.add(row)
            session.commit()

    def get_latest(self, project_id: str) -> ValidationResult | None:
        with get_session() as session:
            row = (
                session.query(ValidationRow)
                .filter(ValidationRow.project_id == project_id)
                .order_by(ValidationRow.created_at.desc())
                .first()
            )
            if not row:
                return None
            from noir.domain.models import ValidationFinding

            findings = [ValidationFinding(**f) for f in json.loads(row.findings_json)]
            return ValidationResult(
                validation_id=row.validation_id,
                project_id=row.project_id,
                workspace_revision=row.workspace_revision,
                findings=findings,
                passed=row.passed,
                error_count=row.error_count,
                warning_count=row.warning_count,
                created_at=row.created_at,
            )
