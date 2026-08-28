import 'dart:async';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';
import 'safe_notifier.dart';

enum BackendConnectionState { disconnected, connecting, connected, error }

class ConnectionController extends SafeNotifier {
  ConnectionController({
    NoirApiClient? client,
    FlutterSecureStorage? storage,
    NoirApiClient Function()? activationClientFactory,
    bool restore = true,
  }) : api = client ?? NoirApiClient(),
       _storage = storage ?? const FlutterSecureStorage(),
       _activationClientFactory =
           activationClientFactory ??
           (() => NoirApiClient(baseUrl: NoirApiClient.cloudBaseUrl)) {
    api.onUnauthorized = () {
      unawaited(clearToken());
    };
    ready = restore ? _loadSaved() : Future.value();
  }
  final FlutterSecureStorage _storage;
  final NoirApiClient Function() _activationClientFactory;
  Future<void> _storageWrites = Future.value();
  final NoirApiClient api;
  late final Future<void> ready;
  BackendConnectionState state = BackendConnectionState.disconnected;
  String get baseUrl => api.baseUrl;
  HealthResponse? health;
  Map<String, dynamic>? user;
  int sessionEpoch = 0;
  String get workspaceName => user?['name'] as String? ?? 'Private workspace';
  String? errorMessage;
  bool get isConnected => state == BackendConnectionState.connected;
  bool get hasToken => api.token?.isNotEmpty == true;

  Future<void> _persist(Future<void> Function() action) {
    final next = _storageWrites.catchError((Object _) {}).then((_) => action());
    _storageWrites = next;
    return next;
  }

  void _resetSession() {
    sessionEpoch++;
    state = BackendConnectionState.disconnected;
    health = null;
    user = null;
    errorMessage = null;
  }

  Future<void> _loadSaved() async {
    try {
      final url = await _storage.read(key: 'noir_base_url');
      final token = await _storage.read(key: 'noir_token');
      if (disposed) return;
      if (url != null) api.baseUrl = NoirApiClient.normalizeBaseUrl(url);
      api.token = token;
      notifyListeners();
      if (hasToken) await testConnection();
    } catch (_) {
      state = BackendConnectionState.error;
      errorMessage =
          'Cannot load secure credentials. Configure the connection again.';
      notifyListeners();
    }
  }

  Future<void> configure(String url, String token) async {
    await ready;
    final normalized = NoirApiClient.normalizeBaseUrl(url);
    // Never carry a saved token to a different server without explicit input.
    if (normalized != api.baseUrl && token.trim().isEmpty) {
      throw ApiException(
        'Paste a token explicitly when changing backend address.',
      );
    }
    if (normalized != api.baseUrl ||
        (token.trim().isNotEmpty && token.trim() != api.token)) {
      _resetSession();
    }
    api.baseUrl = normalized;
    if (token.trim().isNotEmpty) api.token = token.trim();
    notifyListeners();
    if (!hasToken) throw UnauthorizedException();
    final savedToken = api.token;
    await _persist(() async {
      await _storage.write(key: 'noir_base_url', value: normalized);
      await _storage.write(key: 'noir_token', value: savedToken);
    });
    notifyListeners();
  }

  Future<void> clearToken() async {
    api.token = null;
    _resetSession();
    notifyListeners();
    try {
      await _persist(() => _storage.delete(key: 'noir_token'));
    } catch (_) {
      errorMessage =
          'Could not remove the saved credential from secure storage.';
      notifyListeners();
    }
  }

  Future<bool> activateInvite(String code) async {
    await ready;
    // Do not send a previous account's credentials or an invite to a custom origin.
    final activation = _activationClientFactory();
    try {
      final response = await activation.redeemInvite(code);
      final token = response['token'] as String?;
      if (token == null || token.isEmpty) {
        throw ApiException('Invalid activation response.');
      }
      await configure(NoirApiClient.cloudBaseUrl, token);
      return await testConnection();
    } finally {
      activation.dispose();
    }
  }

  Future<void> signOut() async {
    if (hasToken) await api.logout();
    await clearToken();
  }

  Future<bool> testConnection() async {
    if (state == BackendConnectionState.connecting) return false;
    final epoch = sessionEpoch;
    state = BackendConnectionState.connecting;
    errorMessage = null;
    health = null;
    notifyListeners();
    try {
      if (!hasToken) {
        throw UnauthorizedException('Enter your invitation to activate NOIR.');
      }
      final checkedHealth = await api.getHealth();
      if (checkedHealth.status != 'ok') {
        throw ApiException('Backend is not healthy.');
      }
      final checkedUser = await api.getCurrentUser();
      if (epoch != sessionEpoch || disposed) return false;
      health = checkedHealth;
      user = checkedUser;
      state = BackendConnectionState.connected;
      return true;
    } catch (e) {
      if (epoch == sessionEpoch && !disposed) {
        state = BackendConnectionState.error;
        errorMessage = e is ApiException
            ? e.message
            : 'Connection check failed.';
      }
      return false;
    } finally {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    api.onUnauthorized = null;
    api.dispose();
    super.dispose();
  }
}
