/// NOIR HTTP API client — typed, authenticated, with SSE support.
///
/// Uses package:http for requests. Bearer token injected on all protected calls.
/// Credentials are never logged. Multipart upload supports progress callback.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../models/models.dart';
import 'api_exceptions.dart';

class NoirApiClient {
  NoirApiClient({String? baseUrl, this.token})
      : baseUrl = baseUrl ?? 'http://127.0.0.1:8787';

  String baseUrl;
  String? token;
  final http.Client _client = http.Client();

  /// In-flight request tracking to prevent duplicate submissions.
  final Set<String> _inFlight = {};

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      };

  // ── Helpers ──────────────────────────────────────────────────

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse('$baseUrl$path').replace(queryParameters: query);

  Future<Map<String, dynamic>> _get(String path, {Map<String, String>? query}) async {
    try {
      final response = await _client
          .get(_uri(path, query), headers: _headers)
          .timeout(const Duration(seconds: 30));
      return _handleResponse(response);
    } on SocketException {
      throw ConnectionException();
    } on TimeoutException {
      throw ConnectionException('Request timed out');
    }
  }

  Future<Map<String, dynamic>> _post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query, String? dedupeKey}) async {
    final key = dedupeKey ?? path;
    if (_inFlight.contains(key)) {
      throw ApiException('Request already in flight');
    }
    _inFlight.add(key);
    try {
      final response = await _client
          .post(_uri(path, query),
              headers: _headers, body: body != null ? jsonEncode(body) : null)
          .timeout(const Duration(seconds: 120));
      return _handleResponse(response);
    } on SocketException {
      throw ConnectionException();
    } on TimeoutException {
      throw ConnectionException('Request timed out');
    } finally {
      _inFlight.remove(key);
    }
  }

  Future<Map<String, dynamic>> _put(String path,
      {required Map<String, dynamic> body}) async {
    try {
      final response = await _client
          .put(_uri(path), headers: _headers, body: jsonEncode(body))
          .timeout(const Duration(seconds: 30));
      return _handleResponse(response);
    } on SocketException {
      throw ConnectionException();
    } on TimeoutException {
      throw ConnectionException('Request timed out');
    }
  }

  Map<String, dynamic> _handleResponse(http.Response response) {
    if (response.statusCode == 401) throw UnauthorizedException();
    if (response.statusCode == 404) {
      final body = _tryParseJson(response.body);
      throw NotFoundException(body?['detail'] as String? ?? 'Not found');
    }
    if (response.statusCode == 409) {
      final body = _tryParseJson(response.body);
      throw ConflictException(body?['detail'] as String? ?? body?['error'] as String?);
    }
    if (response.statusCode == 422) {
      throw ValidationException();
    }
    if (response.statusCode >= 400) {
      final body = _tryParseJson(response.body);
      final msg = body?['detail'] as String? ?? body?['error'] as String? ?? 'Request failed';
      throw ApiException(msg, statusCode: response.statusCode);
    }
    if (response.body.isEmpty) return {};
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Map<String, dynamic>? _tryParseJson(String body) {
    try {
      return jsonDecode(body) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  // ── Health (no auth) ─────────────────────────────────────────

  Future<HealthResponse> getHealth() async {
    try {
      final response = await _client
          .get(_uri('/v1/health'), headers: {'Content-Type': 'application/json'})
          .timeout(const Duration(seconds: 10));
      if (response.statusCode != 200) throw ServerException();
      return HealthResponse.fromJson(jsonDecode(response.body));
    } on SocketException {
      throw ConnectionException();
    } on TimeoutException {
      throw ConnectionException('Health check timed out');
    }
  }

  /// Test authenticated connection (calls projects list).
  Future<bool> testAuth() async {
    await _get('/v1/projects');
    return true;
  }

  // ── Projects ─────────────────────────────────────────────────

  Future<List<ProjectInfo>> listProjects() async {
    final data = await _get('/v1/projects');
    return (data['projects'] as List)
        .map((p) => ProjectInfo.fromJson(p as Map<String, dynamic>))
        .toList();
  }

  Future<ProjectInfo> getProject(String projectId) async {
    final data = await _get('/v1/projects/$projectId');
    return ProjectInfo.fromJson(data);
  }

  Future<AnalysisResult> getAnalysis(String projectId) async {
    final data = await _get('/v1/projects/$projectId/analysis');
    return AnalysisResult.fromJson(data);
  }

  // ── Import ───────────────────────────────────────────────────

  Future<JobInfo> importApk(
    String filePath, {
    String? idempotencyKey,
    void Function(int sent, int total)? onProgress,
  }) async {
    final uri = _uri('/v1/import', {'authorized': 'true'});
    final request = http.MultipartRequest('POST', uri);
    request.headers['Authorization'] = 'Bearer $token';
    if (idempotencyKey != null) {
      request.headers['Idempotency-Key'] = idempotencyKey;
    }
    request.files.add(await http.MultipartFile.fromPath('file', filePath));

    try {
      final streamedResponse = await request.send().timeout(const Duration(minutes: 10));
      final response = await http.Response.fromStream(streamedResponse);
      final data = _handleResponse(response);
      return JobInfo.fromJson(data);
    } on SocketException {
      throw ConnectionException();
    }
  }

  // ── Files ────────────────────────────────────────────────────

  Future<List<FileEntry>> listFiles(String projectId, {String subdir = ''}) async {
    final data = await _get('/v1/projects/$projectId/files',
        query: subdir.isNotEmpty ? {'subdir': subdir} : null);
    return (data['files'] as List)
        .map((f) => FileEntry.fromJson(f as Map<String, dynamic>))
        .toList();
  }

  Future<String> readFile(String projectId, String path) async {
    final data = await _get('/v1/projects/$projectId/files/read', query: {'path': path});
    return data['content'] as String? ?? '';
  }

  Future<List<Map<String, dynamic>>> searchFiles(String projectId, String query) async {
    final data = await _get('/v1/projects/$projectId/files/search', query: {'q': query});
    return List<Map<String, dynamic>>.from(data['results'] ?? []);
  }

  // ── Plans ────────────────────────────────────────────────────

  Future<ChangePlan> createPlan(String projectId, String request, {bool allowAiUpload = true}) async {
    final data = await _post('/v1/projects/$projectId/plans',
        body: {'user_request': request, 'allow_ai_upload': allowAiUpload},
        dedupeKey: 'plan-create-$projectId');
    return ChangePlan.fromJson(data);
  }

  Future<List<Map<String, dynamic>>> listPlans(String projectId) async {
    final data = await _get('/v1/projects/$projectId/plans');
    return List<Map<String, dynamic>>.from(data['plans'] ?? []);
  }

  Future<ChangePlan> getPlan(String projectId, String planId) async {
    final data = await _get('/v1/projects/$projectId/plans/$planId');
    return ChangePlan.fromJson(data);
  }

  Future<ApprovalRecord> approvePlan(String projectId, String planId, String hash) async {
    final data = await _post('/v1/projects/$projectId/plans/$planId/approve',
        body: {'hash': hash}, dedupeKey: 'plan-approve-$planId');
    return ApprovalRecord.fromJson(data);
  }

  Future<void> rejectPlan(String projectId, String planId) async {
    await _post('/v1/projects/$projectId/plans/$planId/reject',
        dedupeKey: 'plan-reject-$planId');
  }

  // ── Patches ──────────────────────────────────────────────────

  Future<PatchSet> generatePatch(String projectId, String planId) async {
    final data = await _post('/v1/projects/$projectId/patches',
        query: {'plan_id': planId}, dedupeKey: 'patch-gen-$planId');
    return PatchSet.fromJson(data);
  }

  Future<List<Map<String, dynamic>>> listPatches(String projectId) async {
    final data = await _get('/v1/projects/$projectId/patches');
    return List<Map<String, dynamic>>.from(data['patches'] ?? []);
  }

  Future<PatchSet> getPatch(String projectId, String patchId) async {
    final data = await _get('/v1/projects/$projectId/patches/$patchId');
    return PatchSet.fromJson(data);
  }

  Future<List<PatchDiffEntry>> getPatchDiff(String projectId, String patchId) async {
    final data = await _get('/v1/projects/$projectId/patches/$patchId/diff');
    return (data['diff'] as List? ?? [])
        .map((d) => PatchDiffEntry.fromJson(d as Map<String, dynamic>))
        .toList();
  }

  Future<ApprovalRecord> approvePatch(String projectId, String patchId, String hash) async {
    final data = await _post('/v1/projects/$projectId/patches/$patchId/approve',
        body: {'hash': hash}, dedupeKey: 'patch-approve-$patchId');
    return ApprovalRecord.fromJson(data);
  }

  Future<Map<String, dynamic>> applyPatch(String projectId, String patchId) async {
    return _post('/v1/projects/$projectId/patches/$patchId/apply',
        dedupeKey: 'patch-apply-$patchId');
  }

  Future<Map<String, dynamic>> undoPatch(String projectId, String patchId) async {
    return _post('/v1/projects/$projectId/patches/$patchId/undo',
        dedupeKey: 'patch-undo-$patchId');
  }

  // ── Manual Session ───────────────────────────────────────────

  Future<ManualSession> getManualSession(String projectId) async {
    final data = await _get('/v1/projects/$projectId/manual/session');
    return ManualSession.fromJson(data);
  }

  Future<Map<String, dynamic>> beginManualSession(String projectId) async {
    return _post('/v1/projects/$projectId/manual/begin',
        dedupeKey: 'manual-begin-$projectId');
  }

  Future<Map<String, dynamic>> replaceFile(
      String projectId, String relativePath, String content, int expectedRevision) async {
    return _put('/v1/projects/$projectId/files', body: {
      'relative_path': relativePath,
      'content': content,
      'expected_revision': expectedRevision,
    });
  }

  Future<Map<String, dynamic>> recordManualChanges(String projectId, String message) async {
    return _post('/v1/projects/$projectId/manual/record',
        body: {'message': message}, dedupeKey: 'manual-record-$projectId');
  }

  // ── Validation & Build ───────────────────────────────────────

  Future<ValidationResult> validate(String projectId) async {
    final data = await _post('/v1/projects/$projectId/validate',
        dedupeKey: 'validate-$projectId');
    return ValidationResult.fromJson(data);
  }

  Future<JobInfo> startBuild(String projectId, {String? idempotencyKey}) async {
    final headers = <String, String>{..._headers};
    if (idempotencyKey != null) headers['Idempotency-Key'] = idempotencyKey;

    try {
      final response = await _client
          .post(_uri('/v1/projects/$projectId/build'), headers: headers)
          .timeout(const Duration(seconds: 30));
      return JobInfo.fromJson(_handleResponse(response));
    } on SocketException {
      throw ConnectionException();
    }
  }

  Future<List<BuildResult>> listBuilds(String projectId) async {
    final data = await _get('/v1/projects/$projectId/builds');
    return (data['builds'] as List)
        .map((b) => BuildResult.fromJson(b as Map<String, dynamic>))
        .toList();
  }

  // ── Signing ──────────────────────────────────────────────────

  Future<List<SigningProfile>> listSigningProfiles() async {
    final data = await _get('/v1/keys');
    return (data['profiles'] as List)
        .map((p) => SigningProfile.fromJson(p as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> signBuild(
      String projectId, String buildId, String profile) async {
    return _post('/v1/projects/$projectId/sign',
        body: {'build_id': buildId, 'profile': profile, 'confirm': true},
        dedupeKey: 'sign-$buildId');
  }

  Future<Map<String, dynamic>> verifyBuild(String projectId, String buildId) async {
    return _get('/v1/projects/$projectId/builds/$buildId/verify');
  }

  /// Download signed APK bytes.
  Future<List<int>> downloadArtifact(String projectId, String buildId,
      {String artifact = 'signed'}) async {
    final uri = _uri('/v1/projects/$projectId/builds/$buildId/download',
        {'artifact': artifact});
    try {
      final response = await _client
          .get(uri, headers: {if (token != null) 'Authorization': 'Bearer $token'})
          .timeout(const Duration(minutes: 5));
      if (response.statusCode == 401) throw UnauthorizedException();
      if (response.statusCode == 404) throw NotFoundException('Artifact not found');
      if (response.statusCode >= 400) throw ServerException();
      return response.bodyBytes;
    } on SocketException {
      throw ConnectionException();
    }
  }

  // ── Audit ────────────────────────────────────────────────────

  Future<Map<String, dynamic>> getAuditJson(String projectId) async {
    return _get('/v1/projects/$projectId/audit', query: {'format': 'json'});
  }

  Future<String> getAuditMarkdown(String projectId) async {
    final data = await _get('/v1/projects/$projectId/audit', query: {'format': 'markdown'});
    return data['markdown'] as String? ?? '';
  }

  // ── Events ───────────────────────────────────────────────────

  Future<List<AuditEvent>> listEvents(String projectId, {String? after}) async {
    final query = <String, String>{};
    if (after != null) query['after'] = after;
    final data = await _get('/v1/projects/$projectId/events', query: query.isEmpty ? null : query);
    return (data['events'] as List)
        .map((e) => AuditEvent.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  // ── Jobs ─────────────────────────────────────────────────────

  Future<List<JobInfo>> listJobs({String? projectId}) async {
    final query = projectId != null ? {'project_id': projectId} : null;
    final data = await _get('/v1/jobs', query: query);
    return (data['jobs'] as List)
        .map((j) => JobInfo.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<JobInfo> getJob(String jobId) async {
    final data = await _get('/v1/jobs/$jobId');
    return JobInfo.fromJson(data);
  }

  Future<void> cancelJob(String jobId) async {
    await _post('/v1/jobs/$jobId/cancel', dedupeKey: 'cancel-$jobId');
  }

  /// Stream SSE events for a job. Reconnects with backoff.
  Stream<AuditEvent> streamJobEvents(String jobId, {String? after}) async* {
    var cursor = after;
    var retries = 0;
    const maxRetries = 5;

    while (retries < maxRetries) {
      try {
        final query = <String, String>{};
        if (cursor != null) query['after'] = cursor;
        final request = http.Request(
            'GET', _uri('/v1/jobs/$jobId/events', query.isEmpty ? null : query));
        request.headers['Authorization'] = 'Bearer $token';
        request.headers['Accept'] = 'text/event-stream';

        final streamedResponse = await _client.send(request);
        if (streamedResponse.statusCode == 401) throw UnauthorizedException();
        if (streamedResponse.statusCode == 404) throw NotFoundException('Job not found');

        final seen = <String>{};
        await for (final chunk in streamedResponse.stream.transform(utf8.decoder)) {
          for (final line in chunk.split('\n')) {
            if (line.startsWith('id: ')) {
              cursor = line.substring(4).trim();
            } else if (line.startsWith('data: ')) {
              final jsonStr = line.substring(6).trim();
              if (jsonStr.isEmpty) continue;
              try {
                final data = jsonDecode(jsonStr) as Map<String, dynamic>;
                final event = AuditEvent.fromJson(data);
                if (!seen.contains(event.eventId)) {
                  seen.add(event.eventId);
                  yield event;
                }
              } catch (_) {
                // Skip malformed events
              }
            } else if (line.startsWith('event: done')) {
              return; // Job finished
            }
          }
        }
        return; // Stream ended normally
      } on UnauthorizedException {
        rethrow;
      } on NotFoundException {
        rethrow;
      } catch (_) {
        retries++;
        if (retries >= maxRetries) rethrow;
        await Future.delayed(Duration(seconds: retries * 2));
      }
    }
  }

  void dispose() {
    _client.close();
  }
}
