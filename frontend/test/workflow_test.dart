import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:crypto/crypto.dart' as crypto;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:noir_app/data/api/noir_api_client.dart';
import 'package:noir_app/data/api/api_exceptions.dart';
import 'package:noir_app/data/api/transfer_progress.dart';
import 'package:noir_app/data/models/models.dart';
import 'package:noir_app/core/state/workflow_controller.dart';

class StreamClient extends http.BaseClient {
  StreamClient(this.handler);
  final Future<http.StreamedResponse> Function(http.BaseRequest) handler;
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      handler(request);
}

http.Response jsonResponse(Object data, [int status = 200]) =>
    http.Response(jsonEncode(data), status);

void main() {
  test('installed-app cache copy is removed after its upload', () async {
    final directory = await Directory.systemTemp.createTemp('noir-app-test-');
    addTearDown(() => directory.delete(recursive: true));
    final copiedApk = File('${directory.path}/example.chess.apk');
    await copiedApk.writeAsBytes([1, 2, 3, 4]);
    final job = {
      'job_id': 'import-job',
      'project_id': 'project',
      'state': 'succeeded',
      'stage': 'complete',
      'result_data': {'operation': 'import', 'result': <String, Object>{}},
    };
    final requestedPaths = <String>[];
    final api = NoirApiClient(
      token: 'test',
      client: StreamClient((request) async {
        requestedPaths.add(request.url.path);
        if (request.url.path == '/v1/uploads' && request.method == 'POST') {
          await request.finalize().drain<void>();
          return http.StreamedResponse(
            Stream.value(
              utf8.encode(
                jsonEncode({
                  'upload_id': 'upload',
                  'size': 4,
                  'offset': 0,
                  'chunk_size': 4,
                }),
              ),
            ),
            201,
          );
        }
        if (request.url.path == '/v1/uploads/upload' &&
            request.method == 'PATCH') {
          expect(request.headers['upload-offset'], '0');
          expect(await request.finalize().expand((chunk) => chunk).toList(), [
            1,
            2,
            3,
            4,
          ]);
          return http.StreamedResponse(
            Stream.value(
              utf8.encode(
                jsonEncode({
                  'upload_id': 'upload',
                  'size': 4,
                  'offset': 4,
                  'chunk_size': 4,
                }),
              ),
            ),
            200,
          );
        }
        if (request.url.path == '/v1/uploads/upload/complete' &&
            request.method == 'POST') {
          expect(request.url.queryParameters['authorized'], 'true');
          await request.finalize().drain<void>();
          return http.StreamedResponse(
            Stream.value(utf8.encode(jsonEncode(job))),
            202,
          );
        }
        final body = request.url.path == '/v1/jobs/import-job'
            ? job
            : {
                'id': 'project',
                'original_filename': 'example.chess.apk',
                'workspace_revision': 0,
              };
        return http.StreamedResponse(
          Stream.value(utf8.encode(jsonEncode(body))),
          200,
        );
      }),
    );
    final flow = WorkflowController(api);
    await flow.importLocalApk(
      path: copiedApk.path,
      filename: 'example.chess.apk',
      length: 4,
      deleteAfter: true,
    );
    expect(await copiedApk.exists(), false);
    expect(flow.project?.id, 'project');
    expect(flow.error, isNull);
    expect(requestedPaths, isNot(contains('/v1/import')));
    flow.dispose();
    api.dispose();
  });

  test('safe-point multipart upload reports actual APK bytes', () async {
    final progress = <TransferProgress>[];
    final api = NoirApiClient(
      token: 'test',
      client: StreamClient((request) async {
        expect(request.url.path, '/v1/import');
        expect(request.url.queryParameters['authorized'], 'true');
        expect(request.headers['idempotency-key'], 'safe-upload');
        await request.finalize().drain<void>();
        return http.StreamedResponse(
          Stream.value(
            utf8.encode(
              jsonEncode({'job_id': 'j', 'project_id': 'p', 'state': 'queued'}),
            ),
          ),
          202,
        );
      }),
    );
    await api.importApkStream(
      'app.apk',
      6,
      Stream.fromIterable([
        [1, 2],
        [3, 4],
        [5, 6],
      ]),
      idempotencyKey: 'safe-upload',
      onProgress: progress.add,
    );
    expect(progress.map((item) => item.bytes), [0, 2, 4, 6]);
    expect(progress.every((item) => item.total == 6), true);
    api.dispose();
  });

  test(
    'upload progress advances only from server offsets and resumes a lost response',
    () async {
      final progress = <TransferProgress>[];
      final source = [1, 2, 3, 4, 5, 6];
      final patchOffsets = <int>[];
      var serverOffset = 0;
      var loseSecondResponse = true;
      final client = StreamClient((request) async {
        expect(request.followRedirects, false);
        if (request.url.path == '/v1/uploads' && request.method == 'POST') {
          await request.finalize().drain<void>();
          return http.StreamedResponse(
            Stream.value(
              utf8.encode(
                jsonEncode({
                  'upload_id': 'upload',
                  'size': 6,
                  'offset': serverOffset,
                  'chunk_size': 2,
                }),
              ),
            ),
            201,
          );
        }
        if (request.url.path == '/v1/uploads/upload' &&
            request.method == 'GET') {
          return http.StreamedResponse(
            Stream.value(
              utf8.encode(
                jsonEncode({
                  'upload_id': 'upload',
                  'size': 6,
                  'offset': serverOffset,
                  'chunk_size': 2,
                }),
              ),
            ),
            200,
          );
        }
        if (request.url.path == '/v1/uploads/upload' &&
            request.method == 'PATCH') {
          final requestOffset = int.parse(request.headers['upload-offset']!);
          patchOffsets.add(requestOffset);
          final body = await request.finalize().fold<List<int>>(
            [],
            (all, chunk) => all..addAll(chunk),
          );
          expect(requestOffset, serverOffset);
          serverOffset += body.length;
          if (requestOffset == 2 && loseSecondResponse) {
            loseSecondResponse = false;
            throw http.ClientException('response lost after durable write');
          }
          return http.StreamedResponse(
            Stream.value(
              utf8.encode(
                jsonEncode({
                  'upload_id': 'upload',
                  'size': 6,
                  'offset': serverOffset,
                  'chunk_size': 2,
                }),
              ),
            ),
            200,
          );
        }
        expect(request.url.path, '/v1/uploads/upload/complete');
        await request.finalize().drain<void>();
        return http.StreamedResponse(
          Stream.value(
            utf8.encode(
              jsonEncode({
                'job_id': 'j',
                'project_id': 'p',
                'state': 'queued',
                'stage': 'validating_input',
              }),
            ),
          ),
          202,
        );
      });
      final api = NoirApiClient(
        token: 'test',
        client: client,
        retryDelay: (_) async {},
        uploadParallelism: 1,
      );
      await api.importApkResumable(
        'app.apk',
        6,
        (start, end) => Stream.value(source.sublist(start, end)),
        idempotencyKey: 'test',
        onProgress: progress.add,
      );
      expect(progress.map((p) => p.bytes), [0, 2, 4, 6]);
      expect(progress.every((p) => p.total == 6), true);
      expect(progress.last.fraction, 1);
      expect(patchOffsets, [0, 2, 4]);
      api.dispose();
    },
  );

  test('resumable upload uses four parallel workers by default', () async {
    final source = List<int>.generate(20, (index) => index);
    final received = <int>{};
    final progress = <TransferProgress>[];
    var inFlight = 0;
    var maximumInFlight = 0;

    Map<String, Object> session() {
      var contiguous = 0;
      while (received.contains(contiguous)) {
        contiguous += 2;
      }
      return {
        'upload_id': 'parallel',
        'size': source.length,
        'offset': contiguous,
        'chunk_size': 2,
        'received_bytes': received.length * 2,
        'received_offsets': received.toList(),
      };
    }

    final api = NoirApiClient(
      token: 'test',
      retryDelay: (_) async {},
      client: StreamClient((request) async {
        if (request.url.path == '/v1/uploads' && request.method == 'POST') {
          await request.finalize().drain<void>();
          return http.StreamedResponse(
            Stream.value(utf8.encode(jsonEncode(session()))),
            201,
          );
        }
        if (request.url.path == '/v1/uploads/parallel' &&
            request.method == 'PATCH') {
          final offset = int.parse(request.headers['upload-offset']!);
          final body = await request.finalize().fold<List<int>>(
            [],
            (all, chunk) => all..addAll(chunk),
          );
          expect(body, source.sublist(offset, offset + 2));
          inFlight++;
          maximumInFlight = inFlight > maximumInFlight
              ? inFlight
              : maximumInFlight;
          await Future<void>.delayed(const Duration(milliseconds: 10));
          received.add(offset);
          inFlight--;
          return http.StreamedResponse(
            Stream.value(utf8.encode(jsonEncode(session()))),
            200,
          );
        }
        expect(request.url.path, '/v1/uploads/parallel/complete');
        await request.finalize().drain<void>();
        return http.StreamedResponse(
          Stream.value(
            utf8.encode(
              jsonEncode({'job_id': 'j', 'project_id': 'p', 'state': 'queued'}),
            ),
          ),
          202,
        );
      }),
    );

    await api.importApkResumable(
      'parallel.apk',
      source.length,
      (start, end) => Stream.value(source.sublist(start, end)),
      idempotencyKey: 'parallel',
      onProgress: progress.add,
    );
    expect(maximumInFlight, 4);
    expect(received, {0, 2, 4, 6, 8, 10, 12, 14, 16, 18});
    expect(progress.last.bytes, 20);
    api.dispose();
  });

  test('resumable upload sends APK parts directly to private S3', () async {
    final source = <int>[1, 2, 3, 4, 5, 6];
    final wholeSha256 = crypto.sha256.convert(source).toString();
    final partChecksum = base64Encode(crypto.sha256.convert(source).bytes);
    final progress = <TransferProgress>[];
    var sawDirectPut = false;

    Map<String, Object> session({bool complete = false}) => {
      'upload_mode': 's3',
      'protocol': 's3_multipart_v1',
      'upload_id': 'upload',
      'project_id': 'private-project',
      'size': source.length,
      'sha256': wholeSha256,
      'part_size': 5 * 1024 * 1024,
      'total_parts': 1,
      'uploaded_bytes': complete ? source.length : 0,
      'completed_parts': complete
          ? [
              {
                'part_number': 1,
                'etag': '"0123456789abcdef0123456789abcdef"',
                'checksum_sha256': partChecksum,
                'size': source.length,
              },
            ]
          : <Object>[],
      'state': complete ? 'uploaded' : 'uploading',
    };

    final api = NoirApiClient(
      token: 'private-token',
      retryDelay: (_) async {},
      client: StreamClient((request) async {
        if (request.url.path == '/v1/uploads' && request.method == 'POST') {
          expect(request.headers['authorization'], 'Bearer private-token');
          final body = await request.finalize().fold<List<int>>(
            [],
            (all, chunk) => all..addAll(chunk),
          );
          expect(jsonDecode(utf8.decode(body)), {
            'filename': 'fixture.apk',
            'size': source.length,
            'sha256': wholeSha256,
            'upload_mode': 'auto',
          });
          return http.StreamedResponse(
            Stream.value(utf8.encode(jsonEncode(session()))),
            201,
          );
        }
        if (request.url.path == '/v1/uploads/upload/parts/presign') {
          expect(request.headers['authorization'], 'Bearer private-token');
          final body = await request.finalize().fold<List<int>>(
            [],
            (all, chunk) => all..addAll(chunk),
          );
          expect(jsonDecode(utf8.decode(body)), {
            'parts': [
              {'part_number': 1, 'checksum_sha256': partChecksum},
            ],
          });
          return http.StreamedResponse(
            Stream.value(
              utf8.encode(
                jsonEncode({
                  'parts': [
                    {
                      'part_number': 1,
                      'url':
                          'https://noir-test.s3.ap-south-1.amazonaws.com/noir/input.apk?uploadId=u&partNumber=1&X-Amz-Signature=test',
                      'headers': {'x-amz-checksum-sha256': partChecksum},
                    },
                  ],
                }),
              ),
            ),
            200,
          );
        }
        if (request.url.host == 'noir-test.s3.ap-south-1.amazonaws.com') {
          sawDirectPut = true;
          expect(request.method, 'PUT');
          expect(request.headers.containsKey('authorization'), false);
          expect(request.headers['x-amz-checksum-sha256'], partChecksum);
          final body = await request.finalize().fold<List<int>>(
            [],
            (all, chunk) => all..addAll(chunk),
          );
          expect(body, source);
          return http.StreamedResponse(
            const Stream.empty(),
            200,
            headers: {'etag': '"0123456789abcdef0123456789abcdef"'},
          );
        }
        if (request.url.path == '/v1/uploads/upload/parts' &&
            request.method == 'PUT') {
          expect(request.headers['authorization'], 'Bearer private-token');
          await request.finalize().drain<void>();
          return http.StreamedResponse(
            Stream.value(utf8.encode(jsonEncode(session(complete: true)))),
            200,
          );
        }
        expect(request.url.path, '/v1/uploads/upload/complete');
        expect(request.url.queryParameters['authorized'], 'true');
        expect(request.headers['authorization'], 'Bearer private-token');
        await request.finalize().drain<void>();
        return http.StreamedResponse(
          Stream.value(
            utf8.encode(
              jsonEncode({
                'job_id': 'import-job',
                'project_id': 'private-project',
                'state': 'queued',
              }),
            ),
          ),
          202,
        );
      }),
    );

    final job = await api.importApkResumable(
      'fixture.apk',
      source.length,
      (start, end) => Stream.value(source.sublist(start, end)),
      idempotencyKey: 'direct-s3',
      onProgress: progress.add,
    );
    expect(sawDirectPut, true);
    expect(job.projectId, 'private-project');
    expect(progress.map((value) => value.bytes), [0, source.length]);
    api.dispose();
  });

  test('download counts chunks and rejects a truncated file', () async {
    for (final total in [4, 8, null]) {
      final progress = <TransferProgress>[];
      final api = NoirApiClient(
        token: 'test',
        client: StreamClient(
          (_) async => http.StreamedResponse(
            Stream.fromIterable([
              [1, 2],
              [3, 4],
            ]),
            200,
            contentLength: total,
          ),
        ),
      );
      final future = api.downloadArtifact('p', 'b', onProgress: progress.add);
      if (total == 8) {
        await expectLater(future, throwsA(isA<ApiException>()));
      } else {
        expect(await future, [1, 2, 3, 4]);
      }
      expect(progress.map((p) => p.bytes), [0, 2, 4]);
      expect(progress.last.fraction, total == null ? null : 4 / total);
      api.dispose();
    }
  });

  test(
    'download follows one HTTPS S3 redirect without forwarding auth',
    () async {
      final requests = <http.BaseRequest>[];
      final api = NoirApiClient(
        token: 'private-token',
        client: StreamClient((request) async {
          requests.add(request);
          if (requests.length == 1) {
            return http.StreamedResponse(
              const Stream.empty(),
              307,
              headers: {
                'location':
                    'https://noir-bucket.s3.eu-north-1.amazonaws.com/noir/signed.apk?X-Amz-Signature=test',
              },
            );
          }
          return http.StreamedResponse(
            Stream.value([1, 2, 3]),
            200,
            contentLength: 3,
          );
        }),
      );

      expect(await api.downloadArtifact('p', 'b'), [1, 2, 3]);
      expect(requests, hasLength(2));
      expect(requests.first.headers['Authorization'], 'Bearer private-token');
      expect(requests.last.headers.containsKey('Authorization'), false);
      expect(requests.last.followRedirects, false);
      api.dispose();
    },
  );

  test('download rejects redirects outside Amazon S3', () async {
    final api = NoirApiClient(
      token: 'private-token',
      client: StreamClient(
        (_) async => http.StreamedResponse(
          const Stream.empty(),
          307,
          headers: {'location': 'https://example.com/stolen.apk'},
        ),
      ),
    );

    await expectLater(
      api.downloadArtifact('p', 'b'),
      throwsA(isA<ApiException>()),
    );
    api.dispose();
  });

  test(
    'account change mid-download discards bytes from the old workspace',
    () async {
      final chunks = StreamController<List<int>>();
      final received = Completer<void>();
      final api = NoirApiClient(
        token: 'alice',
        client: StreamClient(
          (_) async =>
              http.StreamedResponse(chunks.stream, 200, contentLength: 4),
        ),
      );
      final future = api.downloadArtifact(
        'p',
        'b',
        onProgress: (p) {
          if (p.bytes == 2) received.complete();
        },
      );
      final assertion = expectLater(future, throwsA(isA<ApiException>()));
      chunks.add([1, 2]);
      await received.future;
      api.token = 'bob';
      chunks.add([3, 4]);
      await chunks.close();
      await assertion;
      api.dispose();
    },
  );

  test(
    'preparation never approves automatically; finish is one exact-hash action',
    () async {
      final writes = <String>[];
      final plan = {
        'plan_id': 'plan',
        'project_id': 'p',
        'workspace_revision': 0,
        'user_request': 'rename',
        'plan_hash': 'a' * 64,
        'intended_outcome': 'Rename app',
        'file_changes': [],
      };
      final patch = {
        'patch_id': 'patch',
        'project_id': 'p',
        'plan_id': 'plan',
        'workspace_revision': 0,
        'patch_hash': 'b' * 64,
        'operations': [],
      };
      Map<String, Object> job(String id, {bool done = false}) => {
        'job_id': id,
        'project_id': 'p',
        'state': done ? 'succeeded' : 'queued',
        'stage': 'planning',
        'result_data': {
          'operation': id == 'prepare' ? 'workflow_prepare' : 'workflow_finish',
          'result': id == 'prepare'
              ? {'plan_id': 'plan', 'patch_id': 'patch'}
              : {'build_id': 'build'},
        },
      };
      final api = NoirApiClient(
        token: 'test',
        client: MockClient((request) async {
          final path = request.url.path;
          if (request.method == 'POST') {
            writes.add(path);
            if (path.endsWith('/finish')) {
              final data = jsonDecode(request.body);
              expect(data['confirm'], true);
              expect(data['plan_hash'], 'a' * 64);
              expect(data['patch_hash'], 'b' * 64);
            }
            return jsonResponse(
              job(path.endsWith('/prepare') ? 'prepare' : 'finish'),
              202,
            );
          }
          if (path == '/v1/jobs/prepare') {
            return jsonResponse(job('prepare', done: true));
          }
          if (path == '/v1/jobs/finish') {
            return jsonResponse(job('finish', done: true));
          }
          if (path.endsWith('/plans/plan')) return jsonResponse(plan);
          if (path.endsWith('/patches/patch')) return jsonResponse(patch);
          if (path.endsWith('/diff')) {
            return jsonResponse({
              'diff': [
                {
                  'path': 'res/values/strings.xml',
                  'operation': 'replace_block',
                  'preview': '- Old\n+ New',
                },
              ],
            });
          }
          if (path.endsWith('/builds')) {
            return jsonResponse({
              'builds': [
                {
                  'build_id': 'build',
                  'project_id': 'p',
                  'success': true,
                  'workspace_revision': 1,
                  'signed_apk_hash': 'c' * 64,
                },
              ],
            });
          }
          return jsonResponse({
            'id': 'p',
            'workspace_revision': 0,
            'original_filename': 'Example.apk',
          });
        }),
      );
      final flow = WorkflowController(api)
        ..project = ProjectInfo.fromJson({'id': 'p'});
      await flow.prepare('rename');
      expect(flow.previewReady, true);
      expect(flow.step, 1);
      expect(writes, ['/v1/projects/p/workflow/prepare']);
      await flow.approveAndBuild();
      expect(writes, [
        '/v1/projects/p/workflow/prepare',
        '/v1/projects/p/workflow/finish',
      ]);
      expect(flow.step, 2);
      expect(flow.build!.signedApkHash, 'c' * 64);
      flow.dispose();
      api.dispose();
    },
  );

  test(
    'unsupported preparation shows the plan without requesting a patch',
    () async {
      var patchRequested = false;
      final api = NoirApiClient(
        token: 'test',
        client: MockClient((request) async {
          final path = request.url.path;
          if (request.method == 'POST') {
            return jsonResponse({
              'job_id': 'prepare',
              'project_id': 'p',
              'state': 'queued',
              'stage': 'planning',
              'result_data': {'operation': 'workflow_prepare'},
            }, 202);
          }
          if (path == '/v1/jobs/prepare') {
            return jsonResponse({
              'job_id': 'prepare',
              'project_id': 'p',
              'state': 'succeeded',
              'stage': 'planning',
              'result_data': {
                'operation': 'workflow_prepare',
                'result': {'plan_id': 'unsupported-plan', 'unsupported': true},
              },
            });
          }
          if (path.endsWith('/plans/unsupported-plan')) {
            return jsonResponse({
              'plan_id': 'unsupported-plan',
              'project_id': 'p',
              'workspace_revision': 0,
              'user_request': 'change engine rules',
              'intended_outcome': 'No safe implementation was identified',
              'file_changes': [],
              'unsupported_aspects': ['No evidence-backed edit was found'],
            });
          }
          if (path.contains('/patches/')) {
            patchRequested = true;
            return jsonResponse({'error': 'must not be requested'}, 500);
          }
          return jsonResponse({
            'id': 'p',
            'workspace_revision': 0,
            'original_filename': 'Chess.apk',
          });
        }),
      );
      final flow = WorkflowController(api)
        ..project = ProjectInfo.fromJson({'id': 'p'});

      await flow.prepare('change engine rules');

      expect(flow.unsupportedPlan, true);
      expect(flow.previewReady, false);
      expect(flow.error, isNull);
      expect(flow.patch, isNull);
      expect(patchRequested, false);
      flow.dispose();
      api.dispose();
    },
  );

  test('failed diff never enables combined approval', () async {
    final api = NoirApiClient(
      token: 'test',
      client: MockClient((request) async {
        if (request.url.path.endsWith('/diff')) {
          return jsonResponse({'error': 'Preimage changed'}, 400);
        }
        if (request.url.path.contains('/jobs/')) {
          return jsonResponse({
            'job_id': 'j',
            'project_id': 'p',
            'state': 'succeeded',
            'stage': 'generating_patch',
            'result_data': {
              'operation': 'workflow_prepare',
              'result': {'plan_id': 'plan', 'patch_id': 'patch'},
            },
          });
        }
        if (request.url.path.endsWith('/plans/plan')) {
          return jsonResponse({'plan_id': 'plan', 'project_id': 'p'});
        }
        if (request.url.path.endsWith('/patches/patch')) {
          return jsonResponse({'patch_id': 'patch', 'project_id': 'p'});
        }
        return jsonResponse({'id': 'p'});
      }),
    );
    final flow = WorkflowController(api)
      ..job = JobInfo.fromJson({
        'job_id': 'j',
        'project_id': 'p',
        'state': 'queued',
        'stage': 'planning',
      });
    await flow.refresh();
    expect(flow.previewReady, false);
    expect(flow.error, contains('Preimage changed'));
    flow.dispose();
    api.dispose();
  });
}
