/// Signing controller — profiles, signing flow, download.
import 'dart:io';
import 'package:flutter/material.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';

class SigningController extends ChangeNotifier {
  SigningController(this._api);

  final NoirApiClient _api;

  List<SigningProfile> _profiles = [];
  List<SigningProfile> get profiles => _profiles;

  String? _selectedProfile;
  String? get selectedProfile => _selectedProfile;

  bool _signing = false;
  bool get signing => _signing;

  Map<String, dynamic>? _signResult;
  Map<String, dynamic>? get signResult => _signResult;

  Map<String, dynamic>? _verifyResult;
  Map<String, dynamic>? get verifyResult => _verifyResult;

  String? _error;
  String? get error => _error;

  Future<void> loadProfiles() async {
    try {
      _profiles = await _api.listSigningProfiles();
      notifyListeners();
    } catch (_) {}
  }

  void selectProfile(String name) {
    _selectedProfile = name;
    notifyListeners();
  }

  Future<bool> sign(String projectId, String buildId) async {
    if (_selectedProfile == null) return false;
    _signing = true;
    _error = null;
    notifyListeners();

    try {
      _signResult = await _api.signBuild(projectId, buildId, _selectedProfile!);
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      return false;
    } finally {
      _signing = false;
      notifyListeners();
    }
  }

  Future<bool> verify(String projectId, String buildId) async {
    _error = null;
    try {
      _verifyResult = await _api.verifyBuild(projectId, buildId);
      notifyListeners();
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
      return false;
    }
  }

  Future<bool> download(String projectId, String buildId, String savePath) async {
    _error = null;
    try {
      final bytes = await _api.downloadArtifact(projectId, buildId);
      await File(savePath).writeAsBytes(bytes);
      return true;
    } on ApiException catch (e) {
      _error = e.message;
      notifyListeners();
      return false;
    }
  }

  void clear() {
    _signResult = null;
    _verifyResult = null;
    _error = null;
    notifyListeners();
  }
}
