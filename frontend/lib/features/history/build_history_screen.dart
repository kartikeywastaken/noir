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

  Widget _operation(JobInfo job) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text('${job.resultData['operation'] ?? job.stage} · ${job.state}'),
      Text('Stage: ${job.stage}'),
      if (job.errorMessage != null) Text(job.errorMessage!),
      if (job.resultData['cancel_requested'] == true)
        const Text('Cancellation requested.'),
      Wrap(
        spacing: 8,
        children: [
          TextButton(
            onPressed: () => context.go('/workflow/${job.projectId}'),
            child: const Text('Resume'),
          ),
          if (!job.isTerminal)
            TextButton(
              onPressed: _busy ? null : () => _cancel(job),
              child: const Text('Cancel'),
            ),
        ],
      ),
    ],
  );

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
    return Section(
      title: '$title · $status',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${item['package_name'] ?? ''}'),
          Text('$date · revision ${build.workspaceRevision}'),
          SelectableText('Build ${build.buildId}'),
          if (build.errorMessage != null) Text(build.errorMessage!),
          if (stale && !signed && build.success)
            const Text('Older or edited workspace. Rebuild before signing.'),
          Wrap(
            spacing: 8,
            children: [
              if (signed)
                TextButton(
                  onPressed: _busy ? null : () => _download(build),
                  child: const Text('Verify & download APK'),
                ),
              if (build.success)
                TextButton(
                  onPressed: () => context.push(
                    '/project/${build.projectId}/sign?build=${Uri.encodeQueryComponent(build.buildId)}',
                  ),
                  child: Text(
                    signed ? 'Advanced build details' : 'Advanced signing',
                  ),
                ),
              TextButton(
                onPressed: () => context.go('/workflow/${build.projectId}'),
                child: const Text('Resume workflow'),
              ),
              TextButton(
                onPressed: () =>
                    context.push('/project/${build.projectId}/audit'),
                child: const Text('Audit report'),
              ),
            ],
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
        const Section(
          title: 'YOUR PREVIOUS BUILDS',
          child: Text(
            'Only your private workspace is shown. Download signed APKs again, resume a workflow, or review its audit report.',
          ),
        ),
        if (active.isNotEmpty)
          Section(
            title: 'IN PROGRESS',
            child: Column(
              children: [for (final job in active) _operation(job)],
            ),
          ),
        if (_saved != null)
          Section(title: 'DOWNLOAD', child: SelectableText(_saved!)),
        if (_builds.isEmpty && !_busy && _error == null)
          const Section(
            title: 'NO BUILDS YET',
            child: Text(
              'Import an APK and rebuild it. Your builds will appear here.',
            ),
          ),
        for (final item in _builds) _buildCard(item),
        if (_builds.length < _total)
          TextButton(
            onPressed: _busy ? null : () => _load(more: true),
            child: const Text('Load older builds'),
          ),
        if (imports.isNotEmpty)
          Section(
            title: 'RECENT OPERATIONS',
            child: ExpansionTile(
              title: Text('${imports.length} completed operations'),
              children: [for (final job in imports) _operation(job)],
            ),
          ),
      ],
    );
  }
}
