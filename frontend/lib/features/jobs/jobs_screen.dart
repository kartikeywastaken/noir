import 'dart:async';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/widgets/review_layout.dart';
import '../../data/models/models.dart';

class JobsScreen extends StatefulWidget {
  const JobsScreen({super.key});
  @override
  State<JobsScreen> createState() => _JobsScreenState();
}

class _JobsScreenState extends State<JobsScreen> {
  List<JobInfo> _jobs = [];
  bool _busy = false;
  String? _error;
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

  Future<void> _load() async {
    if (_busy) return;
    _timer?.cancel();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final jobs = await context.read<ConnectionController>().api.listJobs();
      jobs.sort((a, b) => b.createdAt.compareTo(a.createdAt));
      if (mounted) {
        setState(() => _jobs = jobs);
        if (jobs.any((j) => !j.isTerminal)) {
          _timer = Timer(const Duration(seconds: 3), _load);
        }
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _cancel(JobInfo job) async {
    if (!await confirmAction(
          context,
          'Cancel job?',
          'Request cancellation of ${job.jobId}? The worker may finish before cancellation takes effect.',
          'Request cancellation',
        ) ||
        !mounted) {
      return;
    }
    try {
      await context.read<ConnectionController>().api.cancelJob(job.jobId);
      if (mounted) await _load();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) => ReviewLayout(
    title: 'BACKEND JOBS',
    busy: _busy,
    error: _error,
    onRefresh: _load,
    children: [
      if (_jobs.isEmpty && !_busy) const Text('No jobs recorded.'),
      for (final job in _jobs)
        Section(
          title: '${job.resultData['operation'] ?? job.stage} · ${job.state}',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SelectableText(
                'Job: ${job.jobId}\nProject: ${job.projectId}\nStage: ${job.stage}',
              ),
              if (job.errorMessage != null) SelectableText(job.errorMessage!),
              if (job.resultData['cancel_requested'] == true)
                const Text('Cancellation requested.'),
              Wrap(
                spacing: 8,
                children: [
                  if (job.projectId.isNotEmpty &&
                      (job.resultData['operation'] != 'import' ||
                          job.state == 'succeeded'))
                    TextButton(
                      onPressed: () =>
                          context.push('/project/${job.projectId}'),
                      child: const Text('Open workspace'),
                    ),
                  if (!job.isTerminal)
                    TextButton(
                      onPressed: () => _cancel(job),
                      child: const Text('Cancel'),
                    ),
                ],
              ),
            ],
          ),
        ),
    ],
  );
}
