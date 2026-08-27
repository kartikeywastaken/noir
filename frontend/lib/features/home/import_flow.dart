/// Import flow — authorization dialog, upload with progress, job monitoring.
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../../core/state/connection_controller.dart';
import '../../core/state/projects_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/noir_button.dart';
import '../../data/models/models.dart';

void showImportFlow(BuildContext context, String filePath) {
  showDialog(
    context: context,
    barrierDismissible: false,
    builder: (_) => _ImportDialog(filePath: filePath),
  );
}

class _ImportDialog extends StatefulWidget {
  const _ImportDialog({required this.filePath});
  final String filePath;

  @override
  State<_ImportDialog> createState() => _ImportDialogState();
}

class _ImportDialogState extends State<_ImportDialog> {
  bool _authorized = false;
  bool _uploading = false;
  bool _monitoring = false;
  String _status = 'Awaiting authorization';
  String? _error;
  JobInfo? _job;
  Timer? _pollTimer;

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: NoirColors.surfaceContainerLow,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(4),
        side: BorderSide(color: Colors.white.withValues(alpha: 0.15)),
      ),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 400),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('IMPORT APK',
                  style: NoirTypography.labelCaps.copyWith(
                    color: NoirColors.primary,
                    letterSpacing: 2,
                  )),
              const SizedBox(height: 16),

              Text(
                widget.filePath.split('/').last,
                style: NoirTypography.codeSm.copyWith(color: NoirColors.onSurfaceVariant),
              ),
              const SizedBox(height: 16),

              if (!_uploading && !_monitoring) ...[
                // Authorization checkbox
                Row(
                  children: [
                    SizedBox(
                      width: 20,
                      height: 20,
                      child: Checkbox(
                        value: _authorized,
                        onChanged: (v) => setState(() => _authorized = v ?? false),
                        activeColor: NoirColors.primary,
                        checkColor: NoirColors.black,
                        side: BorderSide(color: Colors.white.withValues(alpha: 0.4)),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        'I am authorized to decode and analyze this APK',
                        style: NoirTypography.codeSm.copyWith(
                          color: NoirColors.onSurfaceVariant.withValues(alpha: 0.8),
                        ),
                      ),
                    ),
                  ],
                ),
              ],

              const SizedBox(height: 16),

              // Status
              Text(
                _status,
                style: NoirTypography.codeSm.copyWith(
                  color: NoirColors.onSurfaceVariant.withValues(alpha: 0.6),
                ),
              ),

              if (_uploading || _monitoring)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: LinearProgressIndicator(
                    color: NoirColors.primary,
                    backgroundColor: NoirColors.outlineVariant,
                    value: _monitoring ? null : null,
                  ),
                ),

              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(_error!,
                      style: NoirTypography.codeSm.copyWith(color: NoirColors.error)),
                ),

              const SizedBox(height: 24),

              // Actions
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  NoirGhostButton(
                    label: 'Cancel',
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                  const SizedBox(width: 12),
                  NoirPrimaryButton(
                    label: _uploading || _monitoring ? 'Importing...' : 'Import',
                    icon: Icons.file_download,
                    loading: _uploading || _monitoring,
                    onPressed: _authorized && !_uploading && !_monitoring
                        ? _startImport
                        : null,
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _startImport() async {
    setState(() {
      _uploading = true;
      _status = 'Uploading APK...';
      _error = null;
    });

    try {
      final projects = context.read<ProjectsController>();
      _job = await projects.importApk(widget.filePath);

      setState(() {
        _uploading = false;
        _monitoring = true;
        _status = 'Decoding... (job: ${_job!.jobId})';
      });

      _pollForCompletion();
    } catch (e) {
      setState(() {
        _uploading = false;
        _error = e.toString();
        _status = 'Import failed';
      });
    }
  }

  void _pollForCompletion() {
    final api = context.read<ConnectionController>().api;
    _pollTimer = Timer.periodic(const Duration(seconds: 2), (timer) async {
      try {
        final job = await api.getJob(_job!.jobId);
        if (job.isTerminal) {
          timer.cancel();
          if (job.state == 'succeeded') {
            if (!mounted) return;
            // Refresh projects and navigate
            await context.read<ProjectsController>().loadProjects();
            final projectId = job.resultData['project_id'] as String?;
            if (!mounted) return;
            Navigator.of(context).pop();
            if (projectId != null) {
              context.push('/project/$projectId');
            }
          } else {
            setState(() {
              _monitoring = false;
              _error = job.errorMessage ?? 'Import failed: ${job.state}';
              _status = 'Import failed';
            });
          }
        } else {
          setState(() {
            _status = 'Decoding... (${job.state})';
          });
        }
      } catch (_) {}
    });
  }
}
