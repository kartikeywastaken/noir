/// Project list and selection controller.
import 'package:flutter/material.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';

class ProjectsController extends ChangeNotifier {
  ProjectsController(this._api);

  final NoirApiClient _api;

  List<ProjectInfo> _projects = [];
  List<ProjectInfo> get projects => _projects;

  bool _loading = false;
  bool get loading => _loading;

  String? _error;
  String? get error => _error;

  Future<void> loadProjects() async {
    _loading = true;
    _error = null;
    notifyListeners();

    try {
      _projects = await _api.listProjects();
      _projects.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    } on ApiException catch (e) {
      _error = e.message;
    } catch (e) {
      _error = e.toString();
    }

    _loading = false;
    notifyListeners();
  }

  Future<ProjectInfo?> refreshProject(String projectId) async {
    try {
      final project = await _api.getProject(projectId);
      final idx = _projects.indexWhere((p) => p.id == projectId);
      if (idx >= 0) {
        _projects[idx] = project;
      } else {
        _projects.insert(0, project);
      }
      notifyListeners();
      return project;
    } catch (_) {
      return null;
    }
  }

  Future<JobInfo> importApk(String filePath) async {
    return _api.importApk(filePath);
  }
}
