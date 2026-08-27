/// Build screen — validation, rebuild with SSE monitoring, cancel.
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../../core/state/build_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/glass_panel.dart';
import '../../core/widgets/log_viewer.dart';
import '../../core/widgets/mesh_gradient_background.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_button.dart';

class BuildScreen extends StatefulWidget {
  const BuildScreen({super.key, required this.projectId});
  final String projectId;

  @override
  State<BuildScreen> createState() => _BuildScreenState();
}

class _BuildScreenState extends State<BuildScreen> {
  @override
  void initState() {
    super.initState();
    context.read<BuildController>().loadBuilds(widget.projectId);
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<BuildController>(
      builder: (context, ctrl, _) {
        return Scaffold(
          backgroundColor: NoirColors.black,
          appBar: NoirAppBar(
            title: 'VALIDATE & BUILD',
            showBackButton: true,
            actions: const [SizedBox(width: 48)],
          ),
          body: MeshGradientBackground(
            child: LayoutBuilder(
              builder: (context, constraints) {
                final wide = constraints.maxWidth > 700;
                return Padding(
                  padding: const EdgeInsets.all(16),
                  child: wide
                      ? Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Expanded(child: _buildProgressPanel(ctrl)),
                            const SizedBox(width: 16),
                            Expanded(child: _buildLogPanel(ctrl)),
                          ],
                        )
                      : ListView(
                          children: [
                            _buildProgressPanel(ctrl),
                            const SizedBox(height: 16),
                            SizedBox(height: 300, child: _buildLogPanel(ctrl)),
                            const SizedBox(height: 16),
                            _buildActions(ctrl),
                          ],
                        ),
                );
              },
            ),
          ),
          bottomNavigationBar: LayoutBuilder(builder: (context, constraints) {
            if (constraints.maxWidth > 700) {
              return Padding(
                padding: const EdgeInsets.all(16),
                child: _buildActions(ctrl),
              );
            }
            return const SizedBox.shrink();
          }),
        );
      },
    );
  }

  Widget _buildProgressPanel(BuildController ctrl) {
    return GlassPanel(
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          // Header
          Align(
            alignment: Alignment.topLeft,
            child: Text('[ SYS.BUILD_STATUS ]',
                style: NoirTypography.labelCaps
                    .copyWith(color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5))),
          ),
          const SizedBox(height: 32),

          // Progress indicator
          SizedBox(
            width: 200,
            height: 200,
            child: Stack(
              alignment: Alignment.center,
              children: [
                // Outer ring
                SizedBox(
                  width: 200,
                  height: 200,
                  child: CircularProgressIndicator(
                    strokeWidth: 1,
                    color: NoirColors.outlineVariant.withValues(alpha: 0.3),
                    value: 1.0,
                  ),
                ),
                // Inner progress ring
                SizedBox(
                  width: 160,
                  height: 160,
                  child: CircularProgressIndicator(
                    strokeWidth: 3,
                    color: NoirColors.primary,
                    value: ctrl.building ? null : (ctrl.currentJob?.isTerminal == true ? 1.0 : 0.0),
                  ),
                ),
                // Center text
                Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      _stateLabel(ctrl),
                      style: NoirTypography.headlineLg.copyWith(
                        color: NoirColors.primary,
                        fontSize: 18,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      _stateSubtext(ctrl),
                      style: NoirTypography.labelCaps
                          .copyWith(color: NoirColors.onSurfaceVariant),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 32),

          // Validation summary
          if (ctrl.validation != null)
            _validationSummary(ctrl),

          // Error
          if (ctrl.error != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(ctrl.error!,
                  style: NoirTypography.codeSm.copyWith(color: NoirColors.error)),
            ),
        ],
      ),
    );
  }

  Widget _validationSummary(BuildController ctrl) {
    final v = ctrl.validation!;
    return Column(
      children: [
        Divider(color: Colors.white.withValues(alpha: 0.1)),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            _validationBadge('ERRORS', '${v.errorCount}', v.errorCount > 0),
            const SizedBox(width: 16),
            _validationBadge('WARNINGS', '${v.warningCount}', false),
            const SizedBox(width: 16),
            _validationBadge('STATUS', v.passed ? 'PASS' : 'FAIL', !v.passed),
          ],
        ),
        if (v.findings.isNotEmpty) ...[
          const SizedBox(height: 12),
          ...v.findings.take(5).map((f) => Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    Icon(
                      f.severity == 'error' ? Icons.error_outline : Icons.warning_amber,
                      size: 12,
                      color: NoirColors.onSurfaceVariant.withValues(alpha: 0.6),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(f.message,
                          style: NoirTypography.codeSm.copyWith(
                            color: NoirColors.onSurfaceVariant.withValues(alpha: 0.6),
                          )),
                    ),
                  ],
                ),
              )),
        ],
      ],
    );
  }

  Widget _validationBadge(String label, String value, bool alert) {
    return Column(
      children: [
        Text(value,
            style: NoirTypography.headlineMd.copyWith(
              color: alert ? NoirColors.primary : NoirColors.onSurfaceVariant,
            )),
        Text(label,
            style: NoirTypography.labelCaps
                .copyWith(color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5))),
      ],
    );
  }

  Widget _buildLogPanel(BuildController ctrl) {
    return LogViewer(
      entries: ctrl.logEntries,
      title: 'SYSTEM_LOG',
    );
  }

  Widget _buildActions(BuildController ctrl) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.end,
      children: [
        if (ctrl.building)
          NoirGhostButton(
            label: 'Cancel',
            icon: Icons.stop,
            onPressed: ctrl.cancelBuild,
          ),
        if (!ctrl.building) ...[
          NoirGhostButton(
            label: 'Validate',
            icon: Icons.check_circle_outline,
            loading: ctrl.validating,
            onPressed: () => ctrl.validate(widget.projectId),
          ),
          const SizedBox(width: 12),
          NoirPrimaryButton(
            label: 'Build',
            icon: Icons.build,
            loading: ctrl.building,
            onPressed: ctrl.validation?.passed == true
                ? () => ctrl.startBuild(widget.projectId)
                : null,
          ),
        ],
        if (ctrl.currentJob?.state == 'succeeded') ...[
          const SizedBox(width: 12),
          NoirPrimaryButton(
            label: 'Sign & Export',
            icon: Icons.vpn_key,
            onPressed: () => context.push('/project/${widget.projectId}/sign'),
          ),
        ],
      ],
    );
  }

  String _stateLabel(BuildController ctrl) {
    if (ctrl.validating) return 'VALIDATING';
    if (ctrl.building) return 'BUILDING';
    if (ctrl.currentJob?.state == 'succeeded') return 'COMPLETE';
    if (ctrl.currentJob?.state == 'failed') return 'FAILED';
    if (ctrl.currentJob?.state == 'cancelled') return 'CANCELLED';
    return 'READY';
  }

  String _stateSubtext(BuildController ctrl) {
    if (ctrl.validating) return 'CHECKING WORKSPACE';
    if (ctrl.building) return 'COMPILING';
    if (ctrl.currentJob?.state == 'succeeded') return 'APK REBUILT';
    if (ctrl.currentJob?.state == 'failed') return 'BUILD ERROR';
    return 'VALIDATE FIRST';
  }
}
