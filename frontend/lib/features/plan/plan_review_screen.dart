/// Plan Review screen — view plan details, approve or reject.
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../../core/state/plan_controller.dart';
import '../../core/state/patch_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/glass_panel.dart';
import '../../core/widgets/mesh_gradient_background.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_button.dart';

class PlanReviewScreen extends StatefulWidget {
  const PlanReviewScreen({
    super.key,
    required this.projectId,
    required this.planId,
  });

  final String projectId;
  final String planId;

  @override
  State<PlanReviewScreen> createState() => _PlanReviewScreenState();
}

class _PlanReviewScreenState extends State<PlanReviewScreen> {
  @override
  void initState() {
    super.initState();
    context.read<PlanController>().loadPlan(widget.projectId, widget.planId);
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<PlanController>(
      builder: (context, ctrl, _) {
        final plan = ctrl.currentPlan;

        return Scaffold(
          backgroundColor: NoirColors.black,
          appBar: NoirAppBar(
            title: 'REVIEW PLAN',
            showBackButton: true,
            actions: const [SizedBox(width: 48)],
          ),
          body: MeshGradientBackground(
            child: plan == null
                ? const Center(
                    child: CircularProgressIndicator(
                        strokeWidth: 1, color: NoirColors.primary),
                  )
                : ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      // Target info
                      GlassPanel(
                        padding: const EdgeInsets.all(20),
                        child: Row(
                          children: [
                            Container(
                              width: 3,
                              height: 60,
                              color: Colors.white.withValues(alpha: 0.4),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    'REQUEST',
                                    style: NoirTypography.labelCaps.copyWith(
                                      color: NoirColors.onSurfaceVariant.withValues(alpha: 0.6),
                                    ),
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    plan.userRequest,
                                    style: NoirTypography.codeLg
                                        .copyWith(color: NoirColors.primary),
                                  ),
                                  const SizedBox(height: 8),
                                  Row(
                                    children: [
                                      Icon(Icons.fingerprint,
                                          size: 12,
                                          color: NoirColors.onSurfaceVariant
                                              .withValues(alpha: 0.4)),
                                      const SizedBox(width: 4),
                                      Expanded(
                                        child: Text(
                                          'Hash: ${plan.planHash.substring(0, 16)}...',
                                          style: NoirTypography.codeSm.copyWith(
                                            color: NoirColors.onSurfaceVariant
                                                .withValues(alpha: 0.5),
                                          ),
                                        ),
                                      ),
                                      Text(
                                        'Rev: ${plan.workspaceRevision}',
                                        style: NoirTypography.codeSm.copyWith(
                                          color: NoirColors.onSurfaceVariant
                                              .withValues(alpha: 0.5),
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),

                      // Intended outcome
                      if (plan.intendedOutcome.isNotEmpty) ...[
                        _sectionTitle('INTENDED OUTCOME'),
                        const SizedBox(height: 8),
                        GlassPanel(
                          padding: const EdgeInsets.all(16),
                          child: Text(
                            plan.intendedOutcome,
                            style: NoirTypography.bodySm.copyWith(
                              color: NoirColors.onSurfaceVariant.withValues(alpha: 0.8),
                            ),
                          ),
                        ),
                        const SizedBox(height: 20),
                      ],

                      // File changes
                      _sectionTitle('FILE CHANGES (${plan.fileChanges.length})'),
                      const SizedBox(height: 8),
                      ...plan.fileChanges.map((change) => Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: GlassPanel(
                              padding: const EdgeInsets.all(14),
                              child: Row(
                                children: [
                                  _opBadge(change.operation),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: Column(
                                      crossAxisAlignment: CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          change.relativePath,
                                          style: NoirTypography.codeSm
                                              .copyWith(color: NoirColors.primary),
                                        ),
                                        if (change.description.isNotEmpty)
                                          Text(
                                            change.description,
                                            style: NoirTypography.codeSm.copyWith(
                                              color: NoirColors.onSurfaceVariant
                                                  .withValues(alpha: 0.5),
                                            ),
                                          ),
                                      ],
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          )),
                      const SizedBox(height: 20),

                      // Risks
                      if (plan.risks.isNotEmpty) ...[
                        _sectionTitle('RISKS'),
                        const SizedBox(height: 8),
                        ...plan.risks.map((r) => _bulletItem(r, Icons.warning_amber)),
                        const SizedBox(height: 20),
                      ],

                      // Behavioral changes
                      if (plan.behavioralChanges.isNotEmpty) ...[
                        _sectionTitle('BEHAVIORAL CHANGES'),
                        const SizedBox(height: 8),
                        ...plan.behavioralChanges.map((b) => _bulletItem(b, Icons.swap_horiz)),
                        const SizedBox(height: 20),
                      ],

                      // Compatibility
                      if (plan.compatibilityConcerns.isNotEmpty) ...[
                        _sectionTitle('COMPATIBILITY'),
                        const SizedBox(height: 8),
                        ...plan.compatibilityConcerns
                            .map((c) => _bulletItem(c, Icons.devices)),
                        const SizedBox(height: 20),
                      ],

                      // Unsupported
                      if (plan.unsupportedAspects.isNotEmpty) ...[
                        _sectionTitle('UNSUPPORTED'),
                        const SizedBox(height: 8),
                        ...plan.unsupportedAspects
                            .map((u) => _bulletItem(u, Icons.block)),
                        const SizedBox(height: 20),
                      ],

                      // Error
                      if (ctrl.error != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 16),
                          child: Text(ctrl.error!,
                              style: NoirTypography.codeSm
                                  .copyWith(color: NoirColors.error)),
                        ),

                      // Actions
                      Divider(color: Colors.white.withValues(alpha: 0.1)),
                      const SizedBox(height: 16),
                      NoirGhostButton(
                        label: 'Reject Plan',
                        icon: Icons.close,
                        expand: true,
                        onPressed: () async {
                          final ok = await ctrl.rejectPlan(widget.projectId);
                          if (ok && mounted) context.pop();
                        },
                      ),
                      const SizedBox(height: 12),
                      NoirPrimaryButton(
                        label: 'Approve Plan',
                        icon: Icons.check,
                        loading: ctrl.approving,
                        onPressed: () async {
                          final ok = await ctrl.approvePlan(widget.projectId);
                          if (ok && mounted) {
                            // Generate patch
                            final patchCtrl = context.read<PatchController>();
                            final patch = await patchCtrl.generatePatch(
                                widget.projectId, widget.planId);
                            if (patch != null && mounted) {
                              context.pushReplacement(
                                '/project/${widget.projectId}/patch/${patch.patchId}',
                              );
                            }
                          }
                        },
                      ),
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
        Text(
          title,
          style: NoirTypography.labelCaps.copyWith(
            color: NoirColors.onSurfaceVariant,
            letterSpacing: 2,
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Divider(color: Colors.white.withValues(alpha: 0.1)),
        ),
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
      child: Text(
        operation.toUpperCase(),
        style: NoirTypography.labelCaps.copyWith(
          color: NoirColors.onSurfaceVariant.withValues(alpha: 0.7),
          fontSize: 8,
        ),
      ),
    );
  }

  Widget _bulletItem(String text, IconData icon) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(icon, size: 14,
                color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              style: NoirTypography.bodySm.copyWith(
                color: NoirColors.onSurfaceVariant.withValues(alpha: 0.7),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
