/// Patch Review screen — diff view, approve, apply.
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../../core/state/patch_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/glass_panel.dart';
import '../../core/widgets/mesh_gradient_background.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_button.dart';

class PatchReviewScreen extends StatefulWidget {
  const PatchReviewScreen({
    super.key,
    required this.projectId,
    required this.patchId,
  });

  final String projectId;
  final String patchId;

  @override
  State<PatchReviewScreen> createState() => _PatchReviewScreenState();
}

class _PatchReviewScreenState extends State<PatchReviewScreen> {
  bool _approved = false;

  @override
  void initState() {
    super.initState();
    final ctrl = context.read<PatchController>();
    ctrl.loadPatch(widget.projectId, widget.patchId);
    ctrl.loadDiff(widget.projectId, widget.patchId);
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<PatchController>(
      builder: (context, ctrl, _) {
        final patch = ctrl.currentPatch;

        return Scaffold(
          backgroundColor: NoirColors.black,
          appBar: NoirAppBar(
            title: 'REVIEW PATCH',
            showBackButton: true,
            actions: const [SizedBox(width: 48)],
          ),
          body: MeshGradientBackground(
            child: patch == null
                ? const Center(
                    child: CircularProgressIndicator(
                        strokeWidth: 1, color: NoirColors.primary),
                  )
                : ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      // Patch info header
                      GlassPanel(
                        padding: const EdgeInsets.all(20),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Container(
                                  width: 3,
                                  height: 40,
                                  color: Colors.white.withValues(alpha: 0.4),
                                ),
                                const SizedBox(width: 12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        'PATCH: ${patch.patchId}',
                                        style: NoirTypography.codeLg
                                            .copyWith(color: NoirColors.primary),
                                      ),
                                      const SizedBox(height: 4),
                                      Row(
                                        children: [
                                          Icon(Icons.fingerprint,
                                              size: 12,
                                              color: NoirColors.onSurfaceVariant
                                                  .withValues(alpha: 0.4)),
                                          const SizedBox(width: 4),
                                          Expanded(
                                            child: Text(
                                              'Hash: ${patch.patchHash.substring(0, 16)}...',
                                              style: NoirTypography.codeSm.copyWith(
                                                color: NoirColors.onSurfaceVariant
                                                    .withValues(alpha: 0.5),
                                              ),
                                            ),
                                          ),
                                        ],
                                      ),
                                    ],
                                  ),
                                ),
                                _infoBadge('${patch.operations.length} ops'),
                                const SizedBox(width: 8),
                                _infoBadge('rev ${patch.workspaceRevision}'),
                              ],
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 20),

                      // Diff entries
                      _sectionTitle('FILE CHANGES'),
                      const SizedBox(height: 8),

                      if (ctrl.diff.isEmpty && ctrl.error == null)
                        ...patch.operations.map((op) => Padding(
                              padding: const EdgeInsets.only(bottom: 6),
                              child: GlassPanel(
                                padding: const EdgeInsets.all(14),
                                child: Row(
                                  children: [
                                    _opBadge(op.operation),
                                    const SizedBox(width: 10),
                                    Expanded(
                                      child: Text(
                                        op.relativePath,
                                        style: NoirTypography.codeSm
                                            .copyWith(color: NoirColors.primary),
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                            )),

                      // Actual diffs
                      ...ctrl.diff.map((entry) => Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: GlassPanel(
                              padding: const EdgeInsets.all(16),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      _opBadge(entry.operation),
                                      const SizedBox(width: 8),
                                      Expanded(
                                        child: Text(
                                          entry.path,
                                          style: NoirTypography.codeSm
                                              .copyWith(color: NoirColors.primary),
                                        ),
                                      ),
                                    ],
                                  ),
                                  if (entry.before != null) ...[
                                    const SizedBox(height: 12),
                                    Text('BEFORE',
                                        style: NoirTypography.labelCaps.copyWith(
                                          color: NoirColors.onSurfaceVariant
                                              .withValues(alpha: 0.4),
                                        )),
                                    const SizedBox(height: 4),
                                    Container(
                                      width: double.infinity,
                                      padding: const EdgeInsets.all(12),
                                      decoration: BoxDecoration(
                                        color: Colors.white.withValues(alpha: 0.02),
                                        borderRadius: BorderRadius.circular(2),
                                        border: Border.all(
                                            color: Colors.white.withValues(alpha: 0.05)),
                                      ),
                                      child: SelectableText(
                                        entry.before!,
                                        style: NoirTypography.codeSm.copyWith(
                                          color: NoirColors.onSurfaceVariant
                                              .withValues(alpha: 0.5),
                                        ),
                                        maxLines: 30,
                                      ),
                                    ),
                                  ],
                                  if (entry.after != null) ...[
                                    const SizedBox(height: 8),
                                    Text('AFTER',
                                        style: NoirTypography.labelCaps.copyWith(
                                          color: NoirColors.onSurfaceVariant
                                              .withValues(alpha: 0.4),
                                        )),
                                    const SizedBox(height: 4),
                                    Container(
                                      width: double.infinity,
                                      padding: const EdgeInsets.all(12),
                                      decoration: BoxDecoration(
                                        color: Colors.white.withValues(alpha: 0.02),
                                        borderRadius: BorderRadius.circular(2),
                                        border: Border.all(
                                            color: Colors.white.withValues(alpha: 0.05)),
                                      ),
                                      child: SelectableText(
                                        entry.after!,
                                        style: NoirTypography.codeSm.copyWith(
                                          color: NoirColors.primary.withValues(alpha: 0.8),
                                        ),
                                        maxLines: 30,
                                      ),
                                    ),
                                  ],
                                ],
                              ),
                            ),
                          )),

                      // Error
                      if (ctrl.error != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 16),
                          child: Text(ctrl.error!,
                              style: NoirTypography.codeSm
                                  .copyWith(color: NoirColors.error)),
                        ),

                      const SizedBox(height: 16),
                      Divider(color: Colors.white.withValues(alpha: 0.1)),
                      const SizedBox(height: 16),

                      // Approve
                      if (!_approved)
                        NoirPrimaryButton(
                          label: 'Approve Patch',
                          icon: Icons.check,
                          onPressed: () async {
                            final ok = await ctrl.approvePatch(widget.projectId);
                            if (ok) setState(() => _approved = true);
                          },
                        ),

                      // Apply (after approval)
                      if (_approved) ...[
                        GlassPanel(
                          padding: const EdgeInsets.all(12),
                          child: Row(
                            children: [
                              const Icon(Icons.check_circle,
                                  color: NoirColors.primary, size: 16),
                              const SizedBox(width: 8),
                              Text('Patch approved',
                                  style: NoirTypography.codeSm
                                      .copyWith(color: NoirColors.primary)),
                            ],
                          ),
                        ),
                        const SizedBox(height: 12),
                        NoirPrimaryButton(
                          label: 'Apply Patch',
                          icon: Icons.play_arrow,
                          loading: ctrl.applying,
                          onPressed: () async {
                            final ok =
                                await ctrl.applyPatch(widget.projectId);
                            if (ok && mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(
                                    content: Text('Patch applied successfully')),
                              );
                              context.pop();
                            }
                          },
                        ),
                      ],
                      const SizedBox(height: 32),
                    ],
                  ),
          ),
        );
      },
    );
  }

  Widget _sectionTitle(String title) {
    return Row(
      children: [
        Text(title,
            style: NoirTypography.labelCaps
                .copyWith(color: NoirColors.onSurfaceVariant, letterSpacing: 2)),
        const SizedBox(width: 12),
        Expanded(child: Divider(color: Colors.white.withValues(alpha: 0.1))),
      ],
    );
  }

  Widget _opBadge(String operation) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.05),
        border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
        borderRadius: BorderRadius.circular(2),
      ),
      child: Text(operation.toUpperCase(),
          style: NoirTypography.labelCaps.copyWith(
            color: NoirColors.onSurfaceVariant.withValues(alpha: 0.7),
            fontSize: 8,
          )),
    );
  }

  Widget _infoBadge(String text) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.05),
        border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
        borderRadius: BorderRadius.circular(2),
      ),
      child: Text(text,
          style: NoirTypography.codeSm.copyWith(
            color: NoirColors.onSurfaceVariant.withValues(alpha: 0.7),
          )),
    );
  }
}
