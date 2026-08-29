import 'dart:async';
import 'package:file_picker/file_picker.dart';
import 'package:uuid/uuid.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/transfer_progress.dart';
import '../../data/models/models.dart';
import 'safe_notifier.dart';

/// Mutations are explicit; only status reads retry after a disconnection.
class WorkflowController extends SafeNotifier {
  WorkflowController(this.api);
  final NoirApiClient api;
  ProjectInfo? project;
  JobInfo? job;
  ChangePlan? plan;
  PatchSet? patch;
  List<PatchDiffEntry> diff = [];
  BuildResult? build;
  TransferProgress? upload;
  String filename = '', request = '';
  String? error;
  bool busy = false, uploading = false, previewReady = false, _polling = false;
  int _misses = 0, _generation = 0;
  Timer? _timer;
  String get operation => '${job?.resultData['operation'] ?? ''}';
  bool get working => busy || uploading || (job != null && !job!.isTerminal);
  int get step => build != null || operation == 'workflow_finish'
      ? 2
      : project != null
      ? 1
      : 0;
  bool get retryFinish =>
      operation == 'workflow_finish' &&
      job!.isTerminal &&
      job!.state != 'succeeded' &&
      plan != null &&
      patch != null;

  void reset() {
    if (working) return;
    _generation++;
    _timer?.cancel();
    project = null;
    job = null;
    plan = null;
    patch = null;
    build = null;
    diff = [];
    upload = null;
    filename = '';
    request = '';
    error = null;
    previewReady = false;
    notifyListeners();
  }

  Future<void> importFile(PlatformFile file) async {
    if (working) return;
    reset();
    uploading = true;
    filename = file.name;
    notifyListeners();
    try {
      job = await api.importApkStream(
        file.name,
        await file.length(),
        file.readAsByteStream(),
        idempotencyKey: const Uuid().v4(),
        onProgress: (value) {
          upload = value;
          notifyListeners();
        },
      );
      uploading = false;
      await refresh();
    } catch (e) {
      error = '$e';
    } finally {
      uploading = false;
      notifyListeners();
    }
  }

  Future<void> prepare(String text) async {
    if (working || project == null || text.trim().isEmpty) return;
    busy = true;
    error = null;
    previewReady = false;
    plan = null;
    patch = null;
    diff = [];
    request = text.trim();
    notifyListeners();
    try {
      job = await api.prepareWorkflow(
        project!.id,
        request,
        project!.workspaceRevision,
        const Uuid().v4(),
      );
      await refresh();
    } catch (e) {
      error = '$e';
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> approveAndBuild() async {
    if (working ||
        plan == null ||
        patch == null ||
        (!previewReady && !retryFinish)) {
      return;
    }
    busy = true;
    error = null;
    notifyListeners();
    try {
      job = await api.finishWorkflow(
        project!.id,
        plan!,
        patch!,
        const Uuid().v4(),
      );
      previewReady = false;
      await refresh();
    } catch (e) {
      error = '$e';
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  void editRequest() {
    if (working) return;
    previewReady = false;
    plan = null;
    patch = null;
    diff = [];
    job = null;
    error = null;
    notifyListeners();
  }

  Future<void> resume(String projectId) async {
    if (uploading || busy) return;
    final generation = ++_generation;
    _timer?.cancel();
    busy = true;
    error = null;
    previewReady = false;
    build = null;
    project = null;
    filename = '';
    request = '';
    plan = null;
    patch = null;
    diff = [];
    upload = null;
    job = null;
    notifyListeners();
    try {
      final jobs = await api.listJobs(projectId: projectId);
      if (generation != _generation || disposed) return;
      job = jobs
          .where((j) => j.resultData.containsKey('operation'))
          .firstOrNull;
      if (job?.resultData['payload'] case final Map payload) {
        request = '${payload['user_request'] ?? ''}';
      }
      if (job?.state == 'succeeded' || operation != 'import') {
        project = await api.getProject(projectId);
        filename = project!.originalFilename;
      } else {
        project = null;
      }
      if (_polling) {
        _timer = Timer(const Duration(seconds: 3), refresh);
      } else {
        await refresh();
      }
    } catch (e) {
      error = '$e';
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> refresh() async {
    if (_polling || job == null || disposed) return;
    final generation = _generation;
    final id = job!.jobId;
    _timer?.cancel();
    _polling = true;
    try {
      final latest = await api.getJob(id);
      if (disposed || generation != _generation) return;
      job = latest;
      error = null;
      _misses = 0;
      if (latest.state == 'succeeded') {
        final result = Map<String, dynamic>.from(
          latest.resultData['result'] as Map? ?? {},
        );
        project = await api.getProject(latest.projectId);
        filename = project!.originalFilename;
        if (operation == 'workflow_prepare') {
          plan = await api.getPlan(project!.id, '${result['plan_id']}');
          patch = await api.getPatch(project!.id, '${result['patch_id']}');
          diff = await api.getPatchDiff(project!.id, patch!.patchId);
          request = plan!.userRequest;
          previewReady = diff.isNotEmpty && !plan!.stale && !patch!.stale;
          if (!previewReady) {
            throw StateError(
              'Preview is empty or stale. Edit your request and try again.',
            );
          }
        } else if (operation == 'workflow_finish') {
          build = (await api.listBuilds(
            project!.id,
          )).where((b) => b.buildId == result['build_id']).firstOrNull;
          if (build?.signedApkHash == null) {
            throw StateError('No signed artifact was recorded. Open History.');
          }
        }
      } else if (latest.isTerminal) {
        error =
            latest.errorMessage ??
            'Operation ${latest.state}. Your completed work is retained.';
        if (operation == 'workflow_finish') {
          final payload = latest.resultData['payload'] as Map;
          plan = await api.getPlan(latest.projectId, '${payload['plan_id']}');
          patch = await api.getPatch(
            latest.projectId,
            '${payload['patch_id']}',
          );
        }
      }
    } catch (e) {
      if (generation == _generation) {
        error = 'Status unavailable: $e';
        _misses++;
      }
    } finally {
      _polling = false;
      if (!disposed && generation == _generation) {
        if (job != null && !job!.isTerminal) {
          _timer = Timer(
            Duration(seconds: (_misses + 1).clamp(1, 5) * 3),
            refresh,
          );
        }
        notifyListeners();
      }
    }
  }

  Future<void> cancel() async {
    if (job == null || job!.isTerminal) return;
    try {
      job = await api.cancelJob(job!.jobId);
      await refresh();
    } catch (e) {
      error = '$e';
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}
