/// Authenticated backend adapter. Mutation requests are never auto-replayed.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import '../models/models.dart';
import 'api_exceptions.dart';
import 'transfer_progress.dart';

class NoirApiClient {
  static const cloudBaseUrl = String.fromEnvironment(
    'NOIR_BACKEND_URL',
    defaultValue: 'https://noir-16-171-197-228.sslip.io',
  );

  NoirApiClient({String? baseUrl, String? token, http.Client? client})
    : _baseUrl = normalizeBaseUrl(baseUrl ?? cloudBaseUrl),
      _token = token,
      _client = client ?? http.Client();
  String _baseUrl;
  String? _token;
  int _credentialRevision = 0;
  int get credentialRevision => _credentialRevision;
  void Function()? onUnauthorized;
  String get baseUrl => _baseUrl;
  set baseUrl(String value) {
    final normalized = normalizeBaseUrl(value);
    if (normalized != _baseUrl) _credentialRevision++;
    _baseUrl = normalized;
  }

  String? get token => _token;
  set token(String? value) {
    if (value != _token) _credentialRevision++;
    _token = value;
  }

  final http.Client _client;
  final Set<String> _inFlight = {};

  static String normalizeBaseUrl(String value) {
    final uri = Uri.tryParse(value.trim());
    if (uri == null ||
        !uri.hasAuthority ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment ||
        (uri.path.isNotEmpty && uri.path != '/') ||
        !['http', 'https'].contains(uri.scheme)) {
      throw ApiException(
        'Enter a backend origin, e.g. http://127.0.0.1:8787 (no path or credentials).',
      );
    }
    if (uri.scheme == 'http' &&
        !['127.0.0.1', 'localhost', '::1', '10.0.2.2'].contains(uri.host)) {
      throw ApiException(
        'Unencrypted HTTP is allowed only for loopback/Android emulator. Use HTTPS for remote hosts.',
      );
    }
    return uri.replace(path: '').toString().replaceFirst(RegExp(r'/$'), '');
  }

  Uri _uri(String path, [Map<String, String>? query]) => Uri.parse(
    '${normalizeBaseUrl(baseUrl)}$path',
  ).replace(queryParameters: query);

  String _redact(String value) => token?.isNotEmpty == true
      ? value.replaceAll(token!, '[REDACTED]')
      : value;

  Future<http.Response> _send(
    http.BaseRequest request, {
    Duration timeout = const Duration(seconds: 30),
    bool mutation = false,
  }) async {
    request.followRedirects = false;
    final revision = credentialRevision;
    try {
      final response = await (() async => http.Response.fromStream(
        await _client.send(request),
      ))().timeout(timeout);
      if (revision != credentialRevision) {
        throw ApiException(
          'Workspace changed. The previous response was discarded.',
        );
      }
      return response;
    } on TimeoutException {
      throw ConnectionException(
        mutation
            ? 'Response timed out. The server may still finish this operation. Refresh history/jobs before retrying; it has NOT been replayed.'
            : 'Request timed out. Check the backend connection.',
      );
    } on SocketException {
      throw ConnectionException(
        mutation
            ? 'Connection lost. Check history/jobs before retrying this operation.'
            : null,
      );
    } on http.ClientException {
      throw ConnectionException(
        mutation
            ? 'Connection lost. Check history/jobs before retrying this operation.'
            : null,
      );
    }
  }

  Map<String, dynamic> _response(http.Response response) {
    Map<String, dynamic>? data;
    try {
      data =
          jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    } catch (_) {
      /* Error responses can be plain text. */
    }
    final detail = data?['detail'] ?? data?['error'];
    final message = _redact(
      detail is String ? detail : 'Request failed (${response.statusCode}).',
    );
    if (response.statusCode == 401) {
      onUnauthorized?.call();
      throw UnauthorizedException(
        'Session expired or revoked. Activate a new invitation.',
      );
    }
    if (response.statusCode == 404) throw NotFoundException(message);
    if (response.statusCode == 409) throw ConflictException(message);
    if (response.statusCode == 422) throw ValidationException(message);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(message, statusCode: response.statusCode);
    }
    if (data == null) {
      throw ApiException('Backend returned an invalid JSON object.');
    }
    return data;
  }

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
    bool authenticated = true,
    String? idempotencyKey,
  }) async {
    final mutation = method != 'GET';
    final key = '$credentialRevision:$method:$path:${query ?? {}}';
    if (mutation && !_inFlight.add(key)) {
      throw ApiException('Request already in flight.');
    }
    try {
      if (authenticated && (token == null || token!.trim().isEmpty)) {
        throw UnauthorizedException();
      }
      final request = http.Request(method, _uri(path, query));
      if (authenticated) request.headers['Authorization'] = 'Bearer $token';
      if (idempotencyKey != null) {
        request.headers['Idempotency-Key'] = idempotencyKey;
      }
      if (body != null) {
        request.headers['Content-Type'] = 'application/json';
        request.body = jsonEncode(body);
      }
      return _response(
        await _send(
          request,
          mutation: mutation,
          timeout: Duration(seconds: mutation ? 300 : 30),
        ),
      );
    } finally {
      if (mutation) _inFlight.remove(key);
    }
  }

  Future<HealthResponse> getHealth() async => HealthResponse.fromJson(
    await _request('GET', '/v1/health', authenticated: false),
  );
  Future<bool> testAuth() async {
    await _request('GET', '/v1/projects', query: {'limit': '1'});
    return true;
  }

  Future<Map<String, dynamic>> redeemInvite(String code) => _request(
    'POST',
    '/v1/auth/redeem',
    authenticated: false,
    body: {'code': code.trim()},
  );
  Future<Map<String, dynamic>> getCurrentUser() =>
      _request('GET', '/v1/auth/me');
  Future<void> logout() async {
    await _request('POST', '/v1/auth/logout');
  }

  Future<Map<String, dynamic>> listBuildHistory({
    int offset = 0,
    int limit = 30,
  }) => _request(
    'GET',
    '/v1/history',
    query: {'offset': '$offset', 'limit': '$limit'},
  );
  Future<SigningProfile> createPersonalSigningProfile() async =>
      SigningProfile.fromJson(await _request('POST', '/v1/keys/personal'));

  Future<List<ProjectInfo>> listProjects() async {
    final projects = <ProjectInfo>[];
    while (true) {
      final data = await _request(
        'GET',
        '/v1/projects',
        query: {'offset': '${projects.length}', 'limit': '100'},
      );
      final page = (data['projects'] as List)
          .map((p) => ProjectInfo.fromJson(p))
          .toList();
      projects.addAll(page);
      if (page.isEmpty || projects.length >= (data['total'] as int)) {
        return projects;
      }
    }
  }

  Future<ProjectInfo> getProject(String id) async =>
      ProjectInfo.fromJson(await _request('GET', '/v1/projects/$id'));
  Future<AnalysisResult> getAnalysis(String id) async =>
      AnalysisResult.fromJson(
        await _request(
          'GET',
          '/v1/projects/$id/analysis',
          query: {'compact': 'true'},
        ),
      );

  Future<JobInfo> importApk(
    String filePath, {
    required String idempotencyKey,
    TransferCallback? onProgress,
  }) async {
    final file = File(filePath);
    return importApkStream(
      file.uri.pathSegments.last,
      await file.length(),
      file.openRead(),
      idempotencyKey: idempotencyKey,
      onProgress: onProgress,
    );
  }

  Future<JobInfo> importApkStream(
    String filename,
    int length,
    Stream<List<int>> bytes, {
    required String idempotencyKey,
    TransferCallback? onProgress,
  }) async {
    if (token?.isNotEmpty != true) throw UnauthorizedException();
    final request = http.MultipartRequest(
      'POST',
      _uri('/v1/import', {'authorized': 'true'}),
    );
    request.headers['Authorization'] = 'Bearer $token';
    request.headers['Idempotency-Key'] = idempotencyKey;
    final revision = credentialRevision;
    Stream<List<int>> counted() async* {
      var sent = 0;
      onProgress?.call(TransferProgress(0, length));
      await for (final chunk in bytes) {
        if (revision != credentialRevision) {
          throw ApiException('Workspace changed. Upload stopped.');
        }
        sent += chunk.length;
        onProgress?.call(TransferProgress(sent, length));
        yield chunk;
      }
    }

    request.files.add(
      http.MultipartFile('file', counted(), length, filename: filename),
    );
    return JobInfo.fromJson(
      _response(
        await _send(
          request,
          timeout: const Duration(minutes: 10),
          mutation: true,
        ),
      ),
    );
  }

  Future<List<FileEntry>> listFiles(String id, {String subdir = ''}) async {
    final data = await _request(
      'GET',
      '/v1/projects/$id/files',
      query: subdir.isEmpty ? null : {'subdir': subdir},
    );
    // Backend returns recursive relative paths, not directory entry objects.
    final entries = <String, FileEntry>{};
    final prefix = subdir.isEmpty ? '' : '$subdir/';
    for (final path in List<String>.from(data['files'])) {
      if (!path.startsWith(prefix)) continue;
      final rest = path.substring(prefix.length);
      if (rest.isEmpty) continue;
      final name = rest.split('/').first;
      entries[name] = FileEntry(
        name: name,
        path: '$prefix$name',
        isDirectory: rest.contains('/'),
      );
    }
    return entries.values.toList()..sort((a, b) {
      if (a.isDirectory != b.isDirectory) return a.isDirectory ? -1 : 1;
      return a.name.compareTo(b.name);
    });
  }

  Future<String> readFile(String id, String path) async =>
      (await _request(
            'GET',
            '/v1/projects/$id/files/read',
            query: {'path': path},
          ))['content']
          as String;
  Future<List<Map<String, dynamic>>> searchFiles(
    String id,
    String query,
  ) async => List<Map<String, dynamic>>.from(
    (await _request(
      'GET',
      '/v1/projects/$id/files/search',
      query: {'q': query},
    ))['results'],
  );
  Future<void> replaceFile(
    String id,
    String path,
    String content,
    int revision,
  ) async {
    await _request(
      'PUT',
      '/v1/projects/$id/files',
      body: {
        'relative_path': path,
        'content': content,
        'expected_revision': revision,
      },
    );
  }

  Future<ChangePlan> createPlan(
    String id,
    String request, {
    required bool allowAiUpload,
  }) async => ChangePlan.fromJson(
    await _request(
      'POST',
      '/v1/projects/$id/plans',
      body: {'user_request': request, 'allow_ai_upload': allowAiUpload},
    ),
  );
  Future<List<Map<String, dynamic>>> listPlans(String id) async =>
      List<Map<String, dynamic>>.from(
        (await _request('GET', '/v1/projects/$id/plans'))['plans'],
      );
  Future<ChangePlan> getPlan(String id, String plan) async =>
      ChangePlan.fromJson(
        await _request('GET', '/v1/projects/$id/plans/$plan'),
      );
  Future<ApprovalRecord> approvePlan(
    String id,
    String plan,
    String hash,
  ) async => ApprovalRecord.fromJson(
    await _request(
      'POST',
      '/v1/projects/$id/plans/$plan/approve',
      body: {'hash': hash},
    ),
  );
  Future<void> rejectPlan(String id, String plan) async {
    await _request('POST', '/v1/projects/$id/plans/$plan/reject');
  }

  Future<PatchSet> generatePatch(String id, String plan) async =>
      PatchSet.fromJson(
        await _request(
          'POST',
          '/v1/projects/$id/patches',
          query: {'plan_id': plan},
        ),
      );
  Future<List<Map<String, dynamic>>> listPatches(String id) async =>
      List<Map<String, dynamic>>.from(
        (await _request('GET', '/v1/projects/$id/patches'))['patches'],
      );
  Future<PatchSet> getPatch(String id, String patch) async => PatchSet.fromJson(
    await _request('GET', '/v1/projects/$id/patches/$patch'),
  );
  Future<List<PatchDiffEntry>> getPatchDiff(String id, String patch) async =>
      ((await _request('GET', '/v1/projects/$id/patches/$patch/diff'))['diff']
              as List)
          .map((d) => PatchDiffEntry.fromJson(d))
          .toList();
  Future<ApprovalRecord> approvePatch(
    String id,
    String patch,
    String hash,
  ) async => ApprovalRecord.fromJson(
    await _request(
      'POST',
      '/v1/projects/$id/patches/$patch/approve',
      body: {'hash': hash},
    ),
  );
  Future<Map<String, dynamic>> applyPatch(String id, String patch) =>
      _request('POST', '/v1/projects/$id/patches/$patch/apply');
  Future<Map<String, dynamic>> undoPatch(String id, String patch) =>
      _request('POST', '/v1/projects/$id/patches/$patch/undo');

  Future<ManualSession> getManualSession(String id) async =>
      ManualSession.fromJson(
        await _request('GET', '/v1/projects/$id/manual/session'),
      );
  Future<void> beginManualSession(String id) async {
    await _request('POST', '/v1/projects/$id/manual/begin');
  }

  Future<Map<String, dynamic>> recordManualChanges(String id, String message) =>
      _request(
        'POST',
        '/v1/projects/$id/manual/record',
        body: {'message': message},
      );
  Future<ValidationResult> validate(String id) async =>
      ValidationResult.fromJson(
        await _request('POST', '/v1/projects/$id/validate'),
      );
  Future<JobInfo> startBuild(
    String id, {
    required String idempotencyKey,
  }) async => JobInfo.fromJson(
    await _request(
      'POST',
      '/v1/projects/$id/build',
      idempotencyKey: idempotencyKey,
    ),
  );
  Future<List<BuildResult>> listBuilds(String id) async =>
      ((await _request('GET', '/v1/projects/$id/builds'))['builds'] as List)
          .map((b) => BuildResult.fromJson(b))
          .toList();

  Future<List<SigningProfile>> listSigningProfiles() async =>
      ((await _request('GET', '/v1/keys'))['profiles'] as List)
          .map((p) => SigningProfile.fromJson(p))
          .toList();
  Future<Map<String, dynamic>> signBuild(
    String id,
    String build,
    String profile,
  ) => _request(
    'POST',
    '/v1/projects/$id/sign',
    body: {'build_id': build, 'profile': profile, 'confirm': true},
  );
  Future<Map<String, dynamic>> verifyBuild(String id, String build) =>
      _request('GET', '/v1/projects/$id/builds/$build/verify');

  Future<Uint8List> downloadArtifact(
    String id,
    String build, {
    String artifact = 'signed',
    TransferCallback? onProgress,
  }) async {
    if (token?.isNotEmpty != true) throw UnauthorizedException();
    final request = http.Request(
      'GET',
      _uri('/v1/projects/$id/builds/$build/download', {'artifact': artifact}),
    );
    request.headers['Authorization'] = 'Bearer $token';
    request.followRedirects = false;
    final revision = credentialRevision;
    void checkIdentity() {
      if (revision != credentialRevision) {
        throw ApiException('Workspace changed. Download discarded.');
      }
    }

    try {
      final response = await _client
          .send(request)
          .timeout(const Duration(seconds: 30));
      checkIdentity();
      if (response.statusCode != 200) {
        final error = await http.Response.fromStream(
          response,
        ).timeout(const Duration(seconds: 30));
        checkIdentity();
        _response(error);
      }
      final result = BytesBuilder(copy: false);
      final total = response.contentLength;
      onProgress?.call(TransferProgress(0, total));
      await for (final chunk in response.stream.timeout(
        const Duration(seconds: 60),
      )) {
        checkIdentity();
        result.add(chunk);
        onProgress?.call(TransferProgress(result.length, total));
      }
      checkIdentity();
      if (total != null && result.length != total) {
        throw ApiException(
          'Download incomplete. Nothing was saved; try downloading again.',
        );
      }
      return result.takeBytes();
    } on TimeoutException {
      throw ConnectionException(
        'Download stalled. Your APK is safe in History; download it again.',
      );
    } on SocketException {
      throw ConnectionException(
        'Download connection lost. Try downloading again from History.',
      );
    } on http.ClientException {
      throw ConnectionException(
        'Download connection lost. Try downloading again from History.',
      );
    }
  }

  Future<JobInfo> prepareWorkflow(
    String id,
    String text,
    int revision,
    String key,
  ) async => JobInfo.fromJson(
    await _request(
      'POST',
      '/v1/projects/$id/workflow/prepare',
      idempotencyKey: key,
      body: {
        'user_request': text,
        'allow_ai_upload': true,
        'revision': revision,
      },
    ),
  );

  Future<JobInfo> finishWorkflow(
    String id,
    ChangePlan plan,
    PatchSet patch,
    String key,
  ) async => JobInfo.fromJson(
    await _request(
      'POST',
      '/v1/projects/$id/workflow/finish',
      idempotencyKey: key,
      body: {
        'plan_id': plan.planId,
        'patch_id': patch.patchId,
        'plan_hash': plan.planHash,
        'patch_hash': patch.patchHash,
        'revision': plan.workspaceRevision,
        'confirm': true,
      },
    ),
  );

  Future<Map<String, dynamic>> getAuditJson(String id) =>
      _request('GET', '/v1/projects/$id/audit', query: {'format': 'json'});
  Future<String> getAuditMarkdown(String id) async =>
      (await _request(
            'GET',
            '/v1/projects/$id/audit',
            query: {'format': 'markdown'},
          ))['markdown']
          as String;
  Future<List<AuditEvent>> listEvents(String id, {String? after}) async =>
      ((await _request(
                'GET',
                '/v1/projects/$id/events',
                query: {'after': ?after, 'limit': '100'},
              ))['events']
              as List)
          .map((e) => AuditEvent.fromJson(e))
          .toList();
  Future<List<JobInfo>> listJobs({String? projectId}) async =>
      ((await _request(
                'GET',
                '/v1/jobs',
                query: {'project_id': ?projectId},
              ))['jobs']
              as List)
          .map((j) => JobInfo.fromJson(j))
          .toList();
  Future<JobInfo> getJob(String id) async =>
      JobInfo.fromJson(await _request('GET', '/v1/jobs/$id'));
  Future<JobInfo> cancelJob(String id) async =>
      JobInfo.fromJson(await _request('POST', '/v1/jobs/$id/cancel'));

  /// SSE frames may span arbitrary network chunks; only complete frames count.
  Stream<AuditEvent> streamJobEvents(String id, {String? after}) async* {
    final revision = credentialRevision;
    final request = http.Request(
      'GET',
      _uri('/v1/jobs/$id/events', {'after': ?after}),
    );
    if (token?.isNotEmpty != true) throw UnauthorizedException();
    request.headers.addAll({
      'Authorization': 'Bearer $token',
      'Accept': 'text/event-stream',
    });
    request.followRedirects = false;
    final response = await _client.send(request);
    if (revision != credentialRevision) {
      throw ApiException('Workspace changed. Event stream closed.');
    }
    if (response.statusCode != 200) {
      _response(await http.Response.fromStream(response));
      return;
    }
    final seen = <String>{?after};
    var type = '';
    final data = <String>[];
    await for (final line
        in response.stream
            .transform(utf8.decoder)
            .transform(const LineSplitter())) {
      if (revision != credentialRevision) {
        throw ApiException('Workspace changed. Event stream closed.');
      }
      if (line.isEmpty) {
        if (type == 'done') return;
        if (type == 'error') {
          throw ApiException(
            'Event stream cursor rejected. Refresh job status.',
          );
        }
        if (data.isNotEmpty) {
          final event = AuditEvent.fromJson(jsonDecode(data.join('\n')));
          if (event.eventId.isNotEmpty && seen.add(event.eventId)) yield event;
        }
        type = '';
        data.clear();
      } else if (line.startsWith('event:')) {
        type = line.substring(6).trim();
      } else if (line.startsWith('data:')) {
        data.add(line.substring(5).trimLeft());
      }
    }
  }

  void dispose() => _client.close();
}
