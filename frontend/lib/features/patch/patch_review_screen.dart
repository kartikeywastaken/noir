import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';
import '../../data/models/models.dart';

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
  PatchSet? _patch;
  List<PatchDiffEntry> _diff = [];
  bool _busy = true;
  bool _diffReady = false;
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
      _patch = null;
      _diff = [];
      _diffReady = false;
    });
    try {
      final api = context.read<ConnectionController>().api;
      final patch = await api.getPatch(widget.projectId, widget.patchId);
      if (!mounted) return;
      setState(() => _patch = patch);
      if (!patch.applied && !patch.stale) {
        final diff = await api.getPatchDiff(widget.projectId, widget.patchId);
        final paths = diff.map((d) => d.path).toSet();
        if (mounted) {
          setState(() {
            _diff = diff;
            _diffReady =
                diff.isNotEmpty &&
                patch.operations.every((op) => paths.contains(op.relativePath));
            if (!_diffReady) {
              _error =
                  'Incomplete diff. Approval is blocked; refresh to try again.';
            }
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _action(String action) async {
    final patch = _patch!;
    final message = action == 'undo'
        ? 'Restore files for patch ${patch.patchId}? The backend will reject undo if the workspace has moved on.'
        : 'Patch ${patch.patchId}\nRevision ${patch.workspaceRevision}\nSHA-256: ${patch.patchHash}';
    if (!await confirmAction(
          context,
          '${action.toUpperCase()} exact patch?',
          message,
          action,
        ) ||
        !mounted) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final api = context.read<ConnectionController>().api;
      if (action == 'approve') {
        await api.approvePatch(
          widget.projectId,
          patch.patchId,
          patch.patchHash,
        );
      }
      if (action == 'apply') {
        await api.applyPatch(widget.projectId, patch.patchId);
      }
      if (action == 'undo') {
        await api.undoPatch(widget.projectId, patch.patchId);
      }
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
    final patch = _patch;
    final canReview =
        !_busy &&
        _diffReady &&
        patch != null &&
        !patch.applied &&
        !patch.stale &&
        patch.patchHash.length == 64;
    return ReviewLayout(
      title: 'REVIEW PATCH',
      busy: _busy,
      error: _error,
      onRefresh: _load,
      children: [
        if (patch != null) ...[
          Section(
            title: 'EXACT PATCH',
            child: SelectableText(
              'ID: ${patch.patchId}\nPlan: ${patch.planId}\nRevision: ${patch.workspaceRevision}\nSHA-256: ${patch.patchHash}\nStatus: ${patch.applied
                  ? 'Applied'
                  : patch.stale
                  ? 'STALE — generate a new plan and patch'
                  : patch.approved
                  ? 'Approved; not applied'
                  : 'Awaiting approval'}',
            ),
          ),
          Section(
            title: 'OPERATIONS',
            child: SelectableText(
              patch.operations
                  .map((op) => '${op.operation} · ${op.relativePath}')
                  .join('\n'),
            ),
          ),
          if (_diff.isNotEmpty)
            const Text(
              'Deterministic backend preview. − removes a line; + adds a line.',
            ),
          for (final entry in _diff)
            Section(
              title: entry.path,
              child: SelectableText(
                entry.preview.isEmpty
                    ? '(No textual difference)'
                    : entry.preview,
                style: NoirTypography.codeSm,
              ),
            ),
          if (patch.applied)
            const Section(
              title: 'APPLIED',
              child: Text(
                'The patch is recorded in the workspace. See the audit report for its history; preview is not rerun against already-modified files.',
              ),
            ),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              if (!patch.approved && !patch.applied)
                NoirPrimaryButton(
                  label: 'Approve patch',
                  onPressed: canReview ? () => _action('approve') : null,
                ),
              if (patch.approved && !patch.applied)
                NoirPrimaryButton(
                  label: 'Apply patch',
                  onPressed: canReview ? () => _action('apply') : null,
                ),
              if (patch.applied) ...[
                NoirPrimaryButton(
                  label: 'Validate & build',
                  onPressed: _busy
                      ? null
                      : () =>
                            context.push('/project/${widget.projectId}/build'),
                ),
                NoirGhostButton(
                  label: 'Undo patch',
                  onPressed: _busy ? null : () => _action('undo'),
                ),
              ],
            ],
          ),
        ],
      ],
    );
  }
}
