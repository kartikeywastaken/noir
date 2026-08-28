import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:noir_app/core/state/connection_controller.dart';
import 'package:noir_app/core/theme/noir_theme.dart';
import 'package:noir_app/data/api/api_exceptions.dart';
import 'package:noir_app/data/api/noir_api_client.dart';
import 'package:noir_app/features/history/build_history_screen.dart';
import 'package:noir_app/features/settings/settings_screen.dart';

http.Response jsonResponse(Object value, [int status = 200]) => http.Response(
  jsonEncode(value),
  status,
  headers: {'content-type': 'application/json'},
);
const health = {'status': 'ok', 'version': '0.1.0', 'capabilities': {}};

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    await (FontLoader(
      'MaterialIcons',
    )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
    for (final name in ['Inter', 'JetBrainsMono']) {
      await (FontLoader(
        name,
      )..addFont(rootBundle.load('assets/fonts/$name.ttf'))).load();
    }
  });
  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  test('default origin is cloud and a new app contains no shared token', () {
    final api = NoirApiClient();
    expect(api.baseUrl, 'https://noir-16-171-197-228.sslip.io');
    expect(api.token, isNull);
    api.dispose();
  });

  test(
    'saved session reconnects automatically and loads its identity',
    () async {
      FlutterSecureStorage.setMockInitialValues({
        'noir_token': 'private-session',
      });
      final seen = <String>[];
      final api = NoirApiClient(
        client: MockClient((request) async {
          expect(request.url.origin, NoirApiClient.cloudBaseUrl);
          seen.add(request.url.path);
          if (request.url.path.endsWith('/health')) return jsonResponse(health);
          expect(request.headers['Authorization'], 'Bearer private-session');
          return jsonResponse({'user_id': 'alice', 'name': 'Alice'});
        }),
      );
      final connection = ConnectionController(client: api);
      await connection.ready;
      expect(connection.isConnected, isTrue);
      expect(connection.workspaceName, 'Alice');
      expect(seen, ['/v1/health', '/v1/auth/me']);
      connection.dispose();
    },
  );

  test(
    'invitation activates on cloud without forwarding the previous token',
    () async {
      final api = NoirApiClient(
        baseUrl: 'https://custom.example',
        token: 'old-token',
        client: MockClient(
          (request) async => jsonResponse(
            request.url.path.endsWith('/health')
                ? health
                : {'user_id': 'alice', 'name': 'Alice'},
          ),
        ),
      );
      final connection = ConnectionController(
        client: api,
        restore: false,
        activationClientFactory: () => NoirApiClient(
          client: MockClient((request) async {
            expect(request.url.origin, NoirApiClient.cloudBaseUrl);
            expect(request.headers['Authorization'], isNull);
            expect(jsonDecode(request.body)['code'], 'one-time-invitation');
            return jsonResponse({
              'token': 'alice-session',
              'user': {'user_id': 'alice', 'name': 'Alice'},
            });
          }),
        ),
      );
      expect(await connection.activateInvite('one-time-invitation'), isTrue);
      expect(connection.api.token, 'alice-session');
      expect(connection.baseUrl, NoirApiClient.cloudBaseUrl);
      expect(
        await const FlutterSecureStorage().read(key: 'noir_token'),
        'alice-session',
      );
      expect(connection.sessionEpoch, greaterThan(0));
      connection.dispose();
    },
  );

  test('old response is discarded when the workspace changes', () async {
    final response = Completer<http.Response>();
    final api = NoirApiClient(
      token: 'alice',
      client: MockClient((_) => response.future),
    );
    final future = api.listBuildHistory();
    api.token = 'bob';
    response.complete(
      jsonResponse({
        'builds': [
          {'build_id': 'alice-private'},
        ],
        'total': 1,
      }),
    );
    await expectLater(future, throwsA(isA<ApiException>()));
    api.dispose();
  });

  test('changing origin never silently carries a saved token', () async {
    final api = NoirApiClient(token: 'owner');
    final connection = ConnectionController(client: api, restore: false);
    await expectLater(
      connection.configure('https://different.example', ''),
      throwsA(isA<ApiException>()),
    );
    expect(api.baseUrl, NoirApiClient.cloudBaseUrl);
    connection.dispose();
  });

  test(
    'sign out revokes the server session and clears secure storage',
    () async {
      FlutterSecureStorage.setMockInitialValues({'noir_token': 'alice'});
      var revoked = false;
      final api = NoirApiClient(
        token: 'alice',
        client: MockClient((request) async {
          expect(request.url.path, '/v1/auth/logout');
          revoked = true;
          return jsonResponse({'signed_out': true});
        }),
      );
      final connection = ConnectionController(client: api, restore: false);
      await connection.signOut();
      expect(revoked, isTrue);
      expect(connection.hasToken, isFalse);
      expect(
        await const FlutterSecureStorage().read(key: 'noir_token'),
        isNull,
      );
      connection.dispose();
    },
  );

  testWidgets('new user sees invitation, advanced address stays collapsed', (
    tester,
  ) async {
    final connection = ConnectionController(restore: false);
    await tester.pumpWidget(
      ChangeNotifierProvider.value(
        value: connection,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: NoirTheme.darkTheme,
          home: const SettingsScreen(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('ACTIVATE INVITATION'), findsOneWidget);
    expect(find.text('Invitation code'), findsOneWidget);
    expect(find.text('NOIR BEARER TOKEN'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    connection.dispose();
  });

  testWidgets(
    'History displays prior builds and does not sign or download on load',
    (tester) async {
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      var mutations = 0;
      final api = NoirApiClient(
        token: 'alice',
        client: MockClient((request) async {
          if (request.method != 'GET') mutations++;
          if (request.url.path == '/v1/jobs') return jsonResponse({'jobs': []});
          expect(request.url.path, '/v1/history');
          return jsonResponse({
            'total': 2,
            'builds': [
              {
                'project_id': 'p',
                'build_id': 'signed-build',
                'original_filename': 'Example.apk',
                'package_name': 'app.example',
                'workspace_revision': 1,
                'current_revision': 2,
                'success': true,
                'signed_apk_hash': 'a' * 64,
                'created_at': '2026-08-28T10:00:00Z',
              },
              {
                'project_id': 'p',
                'build_id': 'failed-build',
                'original_filename': 'Example.apk',
                'package_name': 'app.example',
                'workspace_revision': 2,
                'current_revision': 2,
                'success': false,
                'error_message': 'Resource compile failed',
                'created_at': '2026-08-28T11:00:00Z',
              },
            ],
          });
        }),
      );
      final connection = ConnectionController(client: api, restore: false);
      await tester.pumpWidget(
        ChangeNotifierProvider.value(
          value: connection,
          child: MaterialApp(
            debugShowCheckedModeBanner: false,
            theme: NoirTheme.darkTheme,
            home: const BuildHistoryScreen(),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('BUILD HISTORY'), findsOneWidget);
      expect(find.text('Example.apk · SIGNED'), findsOneWidget);
      expect(find.text('Verify & download APK'), findsOneWidget);
      expect(find.text('Example.apk · FAILED'), findsOneWidget);
      expect(find.text('Resource compile failed'), findsOneWidget);
      expect(mutations, 0);
      expect(tester.takeException(), isNull);
      await expectLater(
        find.byType(MaterialApp),
        matchesGoldenFile('goldens/history_phone.png'),
      );
      await tester.pumpWidget(const SizedBox());
      connection.dispose();
    },
  );
}
