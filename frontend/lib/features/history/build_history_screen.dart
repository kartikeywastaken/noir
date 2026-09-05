import 'dart:async';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/state/signing_controller.dart';
import '../../core/widgets/noir_bottom_nav.dart';
import '../../core/widgets/review_layout.dart';
import '../../data/models/models.dart';
import '../../data/api/transfer_progress.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/widgets/transfer_bar.dart';

class BuildHistoryScreen extends StatefulWidget {
  const BuildHistoryScreen({super.key});
  @override
  State<BuildHistoryScreen> createState() => _BuildHistoryScreenState();
}

class _BuildHistoryScreenState extends State<BuildHistoryScreen> {
  final List<Map<String, dynamic>> _builds = [];
  List<JobInfo> _jobs = [];
  int _total = 0;
  bool _busy = false;
  String? _error;
  String? _saved;
  TransferProgress? _downloadProgress;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load({bool more = false}) async {
    if (_busy) return;
    _timer?.cancel();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final connection = context.read<ConnectionController>();
      await connection.ready;
      if (!connection.hasToken) {
        throw Exception('Activate your invitation to view your build history.');
      }
      final data = await connection.api.listBuildHistory(
        offset: more ? _builds.length : 0,
      );
      final jobs = await connection.api.listJobs();
      if (!mounted) return;
      setState(() {
        if (!more) _builds.clear();
        for (final build
            in (data['builds'] as List).cast<Map<String, dynamic>>()) {
          if (!_builds.any((old) => old['build_id'] == build['build_id'])) {
            _builds.add(build);
          }
        }
        _total = data['total'] as int;
        _jobs = jobs
            .where((job) => job.resultData.containsKey('operation'))
            .toList();
      });
      if (jobs.any((job) => !job.isTerminal)) {
        _timer = Timer(const Duration(seconds: 3), () => _load());
      }
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _cancel(JobInfo job) async {
    if (!await confirmAction(
          context,
          'Cancel operation?',
          'The worker may finish before cancellation takes effect.',
          'Request cancellation',
        ) ||
        !mounted) {
      return;
    }
    try {
      await context.read<ConnectionController>().api.cancelJob(job.jobId);
      if (mounted) await _load();
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    }
  }

  Future<void> _download(BuildResult build) async {
    _timer?.cancel();
    final signing = SigningController(context.read<ConnectionController>().api);
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
      _downloadProgress = null;
    });
    try {
      final bytes = await signing.verifiedDownload(
        build.projectId,
        build,
        onProgress: (p) {
          if (mounted) setState(() => _downloadProgress = p);
        },
      );
      if (!mounted) return;
      final saved = await FilePicker.saveFile(
        fileName: 'noir-${build.projectId}-${build.buildId}-signed.apk',
        mimeType: 'application/vnd.android.package-archive',
        bytes: bytes,
      );
      if (mounted) {
        setState(
          () => _saved = saved == null
              ? 'Save cancelled.'
              : 'Signature and SHA-256 verified. Saved to:\n$saved',
        );
      }
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    } finally {
      signing.dispose();
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _operation(JobInfo job) {
    final opName = job.resultData['operation'] ?? job.stage;
    final state = job.state;
    final isSuccess = state == 'succeeded';
    final isFailed = state == 'failed' || state == 'error';

    final badgeFg = isSuccess
        ? NoirColors.primaryFixed
        : isFailed
        ? NoirColors.error
        : NoirColors.tertiaryFixed;

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainerHigh.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Icon(
            isSuccess
                ? Icons.check_circle_outline_rounded
                : isFailed
                ? Icons.error_outline_rounded
                : Icons.hourglass_top_rounded,
            size: 20,
            color: badgeFg,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        '$opName · $state',
                        style: const TextStyle(
                          fontFamily: 'JetBrainsMono',
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: NoirColors.primary,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  'Stage: ${job.stage}',
                  style: const TextStyle(
                    fontFamily: 'Inter',
                    fontSize: 12,
                    color: NoirColors.onSurfaceVariant,
                  ),
                ),
                if (job.errorMessage != null) ...[
                  const SizedBox(height: 3),
                  Text(
                    job.errorMessage!,
                    style: const TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 11,
                      color: NoirColors.error,
                    ),
                  ),
                ],
                if (job.resultData['cancel_requested'] == true) ...[
                  const SizedBox(height: 3),
                  const Text(
                    'Cancellation requested.',
                    style: TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 11,
                      color: NoirColors.tertiaryFixed,
                    ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: 8),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              FilledButton.tonal(
                style: FilledButton.styleFrom(
                  backgroundColor: NoirColors.primaryFixed.withValues(alpha: 0.15),
                  foregroundColor: NoirColors.primaryFixed,
                  elevation: 0,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(8),
                  ),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  minimumSize: const Size(0, 32),
                ),
                onPressed: () => context.go('/workflow/${job.projectId}'),
                child: const Text(
                  'Resume',
                  style: TextStyle(fontWeight: FontWeight.w700, fontSize: 11),
                ),
              ),
              if (!job.isTerminal) ...[
                const SizedBox(width: 6),
                OutlinedButton(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: NoirColors.error,
                    side: BorderSide(color: NoirColors.error.withValues(alpha: 0.3)),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    minimumSize: const Size(0, 32),
                  ),
                  onPressed: _busy ? null : () => _cancel(job),
                  child: const Text('Cancel', style: TextStyle(fontSize: 11)),
                ),
              ],
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildCard(Map<String, dynamic> item) {
    final build = BuildResult.fromJson(item);
    final signed = build.signedApkHash != null;
    final status = !build.success
        ? 'FAILED'
        : signed
        ? 'SIGNED'
        : 'UNSIGNED';
    final created = DateTime.tryParse('${item['created_at']}')?.toLocal();
    final date = created == null
        ? ''
        : '${created.year}-${created.month.toString().padLeft(2, '0')}-${created.day.toString().padLeft(2, '0')} ${created.hour.toString().padLeft(2, '0')}:${created.minute.toString().padLeft(2, '0')}';
    final title =
        '${item['original_filename'] ?? item['package_name'] ?? 'APK build'}';
    final stale =
        item['current_revision'] != build.workspaceRevision ||
        item['project_dirty'] == true;

    final (badgeBg, badgeFg) = switch (status) {
      'SIGNED' => (
        NoirColors.primaryFixed.withValues(alpha: 0.9),
        NoirColors.onPrimaryFixed,
      ),
      'FAILED' => (
        NoirColors.error.withValues(alpha: 0.9),
        NoirColors.onError,
      ),
      _ => (
        NoirColors.surfaceVariant,
        NoirColors.onSurfaceVariant,
      ),
    };

    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: status == 'FAILED'
              ? NoirColors.error.withValues(alpha: 0.25)
              : Colors.white.withValues(alpha: 0.05),
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Row(
                    children: [
                      Icon(
                        signed
                            ? Icons.verified_outlined
                            : !build.success
                            ? Icons.error_outline
                            : Icons.inventory_2_outlined,
                        size: 20,
                        color: signed
                            ? NoirColors.primaryFixed
                            : !build.success
                            ? NoirColors.error
                            : NoirColors.outline,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '$title · $status',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontFamily: 'JetBrainsMono',
                            fontSize: 14,
                            fontWeight: FontWeight.w700,
                            color: NoirColors.primary,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: badgeBg,
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    '[$status]',
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
            if (item['package_name'] != null &&
                item['package_name'].toString().isNotEmpty &&
                item['package_name'] != title) ...[
              const SizedBox(height: 6),
              Text(
                '${item['package_name']}',
                style: const TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 12,
                  color: NoirColors.onSurfaceVariant,
                ),
              ),
            ],
            const SizedBox(height: 6),
            Text(
              '$date · revision ${build.workspaceRevision}',
              style: const TextStyle(
                fontFamily: 'Inter',
                fontSize: 12,
                color: NoirColors.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 4),
            SelectableText(
              'Build ${build.buildId}',
              style: const TextStyle(
                fontFamily: 'JetBrainsMono',
                fontSize: 11,
                color: NoirColors.outline,
              ),
            ),
            if (build.errorMessage != null) ...[
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: NoirColors.errorContainer.withValues(alpha: 0.3),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: NoirColors.error.withValues(alpha: 0.3)),
                ),
                child: Text(
                  build.errorMessage!,
                  style: const TextStyle(
                    fontFamily: 'Inter',
                    fontSize: 12,
                    color: NoirColors.error,
                  ),
                ),
              ),
            ],
            if (stale && !signed && build.success) ...[
              const SizedBox(height: 8),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: NoirColors.surfaceContainerHigh,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Text(
                  'Older or edited workspace. Rebuild before signing.',
                  style: TextStyle(
                    fontFamily: 'Inter',
                    fontSize: 12,
                    color: NoirColors.onSurfaceVariant,
                  ),
                ),
              ),
            ],
            const SizedBox(height: 14),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (signed)
                  ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: NoirColors.primaryFixed,
                      foregroundColor: NoirColors.onPrimaryFixed,
                      elevation: 0,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    ),
                    icon: const Icon(Icons.download_rounded, size: 16),
                    label: const Text(
                      'Verify & download APK',
                      style: TextStyle(fontWeight: FontWeight.w700, fontSize: 12),
                    ),
                    onPressed: _busy ? null : () => _download(build),
                  ),
                if (build.success)
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: NoirColors.onSurface,
                      side: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
                      backgroundColor: NoirColors.surfaceContainerHigh.withValues(alpha: 0.5),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    ),
                    icon: const Icon(Icons.key_rounded, size: 16, color: NoirColors.tertiaryFixed),
                    label: Text(
                      signed ? 'Advanced build details' : 'Advanced signing',
                      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                    ),
                    onPressed: () => context.push(
                      '/project/${build.projectId}/sign?build=${Uri.encodeQueryComponent(build.buildId)}',
                    ),
                  ),
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: NoirColors.onSurface,
                    side: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
                    backgroundColor: NoirColors.surfaceContainerHigh.withValues(alpha: 0.5),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                  icon: const Icon(Icons.play_arrow_rounded, size: 16, color: NoirColors.primaryFixed),
                  label: const Text(
                    'Resume workflow',
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                  ),
                  onPressed: () => context.go('/workflow/${build.projectId}'),
                ),
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: NoirColors.onSurface,
                    side: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
                    backgroundColor: NoirColors.surfaceContainerHigh.withValues(alpha: 0.5),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                  icon: const Icon(Icons.description_outlined, size: 16, color: NoirColors.outline),
                  label: const Text(
                    'Audit report',
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                  ),
                  onPressed: () => context.push('/project/${build.projectId}/audit'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHeaderInfo() {
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: NoirColors.primaryFixed.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(10),
            ),
            child: const Icon(
              Icons.shield_outlined,
              color: NoirColors.primaryFixed,
              size: 20,
            ),
          ),
          const SizedBox(width: 14),
          const Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'YOUR PREVIOUS BUILDS',
                  style: TextStyle(
                    fontFamily: 'JetBrainsMono',
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 1.2,
                    color: NoirColors.primary,
                  ),
                ),
                SizedBox(height: 4),
                Text(
                  'Only your private workspace is shown. Download signed APKs again, resume a workflow, or review its audit report.',
                  style: TextStyle(
                    fontFamily: 'Inter',
                    fontSize: 13,
                    color: NoirColors.onSurfaceVariant,
                    height: 1.4,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildEmptyState() {
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.symmetric(vertical: 36, horizontal: 20),
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainer,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding: const EdgeInsets.all(14),
            decoration: const BoxDecoration(
              color: NoirColors.surfaceContainerHigh,
              shape: BoxShape.circle,
            ),
            child: const Icon(
              Icons.build_circle_outlined,
              size: 32,
              color: NoirColors.outline,
            ),
          ),
          const SizedBox(height: 14),
          const Text(
            'NO BUILDS YET',
            style: TextStyle(
              fontFamily: 'JetBrainsMono',
              fontSize: 13,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.2,
              color: NoirColors.primary,
            ),
          ),
          const SizedBox(height: 6),
          const Text(
            'Import an APK and rebuild it. Your builds will appear here.',
            textAlign: TextAlign.center,
            style: TextStyle(
              fontFamily: 'Inter',
              fontSize: 13,
              color: NoirColors.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final active = _jobs.where((job) => !job.isTerminal).toList();
    final imports = _jobs
        .where(
          (job) =>
              job.isTerminal &&
              (job.resultData['operation'] == 'import' ||
                  job.resultData['operation'] == 'workflow_prepare' ||
                  job.state != 'succeeded'),
        )
        .toList();
    return ReviewLayout(
      title: 'BUILD HISTORY',
      busy: _busy,
      error: _error,
      onRefresh: () => _load(),
      bottomNavigationBar: NoirBottomNav(
        currentIndex: 1,
        onTap: (index) {
          if (index == 0) context.go('/');
          if (index == 2) context.go('/settings');
        },
      ),
      children: [
        if (_downloadProgress != null)
          TransferBar(
            progress: _downloadProgress!,
            title: _downloadProgress!.fraction == 1
                ? 'Received · verifying and saving'
                : 'Downloading APK',
          ),
        _buildHeaderInfo(),
        if (active.isNotEmpty)
          Container(
            margin: const EdgeInsets.only(bottom: 16),
            decoration: BoxDecoration(
              color: NoirColors.surfaceContainer,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: NoirColors.tertiaryFixed.withValues(alpha: 0.3)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 14, 16, 10),
                  child: Row(
                    children: [
                      const Icon(Icons.sync, size: 18, color: NoirColors.tertiaryFixed),
                      const SizedBox(width: 8),
                      const Text(
                        'IN PROGRESS',
                        style: TextStyle(
                          fontFamily: 'JetBrainsMono',
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 1.2,
                          color: NoirColors.tertiaryFixed,
                        ),
                      ),
                      const Spacer(),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(
                          color: NoirColors.tertiaryFixed.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          '${active.length} active',
                          style: const TextStyle(
                            fontFamily: 'JetBrainsMono',
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: NoirColors.tertiaryFixed,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const Divider(height: 1, color: Colors.white10),
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    children: [for (final job in active) _operation(job)],
                  ),
                ),
              ],
            ),
          ),
        if (_saved != null)
          Container(
            margin: const EdgeInsets.only(bottom: 16),
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: NoirColors.surfaceContainer,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: NoirColors.primaryFixed.withValues(alpha: 0.3)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Row(
                  children: [
                    Icon(Icons.check_circle, color: NoirColors.primaryFixed, size: 18),
                    SizedBox(width: 8),
                    Text(
                      'DOWNLOAD',
                      style: TextStyle(
                        fontFamily: 'JetBrainsMono',
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 1.2,
                        color: NoirColors.primaryFixed,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                SelectableText(
                  _saved!,
                  style: const TextStyle(
                    fontFamily: 'JetBrainsMono',
                    fontSize: 12,
                    color: NoirColors.onSurface,
                  ),
                ),
              ],
            ),
          ),
        if (_builds.isEmpty && !_busy && _error == null)
          _buildEmptyState(),
        for (final item in _builds) _buildCard(item),
        if (_builds.length < _total)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Center(
              child: OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: NoirColors.primary,
                  side: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
                  backgroundColor: NoirColors.surfaceContainer,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
                ),
                icon: const Icon(Icons.expand_more_rounded, size: 18, color: NoirColors.primaryFixed),
                label: const Text(
                  'Load older builds',
                  style: TextStyle(fontFamily: 'Inter', fontWeight: FontWeight.w600, fontSize: 13),
                ),
                onPressed: _busy ? null : () => _load(more: true),
              ),
            ),
          ),
        if (imports.isNotEmpty)
          Container(
            margin: const EdgeInsets.only(bottom: 16),
            decoration: BoxDecoration(
              color: NoirColors.surfaceContainer,
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
            ),
            child: Theme(
              data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
              child: ExpansionTile(
                tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
                initiallyExpanded: true,
                iconColor: NoirColors.primaryFixed,
                collapsedIconColor: NoirColors.outline,
                title: Row(
                  children: [
                    const Icon(
                      Icons.history_rounded,
                      size: 18,
                      color: NoirColors.primaryFixed,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      '${imports.length} completed operations',
                      style: const TextStyle(
                        fontFamily: 'Inter',
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        color: NoirColors.primary,
                      ),
                    ),
                  ],
                ),
                children: [for (final job in imports) _operation(job)],
              ),
            ),
          ),
      ],
    );
  }
}
