import 'dart:async';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import 'package:uuid/uuid.dart';
import '../../core/state/connection_controller.dart';
import '../../core/state/projects_controller.dart';
import '../../data/models/models.dart';

Future<void> showImportFlow(BuildContext context, PlatformFile file) async {
  final id = await showDialog<String>(
    context: context,
    barrierDismissible: false,
    builder: (_) => _ImportDialog(file: file),
  );
  if (id != null && context.mounted) {
    await context.read<ProjectsController>().loadProjects();
    if (context.mounted) context.push('/project/$id');
  }
}

class _ImportDialog extends StatefulWidget {
  const _ImportDialog({required this.file});
  final PlatformFile file;
  @override
  State<_ImportDialog> createState() => _ImportDialogState();
}

class _ImportDialogState extends State<_ImportDialog> {
  final _key = const Uuid().v4();
  bool _authorized = false;
  bool _uploading = false;
  bool _polling = false;
  bool _cancelling = false;
  String? _error;
  JobInfo? _job;
  Timer? _timer;
  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _start() async {
    if (!_authorized || _uploading || _job != null) return;
    setState(() {
      _uploading = true;
      _error = null;
    });
    try {
      final api = context.read<ConnectionController>().api;
      final length = await widget.file.length();
      final job = await api.importApkStream(
        widget.file.name,
        length,
        widget.file.readAsByteStream(),
        idempotencyKey: _key,
      );
      if (!mounted) return;
      setState(() {
        _job = job;
        _uploading = false;
      });
      await _poll();
    } catch (e) {
      if (mounted) {
        setState(() {
          _uploading = false;
          _error = e.toString();
        });
      }
    }
  }

  Future<void> _poll() async {
    if (_polling || _job == null || !mounted) return;
    _timer?.cancel();
    _polling = true;
    try {
      final job = await context.read<ConnectionController>().api.getJob(
        _job!.jobId,
      );
      if (!mounted) return;
      setState(() {
        _job = job;
        _error = null;
      });
      if (job.state == 'succeeded') {
        final result = job.resultData['result'];
        final id = job.projectId.isNotEmpty
            ? job.projectId
            : result is Map
            ? result['project_id'] as String?
            : null;
        if (id == null) {
          throw StateError(
            'Import completed but no project ID was returned. Open Jobs.',
          );
        }
        Navigator.pop(context, id);
      } else if (job.isTerminal) {
        setState(() => _error = job.errorMessage ?? 'Job ${job.state}');
      } else {
        _timer = Timer(const Duration(seconds: 2), _poll);
      }
    } catch (e) {
      if (mounted) {
        setState(() => _error = 'Monitoring paused: $e. Refresh to reconnect.');
      }
    } finally {
      _polling = false;
    }
  }

  Future<void> _cancel() async {
    setState(() => _cancelling = true);
    try {
      final job = await context.read<ConnectionController>().api.cancelJob(
        _job!.jobId,
      );
      if (mounted) {
        setState(() => _job = job);
        await _poll();
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _cancelling = false);
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !_uploading,
    child: AlertDialog(
      title: const Text('Import APK'),
      content: SizedBox(
        width: 450,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.file.name),
              const SizedBox(height: 12),
              if (_job == null)
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'I am authorized to decode and analyze this APK.',
                  ),
                  value: _authorized,
                  onChanged: _uploading
                      ? null
                      : (v) => setState(() => _authorized = v ?? false),
                ),
              Text(
                _uploading
                    ? 'Uploading APK…'
                    : _job == null
                    ? 'Awaiting authorization'
                    : 'Job: ${_job!.jobId}\nStage: ${_job!.stage}\nState: ${_job!.state}',
              ),
              if (_uploading || _job?.isTerminal == false)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 12),
                  child: LinearProgressIndicator(),
                ),
              if (_job?.resultData['cancel_requested'] == true)
                const Text('Cancellation requested; awaiting worker.'),
              if (_error != null) SelectableText(_error!),
              if (_job != null)
                const Text(
                  'Closing this window does not cancel the backend job. Reopen it in Jobs.',
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _uploading ? null : () => Navigator.pop(context),
          child: const Text('Close'),
        ),
        if (_job == null)
          FilledButton(
            onPressed: _authorized && !_uploading ? _start : null,
            child: const Text('Import'),
          ),
        if (_job != null)
          TextButton(onPressed: _poll, child: const Text('Refresh')),
        if (_job?.isTerminal == false)
          TextButton(
            onPressed: _cancelling ? null : _cancel,
            child: const Text('Request cancellation'),
          ),
      ],
    ),
  );
}
