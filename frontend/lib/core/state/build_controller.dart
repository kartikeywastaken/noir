/// Build controller — validation, build jobs, monitoring via SSE.
import 'dart:async';
import 'package:flutter/material.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';

class BuildController extends ChangeNotifier {
  BuildController(this._api);

  final NoirApiClient _api;

  ValidationResult? _validation;
  ValidationResult? get validation => _validation;

  JobInfo? _currentJob;
  JobInfo? get currentJob => _currentJob;

  List<BuildResult> _builds = [];
  List<BuildResult> get builds => _builds;

  List<String> _logEntries = [];
  List<String> get logEntries => List.unmodifiable(_logEntries);

  bool _validating = false;
  bool get validating => _validating;

  bool _building = false;
  bool get building => _building;

  String? _error;
  String? get error => _error;

  StreamSubscription? _eventSub;

  void clear() {
    _eventSub?.cancel();
    _validation = null;
    _currentJob = null;
    _logEntries = [];
    _error = null;
    notifyListeners();
  }

  Future<ValidationResult?> validate(String projectId) async {
    _validating = true;
    _error = null;
    notifyListeners();

    try {
      _validation = await _api.validate(projectId);
      return _validation;
    } on ApiException catch (e) {
      _error = e.message;
      return null;
    } finally {
      _validating = false;
      notifyListeners();
    }
  }

  Future<JobInfo?> startBuild(String projectId) async {
    _building = true;
    _error = null;
    _logEntries = [];
    notifyListeners();

    try {
      _currentJob = await _api.startBuild(projectId);
      _monitorJob(_currentJob!.jobId);
      return _currentJob;
    } on ApiException catch (e) {
      _error = e.message;
      _building = false;
      notifyListeners();
      return null;
    }
  }

  void _monitorJob(String jobId) {
    _eventSub?.cancel();
    _eventSub = _api.streamJobEvents(jobId).listen(
      (event) {
        _logEntries.add(event.message);
        notifyListeners();
      },
      onDone: () async {
        // Fetch final job state
        try {
          _currentJob = await _api.getJob(jobId);
        } catch (_) {}
        _building = false;
        notifyListeners();
      },
      onError: (e) {
        _error = e.toString();
        _building = false;
        notifyListeners();
      },
    );
  }

  Future<void> cancelBuild() async {
    if (_currentJob == null) return;
    try {
      await _api.cancelJob(_currentJob!.jobId);
      _logEntries.add('Cancellation requested...');
      notifyListeners();
    } catch (_) {}
  }

  Future<void> loadBuilds(String projectId) async {
    try {
      _builds = await _api.listBuilds(projectId);
      notifyListeners();
    } catch (_) {}
  }

  @override
  void dispose() {
    _eventSub?.cancel();
    super.dispose();
  }
}
