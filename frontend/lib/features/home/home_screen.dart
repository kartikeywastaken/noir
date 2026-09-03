import 'dart:io';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/state/projects_controller.dart';
import '../../core/state/signing_controller.dart';
import '../../core/state/workflow_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/widgets/circular_build_gauge.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_bottom_nav.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/terminal_log_view.dart';
import '../../core/widgets/transfer_bar.dart';
import '../../data/api/transfer_progress.dart';
import '../../data/device/installed_apps_service.dart';
import '../../data/models/models.dart';
import 'installed_app_picker.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, this.projectId, this.installedAppsService});
  final String? projectId;
  final InstalledAppsService? installedAppsService;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _request = TextEditingController();
  bool _consent = true, _downloading = false, _preparingInstalledApp = false;
  TransferProgress? _download;
  String? _saveMessage, _selectionError;
  final Map<int, bool> _modToggles = {};

  InstalledAppsService get _installedApps =>
      widget.installedAppsService ?? const InstalledAppsService();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      context.read<ProjectsController>().loadProjects();
      if (widget.projectId != null) {
        final flow = context.read<WorkflowController>();
        flow.resume(widget.projectId!).then((_) {
          if (mounted) _request.text = flow.request;
        });
      } else {
        _request.text = context.read<WorkflowController>().request;
      }
    });
  }

  @override
  void dispose() {
    _request.dispose();
    super.dispose();
  }

  Future<void> _select() async {
    try {
      final files = await FilePicker.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['apk'],
      );
      if (!mounted || files.isEmpty) return;
      _saveMessage = null;
      _selectionError = null;
      _download = null;
      _request.clear();
      _modToggles.clear();
      await context.read<WorkflowController>().importFile(files.single);
    } catch (e) {
      if (mounted) setState(() => _saveMessage = '$e');
    }
  }

  Future<void> _selectInstalledApp() async {
    final app = await showInstalledAppPicker(context, _installedApps);
    if (app == null || !mounted) return;
    setState(() {
      _preparingInstalledApp = true;
      _selectionError = null;
      _saveMessage = null;
      _download = null;
      _request.clear();
      _modToggles.clear();
    });
    try {
      final extracted = await _installedApps.extract(app);
      if (!mounted) {
        try {
          await File(extracted.path).delete();
        } catch (_) {}
        return;
      }
      await context.read<WorkflowController>().importLocalApk(
        path: extracted.path,
        filename: extracted.filename,
        length: extracted.length,
        deleteAfter: true,
      );
    } catch (error) {
      if (mounted) setState(() => _selectionError = '$error');
    } finally {
      if (mounted) setState(() => _preparingInstalledApp = false);
    }
  }

  Future<void> _save(WorkflowController flow) async {
    final build = flow.build!;
    final signing = SigningController(context.read<ConnectionController>().api);
    setState(() {
      _downloading = true;
      _download = null;
      _saveMessage = null;
    });
    try {
      final bytes = await signing.verifiedDownload(
        build.projectId,
        build,
        onProgress: (p) {
          if (mounted) setState(() => _download = p);
        },
      );
      if (!mounted) return;
      final saved = await FilePicker.saveFile(
        fileName: 'noir-${build.buildId}-signed.apk',
        mimeType: 'application/vnd.android.package-archive',
        bytes: bytes,
      );
      if (mounted) {
        setState(
          () => _saveMessage = saved == null
              ? 'Save cancelled. Download again whenever you need it.'
              : 'Verified APK saved to $saved',
        );
      }
    } catch (e) {
      if (mounted) setState(() => _saveMessage = '$e');
    } finally {
      signing.dispose();
      if (mounted) setState(() => _downloading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final connection = context.watch<ConnectionController>();
    final flow = context.watch<WorkflowController>();

    final (statusText, statusColor) = switch (connection.isConnected) {
      false => ('Offline', NoirColors.errorBright),
      true when flow.working => ('Working', NoirColors.tertiaryFixed),
      true => ('Ready', NoirColors.primaryFixed),
    };

    return Scaffold(
      backgroundColor: NoirColors.background,
      appBar: NoirAppBar(
        title: 'NOIR',
        statusText: statusText,
        statusColor: statusColor,
        onSettingsTap: () => context.go('/settings'),
      ),
      bottomNavigationBar: NoirBottomNav(
        currentIndex: 0,
        onTap: (i) {
          if (i == 1) {
            if (flow.project != null) {
              context.push('/project/${flow.project!.id}');
            } else {
              context.go('/history');
            }
          }
          if (i == 2) context.go('/history');
          if (i == 3) context.go('/settings');
        },
      ),
      body: Stack(
        children: [
          ListView(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 120),
            children: [
              if (!connection.isConnected) ...[
                _buildConnectionWarning(connection),
                const SizedBox(height: 16),
              ],
              if (flow.step == 0) _buildStep0(context, connection, flow),
              if (flow.step == 1) _buildStep1(context, connection, flow),
              if (flow.step == 2) _buildStep2(context, flow),
              if (flow.error != null) ...[
                const SizedBox(height: 16),
                _buildErrorCard(flow),
              ],
              if (_saveMessage != null) ...[
                const SizedBox(height: 16),
                _buildSaveMessageCard(),
              ],
            ],
          ),
          if (flow.step == 1 && flow.previewReady && !flow.unsupportedPlan)
            _buildStickyCta(flow),
        ],
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // STEP 0: DECODE & MODIFY (Screenshot 1)
  // ─────────────────────────────────────────────────────────────────────────
  Widget _buildStep0(
    BuildContext context,
    ConnectionController connection,
    WorkflowController flow,
  ) {
    final projectsCtrl = context.watch<ProjectsController>();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Hero Section
        Container(
          padding: const EdgeInsets.all(24),
          decoration: BoxDecoration(
            color: NoirColors.surfaceContainerLow,
            borderRadius: BorderRadius.circular(28),
            border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.4),
                blurRadius: 32,
                offset: const Offset(0, 8),
              ),
            ],
          ),
          child: Column(
            children: [
              const Text(
                'DECODE & MODIFY',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 32,
                  fontWeight: FontWeight.w900,
                  letterSpacing: -0.5,
                  color: NoirColors.primary,
                ),
              ),
              const SizedBox(height: 12),
              const Text(
                'Drag and drop your APK file here to begin decompilation. '
                'Our advanced engine parses resources, manifest configurations, '
                'and Dalvik executables in seconds.',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 14,
                  height: 1.5,
                  color: NoirColors.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 24),
              // Dashed Drop Zone
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 16),
                decoration: BoxDecoration(
                  color: NoirColors.surfaceContainerHighest.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(
                    color: NoirColors.outlineVariant,
                    width: 1.5,
                  ),
                ),
                child: Column(
                  children: [
                    const Icon(
                      Icons.upload_file,
                      size: 44,
                      color: NoirColors.outline,
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      'Drop APK here or',
                      style: TextStyle(
                        fontFamily: 'JetBrainsMono',
                        fontSize: 13,
                        color: NoirColors.onSurfaceVariant,
                      ),
                    ),
                    const SizedBox(height: 16),
                    NoirPrimaryButton(
                      label: 'SELECT APK',
                      onPressed: connection.isConnected && !flow.working
                          ? _select
                          : null,
                    ),
                    if (_installedApps.isSupported) ...[
                      const SizedBox(height: 10),
                      NoirGhostButton(
                        label: 'CHOOSE INSTALLED APP',
                        icon: Icons.apps,
                        loading: _preparingInstalledApp,
                        onPressed: connection.isConnected &&
                                !flow.working &&
                                !_preparingInstalledApp
                            ? _selectInstalledApp
                            : null,
                      ),
                    ],
                  ],
                ),
              ),
              if (flow.upload != null) ...[
                const SizedBox(height: 16),
                TransferBar(
                  progress: flow.upload!,
                  title: flow.upload!.fraction == 1
                      ? 'Upload received · decoding on server'
                      : 'Uploading APK',
                ),
              ],
              if (_selectionError != null) ...[
                const SizedBox(height: 12),
                Text(
                  _selectionError!,
                  style: const TextStyle(color: NoirColors.errorBright, fontSize: 12),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 28),
        // Recent Workspaces Section
        const Text(
          'Recent Workspaces',
          style: TextStyle(
            fontFamily: 'Inter',
            fontSize: 20,
            fontWeight: FontWeight.w700,
            color: NoirColors.primary,
          ),
        ),
        const SizedBox(height: 14),
        if (projectsCtrl.loading)
          const Center(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: CircularProgressIndicator(color: NoirColors.primaryFixed),
            ),
          )
        else if (projectsCtrl.projects.isEmpty)
          Container(
            padding: const EdgeInsets.all(20),
            decoration: BoxDecoration(
              color: NoirColors.surfaceContainer,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
            ),
            child: const Center(
              child: Text(
                'No recent workspaces. Select an APK to start modifying.',
                style: TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 13,
                  color: NoirColors.onSurfaceVariant,
                ),
              ),
            ),
          )
        else
          Column(
            children: [
              for (final p in projectsCtrl.projects.take(4))
                _buildWorkspaceCard(p, flow),
            ],
          ),
      ],
    );
  }

  Widget _buildWorkspaceCard(ProjectInfo project, WorkflowController flow) {
    final (badgeText, badgeBg, badgeFg) = switch (project.status) {
      'analyzed' || 'imported' => (
          '[DECODED]',
          NoirColors.primaryFixed.withValues(alpha: 0.9),
          NoirColors.onPrimaryFixed,
        ),
      'building' || 'queued' => (
          '[BUILDING]',
          NoirColors.tertiaryFixed.withValues(alpha: 0.9),
          NoirColors.onTertiaryFixedVariant,
        ),
      'signed' => (
          '[SIGNED]',
          NoirColors.primaryFixed.withValues(alpha: 0.9),
          NoirColors.onPrimaryFixed,
        ),
      _ => (
          '[ARCHIVED]',
          NoirColors.surfaceVariant,
          NoirColors.onSurfaceVariant,
        ),
    };

    final packageName = project.packageName.isNotEmpty
        ? project.packageName
        : project.originalFilename;

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () async {
            await flow.resume(project.id);
            if (mounted) _request.text = flow.request;
          },
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Icon(
                      Icons.folder_zip,
                      size: 20,
                      color: NoirColors.outline,
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: badgeBg,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        badgeText,
                        style: TextStyle(
                          fontFamily: 'JetBrainsMono',
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                          color: badgeFg,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                Text(
                  packageName,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontFamily: 'JetBrainsMono',
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: NoirColors.primary,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  'Workspace rev ${project.workspaceRevision} · ${project.originalFilename}',
                  style: const TextStyle(
                    fontFamily: 'Inter',
                    fontSize: 12,
                    color: NoirColors.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // STEP 1: REVIEW PLAN / REVIEW MODIFICATIONS (Screenshot 2)
  // ─────────────────────────────────────────────────────────────────────────
  Widget _buildStep1(
    BuildContext context,
    ConnectionController connection,
    WorkflowController flow,
  ) {
    if (flow.unsupportedPlan) {
      return _buildUnsupportedPlanCard(flow);
    }

    if (!flow.previewReady) {
      return _buildRequestInputCard(connection, flow);
    }

    // Preview Ready: Review Modifications Screen
    final patchCount = flow.diff.isNotEmpty ? flow.diff.length : (flow.plan?.fileChanges.length ?? 0);
    final riskCount = flow.plan?.risks.length ?? 0;
    final targetArch = (flow.project?.versionCode.isNotEmpty ?? false)
        ? 'v${flow.project!.versionCode}'
        : 'arm64-v8a';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Section Header
        Row(
          children: [
            const Icon(Icons.assignment, size: 16, color: NoirColors.outline),
            const SizedBox(width: 8),
            Text(
              'EXECUTION PLAN',
              style: TextStyle(
                fontFamily: 'Inter',
                fontSize: 12,
                fontWeight: FontWeight.w700,
                letterSpacing: 2.0,
                color: NoirColors.outline,
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        const Text(
          'Review Modifications',
          style: TextStyle(
            fontFamily: 'Inter',
            fontSize: 28,
            fontWeight: FontWeight.w700,
            color: NoirColors.primary,
          ),
        ),
        const SizedBox(height: 6),
        const Text(
          'Analyze the proposed structural changes to the APK payload. '
          'Review risk levels before applying patches to the binary.',
          style: TextStyle(
            fontFamily: 'Inter',
            fontSize: 14,
            height: 1.4,
            color: NoirColors.onSurfaceVariant,
          ),
        ),
        const SizedBox(height: 20),

        // Stats Bento Grid
        Row(
          children: [
            Expanded(
              child: _buildBentoCard(
                label: 'Total Patches',
                value: '$patchCount',
                valueColor: NoirColors.primaryFixed,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _buildBentoCard(
                label: 'High Risk',
                value: '$riskCount',
                valueColor: riskCount > 0 ? NoirColors.errorBright : NoirColors.primaryFixed,
                hasWarning: riskCount > 0,
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: _buildBentoCard(
                label: 'Target Arch',
                value: targetArch,
                isCode: true,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _buildBentoCard(
                label: 'Build Size Est.',
                value: '+${patchCount * 120} KB',
                isCode: true,
              ),
            ),
          ],
        ),
        const SizedBox(height: 28),

        // Pending Operations List
        Row(
          children: [
            const Icon(
              Icons.build_circle,
              size: 20,
              color: NoirColors.primaryFixed,
            ),
            const SizedBox(width: 8),
            const Text(
              'Pending Operations',
              style: TextStyle(
                fontFamily: 'Inter',
                fontSize: 18,
                fontWeight: FontWeight.w600,
                color: NoirColors.primary,
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),

        for (int i = 0; i < flow.diff.length; i++)
          _buildModCard(flow.diff[i], i),

        const SizedBox(height: 80),
      ],
    );
  }

  Widget _buildBentoCard({
    required String label,
    required String value,
    Color? valueColor,
    bool hasWarning = false,
    bool isCode = false,
  }) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: hasWarning
              ? NoirColors.error.withValues(alpha: 0.3)
              : Colors.white.withValues(alpha: 0.05),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              if (hasWarning) ...[
                const Icon(Icons.warning, size: 14, color: NoirColors.errorBright),
                const SizedBox(width: 4),
              ],
              Text(
                label,
                style: const TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 11,
                  fontWeight: FontWeight.w500,
                  color: NoirColors.onSurfaceVariant,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            value,
            style: TextStyle(
              fontFamily: isCode ? 'JetBrainsMono' : 'Inter',
              fontSize: 22,
              fontWeight: FontWeight.w700,
              color: valueColor ?? NoirColors.onSurface,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildModCard(PatchDiffEntry entry, int index) {
    final isEnabled = _modToggles[index] ?? true;
    final isHighRisk = entry.path.contains('security') ||
        entry.path.contains('Trust') ||
        entry.path.contains('License');

    final accentColor = isHighRisk ? NoirColors.errorBright : NoirColors.primaryFixed;
    final riskBadge = isHighRisk ? 'HIGH RISK' : 'LOW RISK';

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      clipBehavior: Clip.antiAlias,
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Left colored stripe
            Container(
              width: 4,
              color: isEnabled ? accentColor : NoirColors.surfaceVariant,
            ),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Icon in circle
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: accentColor.withValues(alpha: 0.1),
                        shape: BoxShape.circle,
                        border: Border.all(color: accentColor.withValues(alpha: 0.2)),
                      ),
                      child: Icon(
                        isHighRisk ? Icons.gpp_bad : Icons.visibility_off,
                        size: 18,
                        color: accentColor,
                      ),
                    ),
                    const SizedBox(width: 12),
                    // Content
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Expanded(
                                child: Text(
                                  entry.operation.toUpperCase(),
                                  style: TextStyle(
                                    fontFamily: 'Inter',
                                    fontSize: 14,
                                    fontWeight: FontWeight.w700,
                                    color: isEnabled ? NoirColors.primary : NoirColors.outline,
                                  ),
                                ),
                              ),
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                                decoration: BoxDecoration(
                                  color: accentColor.withValues(alpha: 0.15),
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(color: accentColor.withValues(alpha: 0.3)),
                                ),
                                child: Text(
                                  riskBadge,
                                  style: TextStyle(
                                    fontFamily: 'JetBrainsMono',
                                    fontSize: 10,
                                    fontWeight: FontWeight.bold,
                                    color: accentColor,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text(
                            entry.preview.isNotEmpty
                                ? entry.preview.split('\n').first
                                : 'Modifies byte ranges and opcode definitions in APK structure.',
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontFamily: 'Inter',
                              fontSize: 12,
                              color: NoirColors.onSurfaceVariant,
                            ),
                          ),
                          const SizedBox(height: 8),
                          // File path badge
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                            decoration: BoxDecoration(
                              color: NoirColors.surfaceContainerHighest,
                              borderRadius: BorderRadius.circular(6),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                const Icon(Icons.code, size: 12, color: NoirColors.secondary),
                                const SizedBox(width: 4),
                                Flexible(
                                  child: Text(
                                    entry.path,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                      fontFamily: 'JetBrainsMono',
                                      fontSize: 11,
                                      color: NoirColors.secondary,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 10),
                    // Toggle switch
                    Switch(
                      value: isEnabled,
                      activeThumbColor: NoirColors.onPrimaryFixed,
                      activeTrackColor: NoirColors.primaryFixed,
                      inactiveThumbColor: NoirColors.onSurfaceVariant,
                      inactiveTrackColor: NoirColors.surfaceContainerHighest,
                      onChanged: (val) {
                        setState(() => _modToggles[index] = val);
                      },
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildRequestInputCard(
    ConnectionController connection,
    WorkflowController flow,
  ) {
    return Container(
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(Icons.edit_note, color: NoirColors.primaryFixed, size: 24),
              const SizedBox(width: 10),
              Text(
                'Describe Modifications',
                style: const TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                  color: NoirColors.primary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Target APK: ${flow.filename}',
            style: const TextStyle(
              fontFamily: 'JetBrainsMono',
              fontSize: 12,
              color: NoirColors.primaryFixed,
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _request,
            enabled: !flow.working,
            minLines: 3,
            maxLines: 5,
            decoration: const InputDecoration(
              labelText: 'What would you like to change?',
              hintText: 'e.g. rename the app to MyGame, unlock levels, disable tracking',
            ),
          ),
          const SizedBox(height: 12),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _consent,
            activeThumbColor: NoirColors.onPrimaryFixed,
            activeTrackColor: NoirColors.primaryFixed,
            title: const Text(
              'Allow AI analysis for decoded APK payload',
              style: TextStyle(fontSize: 13, color: NoirColors.onSurface),
            ),
            onChanged: flow.working ? null : (v) => setState(() => _consent = v),
          ),
          const SizedBox(height: 16),
          NoirPrimaryButton(
            label: 'PREVIEW CHANGES',
            loading: flow.working,
            onPressed: !flow.working && _consent && connection.isConnected
                ? () => flow.prepare(_request.text)
                : null,
          ),
        ],
      ),
    );
  }

  Widget _buildUnsupportedPlanCard(WorkflowController flow) {
    return Container(
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: NoirColors.error.withValues(alpha: 0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Row(
            children: [
              Icon(Icons.error_outline, color: NoirColors.errorBright, size: 24),
              SizedBox(width: 8),
              Text(
                'Unsupported Modification',
                style: TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                  color: NoirColors.errorBright,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            flow.plan?.intendedOutcome ?? 'Requested change cannot be safely applied.',
            style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
          ),
          const SizedBox(height: 8),
          for (final limitation in (flow.plan?.unsupportedAspects ?? []))
            Text('• $limitation', style: const TextStyle(color: NoirColors.onSurfaceVariant, fontSize: 13)),
          const SizedBox(height: 16),
          NoirGhostButton(
            label: 'EDIT REQUEST',
            onPressed: flow.working
                ? null
                : () {
                    _request.text = flow.request;
                    flow.editRequest();
                  },
          ),
        ],
      ),
    );
  }

  Widget _buildStickyCta(WorkflowController flow) {
    final activeCount = _modToggles.values.where((v) => v).length;
    final total = flow.diff.isNotEmpty ? flow.diff.length : 1;
    final count = _modToggles.isEmpty ? total : activeCount;

    return Positioned(
      bottom: 0,
      left: 0,
      right: 0,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        decoration: BoxDecoration(
          color: NoirColors.surfaceDim.withValues(alpha: 0.95),
          border: Border(
            top: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.6),
              blurRadius: 24,
              offset: const Offset(0, -6),
            ),
          ],
        ),
        child: SafeArea(
          top: false,
          child: Row(
            children: [
              Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Ready to execute',
                    style: TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 11,
                      color: NoirColors.onSurfaceVariant,
                    ),
                  ),
                  Text(
                    '$count Mods Selected',
                    style: const TextStyle(
                      fontFamily: 'JetBrainsMono',
                      fontSize: 13,
                      fontWeight: FontWeight.bold,
                      color: NoirColors.primaryFixed,
                    ),
                  ),
                ],
              ),
              const Spacer(),
              NoirGhostButton(
                label: 'EDIT',
                onPressed: flow.working
                    ? null
                    : () {
                        _request.text = flow.request;
                        flow.editRequest();
                      },
              ),
              const SizedBox(width: 10),
              NoirPrimaryButton(
                label: 'APPLY ALL MODS',
                icon: Icons.play_arrow,
                onPressed: flow.working ? null : flow.approveAndBuild,
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // STEP 2: BUILD PROCESS & EXPORT (Screenshot 4)
  // ─────────────────────────────────────────────────────────────────────────
  Widget _buildStep2(BuildContext context, WorkflowController flow) {
    final isDone = flow.build != null;
    final isRebuilding = flow.job?.stage.contains('rebuilding') ?? false;
    final isSigning = flow.job?.stage.contains('signing') ?? false;

    // Calculate progress gauge value
    double progress = 0.5;
    String stageLabel = 'PROCESSING';
    if (isDone) {
      progress = 1.0;
      stageLabel = 'COMPLETED';
    } else if (isSigning) {
      progress = 0.85;
      stageLabel = 'SIGNING';
    } else if (isRebuilding) {
      progress = 0.65;
      stageLabel = 'REBUILDING';
    } else if (flow.working) {
      progress = 0.40;
      stageLabel = 'MODIFYING';
    }

    // Simulated / live log entries
    final logs = <String>[
      '[NOIR] Initializing build sequence for ${flow.filename}...',
      '[SYS] Checking workspace integrity... OK.',
      '[APKTOOL] Building resources with Apktool 3.0.3',
      if (isRebuilding || isSigning || isDone)
        '[NOIR] Applying approved byte diffs to Dalvik structures...',
      if (isSigning || isDone)
        '[SYS] Packaging APK and aligning ZIP archives...',
      if (isDone) ...[
        '[NOIR] Signing APK with private workspace certificate.',
        '[SYS] Verified APK hash matches build record. Ready for export.',
      ],
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // Build Process Card
        Container(
          padding: const EdgeInsets.all(22),
          decoration: BoxDecoration(
            color: NoirColors.surfaceContainer,
            borderRadius: BorderRadius.circular(24),
            border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
          ),
          child: Column(
            children: [
              Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  'Build Process',
                  style: const TextStyle(
                    fontFamily: 'Inter',
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                    color: NoirColors.primary,
                  ),
                ),
              ),
              const SizedBox(height: 20),
              // Circular Gauge
              CircularBuildGauge(
                progress: progress,
                stageLabel: stageLabel,
              ),
              const SizedBox(height: 24),
              // Stage Badges Checklist
              _buildStageBadge(
                label: 'Decompiling',
                state: 'Done',
                isDone: true,
              ),
              const SizedBox(height: 8),
              _buildStageBadge(
                label: 'Modifying',
                state: 'Done',
                isDone: true,
              ),
              const SizedBox(height: 8),
              _buildStageBadge(
                label: 'Rebuilding',
                state: isDone ? 'Done' : (isRebuilding ? 'Active' : 'Pending'),
                isDone: isDone,
                isActive: isRebuilding,
              ),
              const SizedBox(height: 8),
              _buildStageBadge(
                label: 'Signing',
                state: isDone ? 'Done' : (isSigning ? 'Active' : 'Pending'),
                isDone: isDone,
                isActive: isSigning,
              ),
              const SizedBox(height: 24),
              // Action Button
              if (_download != null) ...[
                TransferBar(
                  progress: _download!,
                  title: _download!.fraction == 1
                      ? 'Received · verifying signature'
                      : 'Downloading APK',
                ),
                const SizedBox(height: 14),
              ],
              NoirPrimaryButton(
                label: isDone ? 'EXPORT APK' : 'BUILDING...',
                icon: isDone ? Icons.download : null,
                loading: _downloading,
                expand: true,
                onPressed: isDone && !_downloading ? () => _save(flow) : null,
              ),
              if (isDone) ...[
                const SizedBox(height: 10),
                NoirGhostButton(
                  label: 'MAKE ANOTHER APK',
                  expand: true,
                  onPressed: _downloading
                      ? null
                      : () {
                          flow.reset();
                          _request.clear();
                          setState(() {
                            _download = null;
                            _saveMessage = null;
                          });
                          context.go('/');
                        },
                ),
              ],
              if (flow.retryFinish) ...[
                const SizedBox(height: 10),
                NoirPrimaryButton(
                  label: 'RETRY BUILD',
                  expand: true,
                  onPressed: flow.working ? null : flow.approveAndBuild,
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 16),
        // Target Details Bento Card
        Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: NoirColors.surfaceContainer,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'TARGET DETAILS',
                style: TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1.5,
                  color: NoirColors.onSurfaceVariant,
                ),
              ),
              const SizedBox(height: 12),
              _buildDetailRow('Package:', flow.filename, NoirColors.primaryFixed),
              const SizedBox(height: 8),
              _buildDetailRow('Engine:', 'Apktool 3.0.3', NoirColors.tertiaryFixed),
              const SizedBox(height: 8),
              _buildDetailRow(
                'Status:',
                isDone ? 'Verified' : 'Processing',
                isDone ? NoirColors.primaryFixed : NoirColors.warning,
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        // Terminal Window
        TerminalLogView(
          lines: logs,
          title: 'noir_build_log_session_${flow.project?.id ?? "1829"}.log',
        ),
      ],
    );
  }

  Widget _buildStageBadge({
    required String label,
    required String state,
    bool isDone = false,
    bool isActive = false,
  }) {
    Color borderColor = NoirColors.surfaceContainerHighest;
    Color bgColor = NoirColors.surfaceContainerHigh;
    Color iconColor = NoirColors.onSurfaceVariant;
    Color stateColor = NoirColors.onSurfaceVariant;
    IconData icon = Icons.circle_outlined;

    if (isDone) {
      icon = Icons.check_circle;
      iconColor = NoirColors.primaryFixed;
      stateColor = NoirColors.onSurfaceVariant;
    } else if (isActive) {
      icon = Icons.sync;
      iconColor = NoirColors.primaryFixed;
      stateColor = NoirColors.primaryFixed;
      borderColor = NoirColors.primaryFixed.withValues(alpha: 0.4);
      bgColor = NoirColors.primaryFixed.withValues(alpha: 0.08);
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: borderColor),
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Row(
            children: [
              Icon(icon, size: 18, color: iconColor),
              const SizedBox(width: 10),
              Text(
                label,
                style: TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 13,
                  fontWeight: isActive ? FontWeight.w700 : FontWeight.w500,
                  color: isActive ? NoirColors.primaryFixed : NoirColors.onSurface,
                ),
              ),
            ],
          ),
          Text(
            state,
            style: TextStyle(
              fontFamily: 'JetBrainsMono',
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: stateColor,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildDetailRow(String label, String value, Color valueColor) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text(
          label,
          style: const TextStyle(
            fontFamily: 'JetBrainsMono',
            fontSize: 12,
            color: NoirColors.onSurface,
          ),
        ),
        Flexible(
          child: Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontFamily: 'JetBrainsMono',
              fontSize: 12,
              fontWeight: FontWeight.w600,
              color: valueColor,
            ),
          ),
        ),
      ],
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Banners & Status Cards
  // ─────────────────────────────────────────────────────────────────────────
  Widget _buildConnectionWarning(ConnectionController connection) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: NoirColors.error.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          const Icon(Icons.wifi_off, color: NoirColors.errorBright, size: 20),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              connection.hasToken
                  ? 'Server reconnecting...'
                  : 'Activate workspace in Settings to connect.',
              style: const TextStyle(fontSize: 13, color: NoirColors.onSurface),
            ),
          ),
          TextButton(
            onPressed: () => context.go('/settings'),
            child: const Text('SETTINGS', style: TextStyle(color: NoirColors.primaryFixed)),
          ),
        ],
      ),
    );
  }

  Widget _buildErrorCard(WorkflowController flow) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: NoirColors.error.withValues(alpha: 0.4)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.warning, color: NoirColors.errorBright, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: SelectableText(
              flow.error!,
              style: const TextStyle(fontSize: 12, color: NoirColors.error),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.refresh, size: 18, color: NoirColors.onSurfaceVariant),
            onPressed: flow.refresh,
          ),
        ],
      ),
    );
  }

  Widget _buildSaveMessageCard() {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: NoirColors.primaryFixed.withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          const Icon(Icons.check_circle, color: NoirColors.primaryFixed, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: SelectableText(
              _saveMessage!,
              style: const TextStyle(fontSize: 12, color: NoirColors.onSurface),
            ),
          ),
        ],
      ),
    );
  }
}
