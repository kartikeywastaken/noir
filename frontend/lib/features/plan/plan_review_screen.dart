import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';
import '../../data/models/models.dart';

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
  ChangePlan? _plan;
  bool _busy = true;
  String? _error;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _busy = true;
      _error = null;
      _plan = null;
    });
    try {
      final plan = await context.read<ConnectionController>().api.getPlan(
        widget.projectId,
        widget.planId,
      );
      if (mounted) setState(() => _plan = plan);
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _approve() async {
    final plan = _plan!;
    if (!await confirmAction(
          context,
          'Approve this exact plan?',
          'Plan ${plan.planId}\nRevision ${plan.workspaceRevision}\nSHA-256: ${plan.planHash}',
          'Approve',
        ) ||
        !mounted) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<ConnectionController>().api.approvePlan(
        widget.projectId,
        plan.planId,
        plan.planHash,
      );
      if (mounted) await _load();
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _busy = false;
        });
      }
    }
  }

  Future<void> _generate() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final patch = await context
          .read<ConnectionController>()
          .api
          .generatePatch(widget.projectId, widget.planId);
      if (mounted) {
        await context.push(
          '/project/${widget.projectId}/patch/${patch.patchId}',
        );
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _reject() async {
    if (!await confirmAction(
          context,
          'Reject plan?',
          'This invalidates approval of this plan.',
          'Reject',
        ) ||
        !mounted) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<ConnectionController>().api.rejectPlan(
        widget.projectId,
        widget.planId,
      );
      if (mounted) await _load();
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e.toString();
          _busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final plan = _plan;
    const sections = {
      'manifest_changes': 'MANIFEST',
      'permission_changes': 'PERMISSIONS',
      'component_changes': 'COMPONENTS',
      'smali_integration_points': 'SMALI INTEGRATION',
      'behavioral_changes': 'BEHAVIOR',
      'network_destinations': 'NETWORK DESTINATIONS',
      'data_categories': 'DATA CATEGORIES',
      'runtime_triggers': 'RUNTIME TRIGGERS',
      'background_behavior': 'BACKGROUND BEHAVIOR',
      'compatibility_concerns': 'COMPATIBILITY',
      'risks': 'RISKS',
      'validation_steps': 'VALIDATION STEPS',
      'expected_test_results': 'EXPECTED TEST RESULTS',
      'unsupported_aspects': 'UNSUPPORTED',
    };
    return ReviewLayout(
      title: 'REVIEW PLAN',
      busy: _busy,
      error: _error,
      onRefresh: _load,
      children: [
        if (plan != null) ...[
          Section(title: 'REQUEST', child: SelectableText(plan.userRequest)),
          Section(
            title: 'OUTCOME',
            child: SelectableText(plan.intendedOutcome),
          ),
          Section(
            title: 'EXACT PLAN',
            child: SelectableText(
              'ID: ${plan.planId}\nSHA-256: ${plan.planHash}\nRevision: ${plan.workspaceRevision}\nProvider: ${plan.provider ?? 'unknown'} · ${plan.model ?? 'unknown'}\nStatus: ${plan.stale
                  ? 'STALE — create a new plan'
                  : plan.approved
                  ? 'Approved'
                  : 'Awaiting approval'}',
            ),
          ),
          Section(
            title: 'AFFECTED FILES',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: plan.fileChanges
                  .map(
                    (file) => Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: SelectableText(
                        '${file.operation} · ${file.relativePath}\n${file.description}',
                      ),
                    ),
                  )
                  .toList(),
            ),
          ),
          for (final section in sections.entries)
            if ((plan.raw[section.key] as List? ?? []).isNotEmpty)
              Section(
                title: section.value,
                child: SelectableText(
                  (plan.raw[section.key] as List)
                      .map((v) => '• $v')
                      .join('\n\n'),
                ),
              ),
          const Section(
            title: 'WORKFLOW',
            child: Text(
              'Plan approval does not modify files. Generate a patch, review its deterministic diff, approve that exact patch, then apply. Signing is separate.',
            ),
          ),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              NoirGhostButton(
                label: 'Reject plan',
                onPressed: _busy ? null : _reject,
              ),
              if (!plan.approved)
                NoirPrimaryButton(
                  label: 'Approve plan',
                  onPressed: _busy || plan.stale || plan.planHash.length != 64
                      ? null
                      : _approve,
                ),
              if (plan.approved)
                NoirPrimaryButton(
                  label: 'Generate patch',
                  onPressed: _busy || plan.stale ? null : _generate,
                ),
              NoirGhostButton(
                label: 'History',
                onPressed: _busy
                    ? null
                    : () =>
                          context.push('/project/${widget.projectId}/history'),
              ),
            ],
          ),
        ],
      ],
    );
  }
}
