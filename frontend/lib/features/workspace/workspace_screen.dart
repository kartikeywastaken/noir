/// Workspace screen — file explorer + code viewer + action bar.
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../../core/state/connection_controller.dart';
import '../../core/state/workspace_controller.dart';
import '../../core/state/plan_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/code_viewer.dart';
import '../../core/widgets/glass_panel.dart';
import '../../core/widgets/mesh_gradient_background.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/status_chip.dart';
import '../../data/models/models.dart';

class WorkspaceScreen extends StatefulWidget {
  const WorkspaceScreen({super.key, required this.projectId});
  final String projectId;

  @override
  State<WorkspaceScreen> createState() => _WorkspaceScreenState();
}

class _WorkspaceScreenState extends State<WorkspaceScreen> {
  final _requestController = TextEditingController();
  bool _showAiDialog = false;
  bool _aiConsent = false;

  @override
  void initState() {
    super.initState();
    final ws = context.read<WorkspaceController>();
    ws.setProjectId(widget.projectId);
    ws.loadProject();
    ws.loadAnalysis();
    ws.loadFiles();
  }

  @override
  void dispose() {
    _requestController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isWide = MediaQuery.of(context).size.width > 800;

    return Consumer<WorkspaceController>(
      builder: (context, ws, _) {
        return Scaffold(
          backgroundColor: NoirColors.black,
          appBar: NoirAppBar(
            title: ws.project?.packageName ?? 'WORKSPACE',
            showBackButton: true,
            actions: [
              if (ws.project != null)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: StatusChip(label: ws.project!.status),
                ),
              const SizedBox(width: 8),
            ],
          ),
          body: MeshGradientBackground(
            child: Column(
              children: [
                // Action bar
                _buildActionBar(context, ws),
                // Main content
                Expanded(
                  child: isWide
                      ? Row(
                          children: [
                            SizedBox(width: 280, child: _buildFileTree(ws)),
                            Container(
                              width: 1,
                              color: Colors.white.withValues(alpha: 0.1),
                            ),
                            Expanded(child: _buildCodePanel(ws)),
                          ],
                        )
                      : ws.selectedFilePath != null
                          ? _buildCodePanel(ws)
                          : _buildFileTree(ws),
                ),
              ],
            ),
          ),
          // AI request dialog
          floatingActionButton: _showAiDialog ? null : FloatingActionButton.small(
            backgroundColor: NoirColors.surfaceContainerHigh,
            foregroundColor: NoirColors.primary,
            onPressed: () => setState(() => _showAiDialog = true),
            child: const Icon(Icons.auto_awesome, size: 18),
          ),
        );
      },
    );
  }

  Widget _buildActionBar(BuildContext context, WorkspaceController ws) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.4),
        border: Border(
          bottom: BorderSide(color: Colors.white.withValues(alpha: 0.05)),
        ),
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            NoirGhostButton(
              label: 'Ask AI',
              icon: Icons.auto_awesome,
              onPressed: () => setState(() => _showAiDialog = true),
            ),
            const SizedBox(width: 8),
            NoirGhostButton(
              label: 'Manual Edit',
              icon: Icons.edit_note,
              onPressed: () => _beginManualEdit(context),
            ),
            const SizedBox(width: 8),
            NoirGhostButton(
              label: 'Build',
              icon: Icons.build,
              onPressed: () => context.push('/project/${widget.projectId}/build'),
            ),
            const SizedBox(width: 8),
            NoirGhostButton(
              label: 'Sign',
              icon: Icons.vpn_key,
              onPressed: () => context.push('/project/${widget.projectId}/sign'),
            ),
            const SizedBox(width: 8),
            NoirGhostButton(
              label: 'Audit',
              icon: Icons.receipt_long,
              onPressed: () => context.push('/project/${widget.projectId}/audit'),
            ),
            if (_showAiDialog) ...[
              const SizedBox(width: 16),
              _buildAiRequestInline(),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildAiRequestInline() {
    return GlassPanel(
      padding: const EdgeInsets.all(12),
      child: SizedBox(
        width: 400,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('AI MODIFICATION REQUEST',
                style: NoirTypography.labelCaps
                    .copyWith(color: NoirColors.onSurfaceVariant)),
            const SizedBox(height: 8),
            TextField(
              controller: _requestController,
              maxLines: 3,
              style: NoirTypography.codeSm.copyWith(color: NoirColors.primary),
              decoration: const InputDecoration(
                hintText: 'Describe the modification...',
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                SizedBox(
                  width: 16,
                  height: 16,
                  child: Checkbox(
                    value: _aiConsent,
                    onChanged: (v) => setState(() => _aiConsent = v ?? false),
                    activeColor: NoirColors.primary,
                    checkColor: NoirColors.black,
                    side: BorderSide(color: Colors.white.withValues(alpha: 0.3)),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'I consent to uploading workspace context to the AI provider',
                    style: NoirTypography.codeSm.copyWith(
                      color: NoirColors.onSurfaceVariant.withValues(alpha: 0.6),
                      fontSize: 10,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                NoirGhostButton(
                  label: 'Cancel',
                  onPressed: () => setState(() => _showAiDialog = false),
                ),
                const SizedBox(width: 8),
                Consumer<PlanController>(
                  builder: (context, planCtrl, _) {
                    return NoirPrimaryButton(
                      label: 'Generate Plan',
                      loading: planCtrl.generating,
                      onPressed: _aiConsent && _requestController.text.isNotEmpty
                          ? () => _generatePlan(planCtrl)
                          : null,
                    );
                  },
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _generatePlan(PlanController planCtrl) async {
    final plan = await planCtrl.createPlan(
      widget.projectId,
      _requestController.text,
    );
    if (plan != null && mounted) {
      setState(() => _showAiDialog = false);
      _requestController.clear();
      context.push('/project/${widget.projectId}/plan/${plan.planId}');
    }
  }

  Future<void> _beginManualEdit(BuildContext context) async {
    final api = context.read<ConnectionController>().api;
    try {
      await api.beginManualSession(widget.projectId);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Manual edit session started. Edit files and record changes.')),
      );
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Error: $e')),
      );
    }
  }

  Widget _buildFileTree(WorkspaceController ws) {
    return Container(
      color: NoirColors.surfaceContainerLowest,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Breadcrumb
          Padding(
            padding: const EdgeInsets.all(12),
            child: GestureDetector(
              onTap: () => ws.loadFiles(),
              child: Row(
                children: [
                  Icon(Icons.folder, size: 14,
                      color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5)),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      ws.currentSubdir.isEmpty ? '/' : '/${ws.currentSubdir}',
                      style: NoirTypography.codeSm.copyWith(
                        color: NoirColors.onSurfaceVariant,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ),
          Divider(height: 1, color: Colors.white.withValues(alpha: 0.05)),

          if (ws.loadingFiles)
            const Padding(
              padding: EdgeInsets.all(24),
              child: Center(
                child: CircularProgressIndicator(strokeWidth: 1, color: NoirColors.primary),
              ),
            ),

          Expanded(
            child: ListView.builder(
              itemCount: ws.files.length,
              itemBuilder: (context, i) {
                final f = ws.files[i];
                final selected = f.path == ws.selectedFilePath;
                return InkWell(
                  onTap: () {
                    if (f.isDirectory) {
                      ws.loadFiles(subdir: f.path);
                    } else {
                      ws.openFile(f.path);
                    }
                  },
                  child: Container(
                    color: selected ? Colors.white.withValues(alpha: 0.05) : null,
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    child: Row(
                      children: [
                        Icon(
                          f.isDirectory ? Icons.folder : Icons.description_outlined,
                          size: 14,
                          color: f.isDirectory
                              ? NoirColors.onSurfaceVariant.withValues(alpha: 0.6)
                              : NoirColors.onSurfaceVariant.withValues(alpha: 0.4),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            f.name,
                            style: NoirTypography.codeSm.copyWith(
                              color: selected ? NoirColors.primary : NoirColors.onSurfaceVariant,
                            ),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        if (f.isDirectory)
                          Icon(Icons.chevron_right, size: 14,
                              color: NoirColors.onSurfaceVariant.withValues(alpha: 0.3)),
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCodePanel(WorkspaceController ws) {
    if (ws.selectedFilePath == null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.code, size: 48,
                color: NoirColors.onSurfaceVariant.withValues(alpha: 0.2)),
            const SizedBox(height: 16),
            Text('Select a file to view',
                style: NoirTypography.bodySm.copyWith(
                  color: NoirColors.onSurfaceVariant.withValues(alpha: 0.4),
                )),
          ],
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // File header
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.3),
            border: Border(
              bottom: BorderSide(color: Colors.white.withValues(alpha: 0.05)),
            ),
          ),
          child: Row(
            children: [
              const Icon(Icons.description_outlined, size: 14, color: NoirColors.onSurfaceVariant),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  ws.selectedFilePath!,
                  style: NoirTypography.codeSm.copyWith(color: NoirColors.onSurfaceVariant),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              // Back button for mobile
              if (MediaQuery.of(context).size.width <= 800)
                IconButton(
                  icon: const Icon(Icons.close, size: 16, color: NoirColors.onSurfaceVariant),
                  onPressed: () => ws.openFile(''),
                ),
            ],
          ),
        ),
        // Content
        Expanded(
          child: ws.loadingContent
              ? const Center(
                  child: CircularProgressIndicator(strokeWidth: 1, color: NoirColors.primary),
                )
              : ws.fileContent != null
                  ? CodeViewer(content: ws.fileContent!)
                  : const SizedBox.shrink(),
        ),
      ],
    );
  }
}
