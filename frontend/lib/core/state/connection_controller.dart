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
    bool restore = true,
  }) : api = client ?? NoirApiClient(),
       _storage = storage ?? const FlutterSecureStorage() {
    ready = restore ? _loadSaved() : Future.value();
  }
  final FlutterSecureStorage _storage;
  final NoirApiClient api;
  late final Future<void> ready;
  BackendConnectionState state = BackendConnectionState.disconnected;
  String get baseUrl => api.baseUrl;
  HealthResponse? health;
  String? errorMessage;
  bool get isConnected => state == BackendConnectionState.connected;
  bool get hasToken => api.token?.isNotEmpty == true;

  Future<void> _loadSaved() async {
    try {
      final url = await _storage.read(key: 'noir_base_url');
      final token = await _storage.read(key: 'noir_token');
      if (disposed) return;
      if (url != null) api.baseUrl = NoirApiClient.normalizeBaseUrl(url);
      api.token = token;
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
    state = BackendConnectionState.disconnected;
    health = null;
    api.baseUrl = normalized;
    if (token.trim().isNotEmpty) api.token = token.trim();
    if (!hasToken) throw UnauthorizedException();
    await _storage.write(key: 'noir_base_url', value: normalized);
    await _storage.write(key: 'noir_token', value: api.token);
    notifyListeners();
  }

  Future<void> clearToken() async {
    api.token = null;
    state = BackendConnectionState.disconnected;
    health = null;
    errorMessage = null;
    notifyListeners();
    await _storage.delete(key: 'noir_token');
  }

  Future<bool> testConnection() async {
    if (state == BackendConnectionState.connecting) return false;
    state = BackendConnectionState.connecting;
    errorMessage = null;
    health = null;
    notifyListeners();
    try {
      if (!hasToken) throw UnauthorizedException();
      health = await api.getHealth();
      if (health!.status != 'ok') throw ApiException('Backend is not healthy.');
      await api.testAuth();
      state = BackendConnectionState.connected;
      return true;
    } catch (e) {
      state = BackendConnectionState.error;
      errorMessage = e is ApiException ? e.message : 'Connection check failed.';
      return false;
    } finally {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    api.dispose();
    super.dispose();
  }
}
