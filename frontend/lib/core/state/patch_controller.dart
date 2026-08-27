/// Patch controller — generate, review, approve, apply, undo.
import 'package:flutter/material.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';

class PatchController extends ChangeNotifier {
  PatchController(this._api);

  final NoirApiClient _api;

  PatchSet? _currentPatch;
  PatchSet? get currentPatch => _currentPatch;

  List<PatchDiffEntry> _diff = [];
  List<PatchDiffEntry> get diff => _diff;

  bool _generating = false;
  bool get generating => _generating;

  bool _applying = false;
  bool get applying => _applying;

  String? _error;
  String? get error => _error;

  void clear() {
    _currentPatch = null;
    _diff = [];
    _error = null;
    notifyListeners();
  }

  Future<PatchSet?> generatePatch(String projectId, String planId) async {
    _generating = true;
    _error = null;
    notifyListeners();

    try {
      _currentPatch = await _api.generatePatch(projectId, planId);
      return _currentPatch;
    } on ApiException catch (e) {
      _error = e.message;
      return null;
    } finally {
      _generating = false;
      notifyListeners();
    }
  }

  Future<PatchSet?> loadPatch(String projectId, String patchId) async {
    _error = null;
    try {
      _currentPatch = await _api.getPatch(projectId, patchId);
      notifyListeners();
      return _currentPatch;
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
      return null;
    }
  }

  Future<void> loadDiff(String projectId, String patchId) async {
    try {
      _diff = await _api.getPatchDiff(projectId, patchId);
      notifyListeners();
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
    }
  }

  Future<bool> approvePatch(String projectId) async {
    if (_currentPatch == null) return false;
    _error = null;
    notifyListeners();

    try {
      await _api.approvePatch(projectId, _currentPatch!.patchId, _currentPatch!.patchHash);
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
      return false;
    }
  }

  Future<bool> applyPatch(String projectId) async {
    if (_currentPatch == null) return false;
    _applying = true;
    _error = null;
    notifyListeners();

    try {
      await _api.applyPatch(projectId, _currentPatch!.patchId);
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      return false;
    } finally {
      _applying = false;
      notifyListeners();
    }
  }

  Future<bool> undoPatch(String projectId, String patchId) async {
    _error = null;
    notifyListeners();
    try {
      await _api.undoPatch(projectId, patchId);
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
      return false;
    }
  }
}
