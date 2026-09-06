import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:noir_app/core/state/build_controller.dart';
import 'package:noir_app/core/state/connection_controller.dart';
import 'package:noir_app/core/state/signing_controller.dart';
import 'package:noir_app/core/state/workspace_controller.dart';
import 'package:noir_app/data/api/api_exceptions.dart';
import 'package:noir_app/data/api/noir_api_client.dart';
import 'package:noir_app/data/models/models.dart';

http.Response jsonResponse(Object data, [int status = 200]) => http.Response(
  jsonEncode(data),
  status,
  headers: {'content-type': 'application/json'},
);
NoirApiClient apiWith(
  Future<http.Response> Function(http.Request) handler, {
  int uploadParallelism = 4,
}) => NoirApiClient(
  token: 'test-only-token',
  client: MockClient(handler),
  uploadParallelism: uploadParallelism,
);

class FragmentedSseClient extends http.BaseClient {
  FragmentedSseClient(this.text);
  final String text;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async =>
      http.StreamedResponse(
        Stream.fromIterable(utf8.encode(text).map((byte) => [byte])),
        200,
      );
}

void main() {
  test(
    'restoring a persisted job refreshes its real state without restarting it',
    () async {
      final api = apiWith((request) async {
        expect(request.method, 'GET');
        final job = {
          'job_id': 'j',
          'project_id': 'p',
          'state': 'interrupted',
          'stage': 'rebuilding',
          'error_message': 'Worker stopped',
          'result_data': {'operation': 'build'},
        };
        if (request.url.path == '/v1/jobs') {
          return jsonResponse({
            'jobs': [job],
          });
        }
        if (request.url.path == '/v1/jobs/j') return jsonResponse(job);
        if (request.url.path.endsWith('/events')) {
          return jsonResponse({
            'events': [
              {
                'event_id': 'e',
                'project_id': 'p',
                'job_id': 'j',
                'message': 'Apktool build started',
              },
            ],
          });
        }
        return jsonResponse({'builds': []});
      });
      final controller = BuildController(api);
      await controller.loadBuilds('p');
      expect(controller.currentJob!.state, 'interrupted');
      expect(controller.building, false);
      expect(controller.error, 'Worker stopped');
      expect(controller.logEntries.single, '[info] Apktool build started');
      controller.dispose();
      api.dispose();
    },
  );
  test('file tree groups recursive backend path strings', () async {
    final api = apiWith((request) async {
      expect(request.headers['authorization'], 'Bearer test-only-token');
      return jsonResponse({
        'files': [
          'AndroidManifest.xml',
          'res/values/strings.xml',
          'res/drawable/icon.xml',
          'smali/Main.smali',
        ],
      });
    });
    final entries = await api.listFiles('p');
    expect(entries.map((e) => e.name), ['res', 'smali', 'AndroidManifest.xml']);
    expect(entries.first.isDirectory, true);
    final child = await api.listFiles('p', subdir: 'res');
    expect(child.map((e) => e.path), ['res/drawable', 'res/values']);
    api.dispose();
  });

  test('plan approval sends the exact displayed hash only', () async {
    final hash = 'a' * 64;
    final api = apiWith((request) async {
      expect(request.url.path, '/v1/projects/p/plans/plan/approve');
      expect(jsonDecode(request.body), {'hash': hash});
      expect(request.followRedirects, false);
      return jsonResponse({'target_hash': hash});
    });
    expect((await api.approvePlan('p', 'plan', hash)).targetHash, hash);
    api.dispose();
  });

  test('plan upload consent is explicit and forwarded', () async {
    final api = apiWith((request) async {
      expect(jsonDecode(request.body)['allow_ai_upload'], false);
      return jsonResponse({'error': 'Must set allow_ai_upload=true'}, 400);
    });
    await expectLater(
      api.createPlan('p', 'rename', allowAiUpload: false),
      throwsA(isA<ApiException>()),
    );
    api.dispose();
  });

  test(
    'empty validation does not pass; errors redact the bearer token',
    () async {
      expect(ValidationResult.fromJson({}).passed, false);
      final api = apiWith(
        (_) async => jsonResponse({'error': 'bad test-only-token'}, 400),
      );
      await expectLater(
        api.validate('p'),
        throwsA(predicate((e) => e.toString() == 'bad [REDACTED]')),
      );
      api.dispose();
    },
  );

  test('failed mutation is sent once, never replayed', () async {
    var calls = 0;
    final api = apiWith((_) async {
      calls++;
      throw http.ClientException('offline');
    });
    await expectLater(
      api.generatePatch('p', 'plan'),
      throwsA(isA<ConnectionException>()),
    );
    expect(calls, 1);
    api.dispose();
  });

  test('health alone cannot mark a tokenless session connected', () async {
    final api = NoirApiClient(
      client: MockClient((_) async => jsonResponse({'status': 'ok'})),
    );
    final connection = ConnectionController(client: api, restore: false);
    expect(await connection.testConnection(), false);
    expect(connection.isConnected, false);
    connection.dispose();
  });

  test('remote HTTP and credential-bearing URLs are rejected', () {
    for (final url in [
      'http://192.168.1.2:8787',
      'http://user:pass@localhost:8787',
      'https://host/path',
      'https://host?token=secret',
    ]) {
      expect(
        () => NoirApiClient.normalizeBaseUrl(url),
        throwsA(isA<ApiException>()),
      );
    }
    expect(
      NoirApiClient.normalizeBaseUrl('http://127.0.0.1:8787/'),
      'http://127.0.0.1:8787',
    );
    expect(
      NoirApiClient.normalizeBaseUrl('https://backend.example'),
      'https://backend.example',
    );
  });

  test(
    'SSE reconstructs split UTF-8 frames and does not treat done as a log',
    () async {
      final api = NoirApiClient(
        token: 'fixture',
        client: FragmentedSseClient(
          'id: e1\ndata: {"event_id":"e1","project_id":"p","message":"décode"}\n\n'
          'id: e1\ndata: {"event_id":"e1","project_id":"p","message":"duplicate"}\n\n'
          'event: done\ndata: {"job_id":"job","state":"succeeded"}\n\n',
        ),
      );
      final events = await api.streamJobEvents('job').toList();
      expect(events.map((e) => e.message), ['décode']);
      api.dispose();
    },
  );

  test(
    'resumable upload sends offset chunks and finalizes idempotently',
    () async {
      final source = utf8.encode('test-apk-bytes');
      var serverOffset = 0;
      final acknowledged = <int>[];
      final api = apiWith((request) async {
        if (request.url.path == '/v1/uploads' && request.method == 'POST') {
          expect(request.headers['idempotency-key'], 'upload-key');
          expect(jsonDecode(request.body), {
            'filename': 'fixture.apk',
            'size': source.length,
            'sha256': sha256.convert(source).toString(),
            'upload_mode': 'auto',
          });
          return jsonResponse({
            'upload_id': 'upload',
            'size': source.length,
            'offset': serverOffset,
            'chunk_size': 5,
          }, 201);
        }
        if (request.url.path == '/v1/uploads/upload' &&
            request.method == 'PATCH') {
          expect(request.headers['content-type'], 'application/octet-stream');
          expect(int.parse(request.headers['upload-offset']!), serverOffset);
          serverOffset += request.bodyBytes.length;
          acknowledged.add(serverOffset);
          return jsonResponse({
            'upload_id': 'upload',
            'size': source.length,
            'offset': serverOffset,
            'chunk_size': 5,
          });
        }
        expect(request.url.path, '/v1/uploads/upload/complete');
        expect(request.url.queryParameters['authorized'], 'true');
        expect(request.headers['idempotency-key'], 'upload-key');
        return jsonResponse({
          'job_id': 'j',
          'project_id': 'p',
          'state': 'queued',
        }, 202);
      }, uploadParallelism: 1);
      final job = await api.importApkResumable(
        'fixture.apk',
        source.length,
        (start, end) => Stream.value(source.sublist(start, end)),
        idempotencyKey: 'upload-key',
      );
      expect(job.projectId, 'p');
      expect(job.isTerminal, false);
      expect(acknowledged, [5, 10, 14]);
      api.dispose();
    },
  );

  test('manual stale revision preserves the unsaved buffer', () async {
    final api = apiWith((request) async {
      if (request.method == 'PUT') {
        expect(jsonDecode(request.body)['expected_revision'], 0);
        return jsonResponse({'detail': 'stale revision'}, 409);
      }
      if (request.url.path.endsWith('/manual/session')) {
        return jsonResponse({
          'active': true,
          'session': {'workspace_revision_start': 0},
        });
      }
      if (request.url.path.endsWith('/files/read')) {
        return jsonResponse({'content': 'before'});
      }
      if (request.url.path.endsWith('/files')) {
        return jsonResponse({
          'files': ['label.txt'],
        });
      }
      return jsonResponse({'id': 'p', 'workspace_revision': 0});
    });
    final ws = WorkspaceController(api);
    await ws.initialize('p');
    await ws.openFile('label.txt');
    ws.edit('after');
    expect(await ws.saveFile(), false);
    expect(ws.draft, 'after');
    expect(ws.fileContent, 'before');
    expect(ws.dirty, true);
    expect(ws.error, 'stale revision');
    ws.dispose();
    api.dispose();
  });

  test('failed validation blocks build creation', () async {
    final api = apiWith((request) async {
      expect(request.url.path.endsWith('/validate'), true);
      return jsonResponse({'passed': false, 'error_count': 1});
    });
    final build = BuildController(api);
    await build.validate('p');
    expect(await build.startBuild('p'), isNull);
    build.dispose();
    api.dispose();
  });

  test('download requires a verified signature and matching SHA-256', () async {
    final bytes = Uint8List.fromList(utf8.encode('signed APK fixture'));
    var verified = false;
    var downloads = 0;
    final api = apiWith((request) async {
      if (request.url.path.endsWith('/verify')) {
        return jsonResponse({'verified': verified});
      }
      downloads++;
      return http.Response.bytes(bytes, 200);
    });
    final signing = SigningController(api);
    BuildResult build(String hash) => BuildResult.fromJson({
      'build_id': 'b',
      'signed_apk_hash': hash,
      'success': true,
    });
    await expectLater(
      signing.verifiedDownload('p', build(sha256.convert(bytes).toString())),
      throwsA(isA<ApiException>()),
    );
    expect(downloads, 0);
    verified = true;
    await expectLater(
      signing.verifiedDownload('p', build('0' * 64)),
      throwsA(isA<ApiException>()),
    );
    expect(
      await signing.verifiedDownload(
        'p',
        build(sha256.convert(bytes).toString()),
      ),
      bytes,
    );
    signing.dispose();
    api.dispose();
  });
}
