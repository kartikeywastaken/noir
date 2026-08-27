/// Workspace file tree and viewer controller.
import 'package:flutter/material.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';

class WorkspaceController extends ChangeNotifier {
  WorkspaceController(this._api);

  final NoirApiClient _api;

  String? _projectId;
  String? get projectId => _projectId;

  ProjectInfo? _project;
  ProjectInfo? get project => _project;

  AnalysisResult? _analysis;
  AnalysisResult? get analysis => _analysis;

  List<FileEntry> _files = [];
  List<FileEntry> get files => _files;

  String _currentSubdir = '';
  String get currentSubdir => _currentSubdir;

  String? _selectedFilePath;
  String? get selectedFilePath => _selectedFilePath;

  String? _fileContent;
  String? get fileContent => _fileContent;

  bool _loadingFiles = false;
  bool get loadingFiles => _loadingFiles;

  bool _loadingContent = false;
  bool get loadingContent => _loadingContent;

  String? _error;
  String? get error => _error;

  void setProjectId(String id) {
    _projectId = id;
    _files = [];
    _selectedFilePath = null;
    _fileContent = null;
    _currentSubdir = '';
    notifyListeners();
  }

  Future<void> loadProject() async {
    if (_projectId == null) return;
    try {
      _project = await _api.getProject(_projectId!);
      notifyListeners();
    } catch (_) {}
  }

  Future<void> loadAnalysis() async {
    if (_projectId == null) return;
    try {
      _analysis = await _api.getAnalysis(_projectId!);
      notifyListeners();
    } catch (_) {}
  }

  Future<void> loadFiles({String subdir = ''}) async {
    if (_projectId == null) return;
    _loadingFiles = true;
    _error = null;
    _currentSubdir = subdir;
    notifyListeners();

    try {
      _files = await _api.listFiles(_projectId!, subdir: subdir);
    } on ApiException catch (e) {
      _error = e.message;
    }

    _loadingFiles = false;
    notifyListeners();
  }

  Future<void> openFile(String path) async {
    if (_projectId == null) return;
    _selectedFilePath = path;
    _loadingContent = true;
    _fileContent = null;
    notifyListeners();

    try {
      _fileContent = await _api.readFile(_projectId!, path);
    } on ApiException catch (e) {
      _fileContent = '// Error loading file: ${e.message}';
    }

    _loadingContent = false;
    notifyListeners();
  }

  Future<List<Map<String, dynamic>>> searchFiles(String query) async {
    if (_projectId == null) return [];
    try {
      return await _api.searchFiles(_projectId!, query);
    } catch (_) {
      return [];
    }
  }
}
