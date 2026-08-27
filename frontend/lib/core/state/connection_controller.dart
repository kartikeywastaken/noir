/// Connection & authentication state controller.
import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import '../../data/api/noir_api_client.dart';
import '../../data/api/api_exceptions.dart';
import '../../data/models/models.dart';

enum ConnectionState { disconnected, connecting, connected, error }

class ConnectionController extends ChangeNotifier {
  ConnectionController() {
    _loadSaved();
  }

  final FlutterSecureStorage _storage = const FlutterSecureStorage();
  late final NoirApiClient api = NoirApiClient();

  ConnectionState _state = ConnectionState.disconnected;
  ConnectionState get state => _state;

  String _baseUrl = 'http://127.0.0.1:8787';
  String get baseUrl => _baseUrl;

  HealthResponse? _health;
  HealthResponse? get health => _health;

  String? _errorMessage;
  String? get errorMessage => _errorMessage;

  bool get isConnected => _state == ConnectionState.connected;

  Future<void> _loadSaved() async {
    final url = await _storage.read(key: 'noir_base_url');
    final token = await _storage.read(key: 'noir_token');
    if (url != null) _baseUrl = url;
    api.baseUrl = _baseUrl;
    if (token != null) {
      api.token = token;
      await testConnection();
    }
  }

  Future<void> setBaseUrl(String url) async {
    _baseUrl = url.endsWith('/') ? url.substring(0, url.length - 1) : url;
    api.baseUrl = _baseUrl;
    await _storage.write(key: 'noir_base_url', value: _baseUrl);
    notifyListeners();
  }

  Future<void> setToken(String token) async {
    api.token = token;
    await _storage.write(key: 'noir_token', value: token);
    notifyListeners();
  }

  Future<void> clearToken() async {
    api.token = null;
    await _storage.delete(key: 'noir_token');
    _state = ConnectionState.disconnected;
    _health = null;
    notifyListeners();
  }

  Future<bool> testConnection() async {
    _state = ConnectionState.connecting;
    _errorMessage = null;
    notifyListeners();

    try {
      _health = await api.getHealth();
      if (api.token != null) {
        await api.testAuth();
      }
      _state = ConnectionState.connected;
      notifyListeners();
      return true;
    } on ConnectionException catch (e) {
      _state = ConnectionState.error;
      _errorMessage = e.message;
      notifyListeners();
      return false;
    } on UnauthorizedException {
      _state = ConnectionState.error;
      _errorMessage = 'Invalid API token';
      notifyListeners();
      return false;
    } catch (e) {
      _state = ConnectionState.error;
      _errorMessage = e.toString();
      notifyListeners();
      return false;
    }
  }
}
