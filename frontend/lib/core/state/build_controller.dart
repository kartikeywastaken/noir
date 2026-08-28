import 'dart:async';
import 'package:uuid/uuid.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/models/models.dart';
import 'safe_notifier.dart';

/// Polling reads persisted jobs/events. No estimated percentages or automatic POST retries.
class BuildController extends SafeNotifier {
  BuildController(this.api);
  final NoirApiClient api;
  ValidationResult? validation;
  JobInfo? currentJob;
  List<BuildResult> builds = [];
  final List<String> _logs = [];
  List<String> get logEntries => List.unmodifiable(_logs);
  bool validating = false;
  bool building = false;
  bool monitoring = false;
  String? error;
  String? _projectId;
  String? _cursor;
  Timer? _timer;
  bool _polling = false;

  Future<ValidationResult?> validate(String id) async {
    if (validating || building) return null;
    validating = true;
    validation = null;
    error = null;
    notifyListeners();
    try {
      validation = await api.validate(id);
      return validation;
    } catch (e) {
      error = e.toString();
      return null;
    } finally {
      validating = false;
      notifyListeners();
    }
  }

  Future<JobInfo?> startBuild(String id) async {
    if (building || validating || validation?.passed != true) return null;
    building = true;
    error = null;
    notifyListeners();
    try {
      final project = await api.getProject(id);
      if (project.workspaceRevision != validation!.workspaceRevision) {
        validation = null;
        throw StateError('Workspace revision changed. Validate again.');
      }
      currentJob = await api.startBuild(id, idempotencyKey: const Uuid().v4());
      _projectId = id;
      _cursor = null;
      _logs.clear();
      await _poll();
      return currentJob;
    } catch (e) {
      error = e.toString();
      building = false;
      return null;
    } finally {
      notifyListeners();
    }
  }

  Future<void> loadBuilds(String id) async {
    _projectId = id;
    error = null;
    try {
      builds = await api.listBuilds(id);
      final jobs = (await api.listJobs(
        projectId: id,
      )).where((j) => j.resultData['operation'] == 'build').toList();
      jobs.sort((a, b) => b.createdAt.compareTo(a.createdAt));
      final active = jobs.where((j) => !j.isTerminal).toList();
      final job = active.isNotEmpty ? active.first : jobs.firstOrNull;
      if (currentJob?.jobId != job?.jobId) {
        _cursor = null;
        _logs.clear();
      }
      currentJob = job;
      if (job != null) await _poll();
    } catch (e) {
      error = e.toString();
    }
    notifyListeners();
  }

  Future<void> _poll() async {
    if (_polling || disposed || currentJob == null) return;
    _timer?.cancel();
    _polling = true;
    monitoring = true;
    try {
      currentJob = await api.getJob(currentJob!.jobId);
      building = !currentJob!.isTerminal;
      // Cursor-based read with deduplication handled by the backend.
      while (!disposed) {
        final events = await api.listEvents(_projectId!, after: _cursor);
        for (final event in events) {
          if (event.jobId == currentJob!.jobId) {
            _logs.add('[${event.severity}] ${event.message}');
          }
          _cursor = event.eventId;
        }
        if (events.length < 100) break;
      }
      if (currentJob!.isTerminal) {
        builds = await api.listBuilds(_projectId!);
        if (currentJob!.state != 'succeeded') {
          error = currentJob!.errorMessage ?? 'Job ${currentJob!.state}';
        }
      }
    } catch (e) {
      // Loss of monitoring is not cancellation or successful completion.
      error = 'Monitoring interrupted: $e. Refresh to reconnect.';
      monitoring = false;
    } finally {
      _polling = false;
      if (!disposed && currentJob?.isTerminal == false && monitoring) {
        _timer = Timer(const Duration(seconds: 2), _poll);
      }
      notifyListeners();
    }
  }

  Future<void> cancelBuild() async {
    if (currentJob == null || currentJob!.isTerminal) return;
    try {
      currentJob = await api.cancelJob(currentJob!.jobId);
      await _poll();
    } catch (e) {
      error = e.toString();
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}
