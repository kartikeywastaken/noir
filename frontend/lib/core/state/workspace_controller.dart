import 'dart:convert';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';
import 'safe_notifier.dart';

class WorkspaceController extends SafeNotifier {
  WorkspaceController(this.api);
  final NoirApiClient api;
  String? projectId;
  ProjectInfo? project;
  AnalysisResult? analysis;
  ManualSession? session;
  List<FileEntry> files = [];
  String currentSubdir = '';
  String? selectedFilePath;
  String? fileContent;
  String? draft;
  int? fileRevision;
  bool loadingFiles = false;
  bool loadingContent = false;
  bool busy = false;
  String? error;
  int _fileRequest = 0;
  int _treeRequest = 0;

  bool get dirty => draft != fileContent;
  bool get manualActive => session?.active == true;
  bool get editable =>
      manualActive &&
      fileContent != null &&
      !fileContent!.contains('\uFFFD') &&
      !fileContent!.contains('\u0000');

  Future<void> initialize(String id) async {
    projectId = id;
    await refresh();
    await loadFiles();
  }

  Future<void> refresh() async {
    try {
      project = await api.getProject(projectId!);
      session = await api.getManualSession(projectId!);
      analysis = await api.getAnalysis(projectId!);
      error = null;
    } catch (e) {
      error = e.toString();
    }
    notifyListeners();
  }

  Future<void> loadFiles({String subdir = ''}) async {
    final request = ++_treeRequest;
    loadingFiles = true;
    error = null;
    notifyListeners();
    try {
      final result = await api.listFiles(projectId!, subdir: subdir);
      if (request != _treeRequest) return;
      files = result;
      currentSubdir = subdir;
    } catch (e) {
      error = e.toString();
    } finally {
      if (request == _treeRequest) loadingFiles = false;
      notifyListeners();
    }
  }

  Future<void> openFile(String path) async {
    if (dirty) {
      throw ApiException('Save or discard the unsaved editor buffer first.');
    }
    final request = ++_fileRequest;
    selectedFilePath = path;
    fileContent = null;
    draft = null;
    fileRevision = null;
    loadingContent = true;
    error = null;
    notifyListeners();
    try {
      final revision = (await api.getProject(projectId!)).workspaceRevision;
      final content = await api.readFile(projectId!, path);
      if (request != _fileRequest) return;
      fileRevision = revision;
      fileContent = content;
      draft = content;
    } catch (e) {
      if (request == _fileRequest) error = e.toString();
    } finally {
      if (request == _fileRequest) loadingContent = false;
      notifyListeners();
    }
  }

  void closeFile() {
    if (dirty) return;
    _fileRequest++;
    selectedFilePath = null;
    fileContent = null;
    draft = null;
    fileRevision = null;
    notifyListeners();
  }

  void edit(String value) {
    draft = value;
    notifyListeners();
  }

  void discardDraft() {
    draft = fileContent;
    notifyListeners();
  }

  Future<void> beginManual() async {
    if (busy) return;
    busy = true;
    error = null;
    notifyListeners();
    try {
      final current = await api.getManualSession(projectId!);
      if (!current.active) await api.beginManualSession(projectId!);
      session = await api.getManualSession(projectId!);
      project = await api.getProject(projectId!);
    } catch (e) {
      error = e.toString();
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> saveFile() async {
    if (busy || !editable || !dirty || fileRevision == null) return false;
    busy = true;
    error = null;
    notifyListeners();
    final content = draft!;
    try {
      if (utf8.encode(content).length > 1000000) {
        throw ApiException('Text exceeds the 1 MB edit limit.');
      }
      await api.replaceFile(
        projectId!,
        selectedFilePath!,
        content,
        fileRevision!,
      );
      fileContent = content;
      return true;
    } catch (e) {
      error = e.toString();
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> record() async {
    if (busy || dirty || !manualActive) return false;
    busy = true;
    error = null;
    notifyListeners();
    try {
      final result = await api.recordManualChanges(
        projectId!,
        'Manual edits recorded from NOIR app',
      );
      session = await api.getManualSession(projectId!);
      project = await api.getProject(projectId!);
      fileRevision = null;
      if (result['validation']?['passed'] == false) {
        error =
            'Changes recorded, but validation failed. Open Validate & Build for findings.';
      }
      return true;
    } catch (e) {
      error = e.toString();
      return false;
    } finally {
      busy = false;
      notifyListeners();
    }
  }
}
