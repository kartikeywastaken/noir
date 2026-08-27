/// Plan controller — create, review, approve/reject.
import 'dart:async';
import 'package:flutter/material.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';

class PlanController extends ChangeNotifier {
  PlanController(this._api);

  final NoirApiClient _api;

  ChangePlan? _currentPlan;
  ChangePlan? get currentPlan => _currentPlan;

  bool _generating = false;
  bool get generating => _generating;

  bool _approving = false;
  bool get approving => _approving;

  String? _error;
  String? get error => _error;

  void clear() {
    _currentPlan = null;
    _error = null;
    notifyListeners();
  }

  Future<ChangePlan?> createPlan(String projectId, String request) async {
    _generating = true;
    _error = null;
    notifyListeners();

    try {
      _currentPlan = await _api.createPlan(projectId, request);
      return _currentPlan;
    } on ApiException catch (e) {
      _error = e.message;
      return null;
    } finally {
      _generating = false;
      notifyListeners();
    }
  }

  Future<ChangePlan?> loadPlan(String projectId, String planId) async {
    _error = null;
    notifyListeners();
    try {
      _currentPlan = await _api.getPlan(projectId, planId);
      notifyListeners();
      return _currentPlan;
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
      return null;
    }
  }

  Future<bool> approvePlan(String projectId) async {
    if (_currentPlan == null) return false;
    _approving = true;
    _error = null;
    notifyListeners();

    try {
      await _api.approvePlan(projectId, _currentPlan!.planId, _currentPlan!.planHash);
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      return false;
    } finally {
      _approving = false;
      notifyListeners();
    }
  }

  Future<bool> rejectPlan(String projectId) async {
    if (_currentPlan == null) return false;
    try {
      await _api.rejectPlan(projectId, _currentPlan!.planId);
      _currentPlan = null;
      notifyListeners();
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
      return false;
    }
  }
}
