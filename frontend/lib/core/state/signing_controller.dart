import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';
import 'safe_notifier.dart';

String _digest(Uint8List bytes) => sha256.convert(bytes).toString();

class SigningController extends SafeNotifier {
  SigningController(this.api);
  final NoirApiClient api;
  List<SigningProfile> profiles = [];
  String? selectedProfile;
  bool signing = false;
  String? error;
  Map<String, dynamic>? signResult;
  Map<String, dynamic>? verifyResult;

  Future<void> loadProfiles() async {
    error = null;
    try {
      profiles = await api.listSigningProfiles();
      if (profiles.isEmpty) {
        profiles = [await api.createPersonalSigningProfile()];
      }
      if (!profiles.any((profile) => profile.name == selectedProfile)) {
        selectedProfile = profiles.firstOrNull?.name;
      }
    } catch (e) {
      error = e.toString();
    }
    notifyListeners();
  }

  void selectProfile(String name) {
    selectedProfile = name;
    notifyListeners();
  }

  Future<bool> sign(String id, String build) async {
    if (signing || selectedProfile == null) return false;
    signing = true;
    error = null;
    signResult = null;
    verifyResult = null;
    notifyListeners();
    try {
      signResult = await api.signBuild(id, build, selectedProfile!);
      return true;
    } catch (e) {
      error = e.toString();
      return false;
    } finally {
      signing = false;
      notifyListeners();
    }
  }

  Future<Uint8List> verifiedDownload(String id, BuildResult build) async {
    final revision = api.credentialRevision;
    final expected = build.signedApkHash;
    if (expected == null || !RegExp(r'^[a-fA-F0-9]{64}$').hasMatch(expected)) {
      throw ApiException(
        'No trusted signed artifact hash is recorded for this build.',
      );
    }
    verifyResult = await api.verifyBuild(id, build.buildId);
    if (verifyResult!['verified'] != true) {
      throw ApiException(
        'APK signature verification failed. Download blocked.',
      );
    }
    final bytes = await api.downloadArtifact(id, build.buildId);
    if (await compute(_digest, bytes) != expected.toLowerCase()) {
      throw ApiException(
        'Downloaded SHA-256 differs from the recorded build. File was NOT saved.',
      );
    }
    if (revision != api.credentialRevision) {
      throw ApiException('Workspace changed. Download discarded.');
    }
    return bytes;
  }
}
