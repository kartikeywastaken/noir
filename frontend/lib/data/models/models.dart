/// API data models matching backend Pydantic schemas.
///
/// Each class has fromJson/toJson for serialization.

// ── Health ────────────────────────────────────────────────────
class HealthResponse {
  final String status;
  final String version;
  final Capabilities capabilities;

  HealthResponse({required this.status, required this.version, required this.capabilities});

  factory HealthResponse.fromJson(Map<String, dynamic> json) => HealthResponse(
        status: json['status'] as String? ?? 'unknown',
        version: json['version'] as String? ?? '',
        capabilities: Capabilities.fromJson(json['capabilities'] as Map<String, dynamic>? ?? {}),
      );
}

class Capabilities {
  final bool import_;
  final bool build;
  final bool ai;
  final bool device;

  Capabilities({required this.import_, required this.build, required this.ai, required this.device});

  factory Capabilities.fromJson(Map<String, dynamic> json) => Capabilities(
        import_: json['import'] as bool? ?? false,
        build: json['build'] as bool? ?? false,
        ai: json['ai'] as bool? ?? false,
        device: json['device'] as bool? ?? false,
      );
}

// ── Project ──────────────────────────────────────────────────
class ProjectInfo {
  final String id;
  final String status;
  final String originalFilename;
  final int originalSize;
  final String originalSha256;
  final String packageName;
  final String versionName;
  final String versionCode;
  final int workspaceRevision;
  final DateTime createdAt;
  final DateTime updatedAt;

  ProjectInfo({
    required this.id,
    required this.status,
    required this.originalFilename,
    required this.originalSize,
    required this.originalSha256,
    required this.packageName,
    required this.versionName,
    required this.versionCode,
    required this.workspaceRevision,
    required this.createdAt,
    required this.updatedAt,
  });

  factory ProjectInfo.fromJson(Map<String, dynamic> json) => ProjectInfo(
        id: json['id'] as String? ?? '',
        status: json['status'] as String? ?? 'created',
        originalFilename: json['original_filename'] as String? ?? '',
        originalSize: json['original_size'] as int? ?? 0,
        originalSha256: json['original_sha256'] as String? ?? '',
        packageName: json['package_name'] as String? ?? '',
        versionName: json['version_name'] as String? ?? '',
        versionCode: json['version_code'] as String? ?? '',
        workspaceRevision: json['workspace_revision'] as int? ?? 0,
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? '') ?? DateTime.now(),
        updatedAt: DateTime.tryParse(json['updated_at'] as String? ?? '') ?? DateTime.now(),
      );
}

// ── Analysis ─────────────────────────────────────────────────
class AnalysisResult {
  final String projectId;
  final String packageName;
  final String versionName;
  final int? minSdk;
  final int? targetSdk;
  final List<String> permissions;
  final List<ComponentInfo> components;
  final bool multidex;
  final List<String> obfuscationIndicators;
  final List<String> compatibilityWarnings;

  AnalysisResult({
    required this.projectId,
    required this.packageName,
    required this.versionName,
    this.minSdk,
    this.targetSdk,
    required this.permissions,
    required this.components,
    required this.multidex,
    required this.obfuscationIndicators,
    required this.compatibilityWarnings,
  });

  factory AnalysisResult.fromJson(Map<String, dynamic> json) => AnalysisResult(
        projectId: json['project_id'] as String? ?? '',
        packageName: json['package_name'] as String? ?? '',
        versionName: json['version_name'] as String? ?? '',
        minSdk: json['min_sdk'] as int?,
        targetSdk: json['target_sdk'] as int?,
        permissions: List<String>.from(json['permissions'] ?? []),
        components: (json['components'] as List? ?? [])
            .map((c) => ComponentInfo.fromJson(c as Map<String, dynamic>))
            .toList(),
        multidex: json['multidex'] as bool? ?? false,
        obfuscationIndicators: List<String>.from(json['obfuscation_indicators'] ?? []),
        compatibilityWarnings: List<String>.from(json['compatibility_warnings'] ?? []),
      );
}

class ComponentInfo {
  final String name;
  final String componentType;
  final bool? exported;
  final bool isLauncher;

  ComponentInfo({
    required this.name,
    required this.componentType,
    this.exported,
    required this.isLauncher,
  });

  factory ComponentInfo.fromJson(Map<String, dynamic> json) => ComponentInfo(
        name: json['name'] as String? ?? '',
        componentType: json['component_type'] as String? ?? '',
        exported: json['exported'] as bool?,
        isLauncher: json['is_launcher'] as bool? ?? false,
      );
}

// ── File listing ─────────────────────────────────────────────
class FileEntry {
  final String name;
  final String path;
  final bool isDirectory;
  final int? size;

  FileEntry({
    required this.name,
    required this.path,
    required this.isDirectory,
    this.size,
  });

  factory FileEntry.fromJson(Map<String, dynamic> json) => FileEntry(
        name: json['name'] as String? ?? '',
        path: json['path'] as String? ?? json['relative_path'] as String? ?? '',
        isDirectory: json['is_directory'] as bool? ?? json['is_dir'] as bool? ?? false,
        size: json['size'] as int?,
      );
}

// ── Change Plan ──────────────────────────────────────────────
class ChangePlan {
  final String planId;
  final String projectId;
  final int workspaceRevision;
  final String userRequest;
  final String intendedOutcome;
  final List<PlanFileChange> fileChanges;
  final List<String> manifestChanges;
  final List<String> permissionChanges;
  final List<String> componentChanges;
  final List<String> behavioralChanges;
  final List<String> risks;
  final List<String> compatibilityConcerns;
  final List<String> validationSteps;
  final List<String> unsupportedAspects;
  final String? provider;
  final String? model;
  final String planHash;

  ChangePlan({
    required this.planId,
    required this.projectId,
    required this.workspaceRevision,
    required this.userRequest,
    required this.intendedOutcome,
    required this.fileChanges,
    required this.manifestChanges,
    required this.permissionChanges,
    required this.componentChanges,
    required this.behavioralChanges,
    required this.risks,
    required this.compatibilityConcerns,
    required this.validationSteps,
    required this.unsupportedAspects,
    this.provider,
    this.model,
    required this.planHash,
  });

  factory ChangePlan.fromJson(Map<String, dynamic> json) => ChangePlan(
        planId: json['plan_id'] as String? ?? '',
        projectId: json['project_id'] as String? ?? '',
        workspaceRevision: json['workspace_revision'] as int? ?? 0,
        userRequest: json['user_request'] as String? ?? '',
        intendedOutcome: json['intended_outcome'] as String? ?? '',
        fileChanges: (json['file_changes'] as List? ?? [])
            .map((c) => PlanFileChange.fromJson(c as Map<String, dynamic>))
            .toList(),
        manifestChanges: List<String>.from(json['manifest_changes'] ?? []),
        permissionChanges: List<String>.from(json['permission_changes'] ?? []),
        componentChanges: List<String>.from(json['component_changes'] ?? []),
        behavioralChanges: List<String>.from(json['behavioral_changes'] ?? []),
        risks: List<String>.from(json['risks'] ?? []),
        compatibilityConcerns: List<String>.from(json['compatibility_concerns'] ?? []),
        validationSteps: List<String>.from(json['validation_steps'] ?? []),
        unsupportedAspects: List<String>.from(json['unsupported_aspects'] ?? []),
        provider: json['provider'] as String?,
        model: json['model'] as String?,
        planHash: json['plan_hash'] as String? ?? '',
      );
}

class PlanFileChange {
  final String relativePath;
  final String operation;
  final String description;

  PlanFileChange({
    required this.relativePath,
    required this.operation,
    required this.description,
  });

  factory PlanFileChange.fromJson(Map<String, dynamic> json) => PlanFileChange(
        relativePath: json['relative_path'] as String? ?? '',
        operation: json['operation'] as String? ?? '',
        description: json['description'] as String? ?? '',
      );
}

// ── Patch ────────────────────────────────────────────────────
class PatchSet {
  final String patchId;
  final String planId;
  final String projectId;
  final int workspaceRevision;
  final String provenance;
  final List<PatchOperation> operations;
  final String patchHash;

  PatchSet({
    required this.patchId,
    required this.planId,
    required this.projectId,
    required this.workspaceRevision,
    required this.provenance,
    required this.operations,
    required this.patchHash,
  });

  factory PatchSet.fromJson(Map<String, dynamic> json) => PatchSet(
        patchId: json['patch_id'] as String? ?? '',
        planId: json['plan_id'] as String? ?? '',
        projectId: json['project_id'] as String? ?? '',
        workspaceRevision: json['workspace_revision'] as int? ?? 0,
        provenance: json['provenance'] as String? ?? '',
        operations: (json['operations'] as List? ?? [])
            .map((o) => PatchOperation.fromJson(o as Map<String, dynamic>))
            .toList(),
        patchHash: json['patch_hash'] as String? ?? '',
      );
}

class PatchOperation {
  final String relativePath;
  final String operation;
  final String? matchContent;
  final String? newContent;

  PatchOperation({
    required this.relativePath,
    required this.operation,
    this.matchContent,
    this.newContent,
  });

  factory PatchOperation.fromJson(Map<String, dynamic> json) => PatchOperation(
        relativePath: json['relative_path'] as String? ?? '',
        operation: json['operation'] as String? ?? '',
        matchContent: json['match_content'] as String?,
        newContent: json['new_content'] as String?,
      );
}

class PatchDiffEntry {
  final String path;
  final String operation;
  final String? before;
  final String? after;

  PatchDiffEntry({required this.path, required this.operation, this.before, this.after});

  factory PatchDiffEntry.fromJson(Map<String, dynamic> json) => PatchDiffEntry(
        path: json['path'] as String? ?? json['relative_path'] as String? ?? '',
        operation: json['operation'] as String? ?? '',
        before: json['before'] as String?,
        after: json['after'] as String?,
      );
}

// ── Validation ───────────────────────────────────────────────
class ValidationResult {
  final String validationId;
  final String projectId;
  final int workspaceRevision;
  final bool passed;
  final int errorCount;
  final int warningCount;
  final List<ValidationFinding> findings;

  ValidationResult({
    required this.validationId,
    required this.projectId,
    required this.workspaceRevision,
    required this.passed,
    required this.errorCount,
    required this.warningCount,
    required this.findings,
  });

  factory ValidationResult.fromJson(Map<String, dynamic> json) => ValidationResult(
        validationId: json['validation_id'] as String? ?? '',
        projectId: json['project_id'] as String? ?? '',
        workspaceRevision: json['workspace_revision'] as int? ?? 0,
        passed: json['passed'] as bool? ?? true,
        errorCount: json['error_count'] as int? ?? 0,
        warningCount: json['warning_count'] as int? ?? 0,
        findings: (json['findings'] as List? ?? [])
            .map((f) => ValidationFinding.fromJson(f as Map<String, dynamic>))
            .toList(),
      );
}

class ValidationFinding {
  final String checkName;
  final String severity;
  final String message;
  final String? filePath;

  ValidationFinding({
    required this.checkName,
    required this.severity,
    required this.message,
    this.filePath,
  });

  factory ValidationFinding.fromJson(Map<String, dynamic> json) => ValidationFinding(
        checkName: json['check_name'] as String? ?? '',
        severity: json['severity'] as String? ?? 'info',
        message: json['message'] as String? ?? '',
        filePath: json['file_path'] as String?,
      );
}

// ── Build ────────────────────────────────────────────────────
class BuildResult {
  final String buildId;
  final String projectId;
  final int workspaceRevision;
  final String? unsignedApkPath;
  final String? unsignedApkHash;
  final String? signedApkPath;
  final String? signedApkHash;
  final bool success;
  final String? errorMessage;

  BuildResult({
    required this.buildId,
    required this.projectId,
    required this.workspaceRevision,
    this.unsignedApkPath,
    this.unsignedApkHash,
    this.signedApkPath,
    this.signedApkHash,
    required this.success,
    this.errorMessage,
  });

  factory BuildResult.fromJson(Map<String, dynamic> json) => BuildResult(
        buildId: json['build_id'] as String? ?? '',
        projectId: json['project_id'] as String? ?? '',
        workspaceRevision: json['workspace_revision'] as int? ?? 0,
        unsignedApkPath: json['unsigned_apk_path'] as String?,
        unsignedApkHash: json['unsigned_apk_hash'] as String?,
        signedApkPath: json['signed_apk_path'] as String?,
        signedApkHash: json['signed_apk_hash'] as String?,
        success: json['success'] as bool? ?? false,
        errorMessage: json['error_message'] as String?,
      );
}

// ── Job ──────────────────────────────────────────────────────
class JobInfo {
  final String jobId;
  final String projectId;
  final String stage;
  final String state;
  final String? errorMessage;
  final Map<String, dynamic> resultData;
  final DateTime createdAt;

  JobInfo({
    required this.jobId,
    required this.projectId,
    required this.stage,
    required this.state,
    this.errorMessage,
    required this.resultData,
    required this.createdAt,
  });

  bool get isTerminal =>
      state == 'succeeded' || state == 'failed' || state == 'cancelled' || state == 'interrupted';

  factory JobInfo.fromJson(Map<String, dynamic> json) => JobInfo(
        jobId: json['job_id'] as String? ?? '',
        projectId: json['project_id'] as String? ?? '',
        stage: json['stage'] as String? ?? '',
        state: json['state'] as String? ?? 'queued',
        errorMessage: json['error_message'] as String?,
        resultData: Map<String, dynamic>.from(json['result_data'] as Map? ?? {}),
        createdAt: DateTime.tryParse(json['created_at'] as String? ?? '') ?? DateTime.now(),
      );
}

// ── Event ────────────────────────────────────────────────────
class AuditEvent {
  final String eventId;
  final String projectId;
  final String? jobId;
  final String? stage;
  final String severity;
  final String message;
  final DateTime timestamp;

  AuditEvent({
    required this.eventId,
    required this.projectId,
    this.jobId,
    this.stage,
    required this.severity,
    required this.message,
    required this.timestamp,
  });

  factory AuditEvent.fromJson(Map<String, dynamic> json) => AuditEvent(
        eventId: json['event_id'] as String? ?? '',
        projectId: json['project_id'] as String? ?? '',
        jobId: json['job_id'] as String?,
        stage: json['stage'] as String?,
        severity: json['severity'] as String? ?? 'info',
        message: json['message'] as String? ?? '',
        timestamp: DateTime.tryParse(json['timestamp'] as String? ?? '') ?? DateTime.now(),
      );
}

// ── Signing ──────────────────────────────────────────────────
class SigningProfile {
  final String name;
  final String type;

  SigningProfile({required this.name, required this.type});

  factory SigningProfile.fromJson(Map<String, dynamic> json) => SigningProfile(
        name: json['name'] as String? ?? '',
        type: json['type'] as String? ?? '',
      );
}

// ── Approval ─────────────────────────────────────────────────
class ApprovalRecord {
  final String approvalId;
  final String projectId;
  final String scope;
  final int workspaceRevision;
  final String targetHash;
  final String targetId;

  ApprovalRecord({
    required this.approvalId,
    required this.projectId,
    required this.scope,
    required this.workspaceRevision,
    required this.targetHash,
    required this.targetId,
  });

  factory ApprovalRecord.fromJson(Map<String, dynamic> json) => ApprovalRecord(
        approvalId: json['approval_id'] as String? ?? '',
        projectId: json['project_id'] as String? ?? '',
        scope: json['scope'] as String? ?? '',
        workspaceRevision: json['workspace_revision'] as int? ?? 0,
        targetHash: json['target_hash'] as String? ?? '',
        targetId: json['target_id'] as String? ?? '',
      );
}

// ── Manual Session ───────────────────────────────────────────
class ManualSession {
  final bool active;
  final String? sessionId;
  final String? projectId;
  final int? workspaceRevisionStart;

  ManualSession({
    required this.active,
    this.sessionId,
    this.projectId,
    this.workspaceRevisionStart,
  });

  factory ManualSession.fromJson(Map<String, dynamic> json) {
    final sessionData = json['session'] as Map<String, dynamic>?;
    return ManualSession(
      active: json['active'] as bool? ?? false,
      sessionId: sessionData?['session_id'] as String?,
      projectId: sessionData?['project_id'] as String?,
      workspaceRevisionStart: sessionData?['workspace_revision_start'] as int?,
    );
  }
}
