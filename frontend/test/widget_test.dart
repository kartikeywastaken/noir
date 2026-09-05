import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:noir_app/main.dart';
import 'package:noir_app/core/state/connection_controller.dart';
import 'package:noir_app/core/state/workspace_controller.dart';
import 'package:noir_app/core/state/build_controller.dart';
import 'package:noir_app/core/theme/noir_theme.dart';
import 'package:noir_app/core/widgets/noir_button.dart';
import 'package:noir_app/data/api/noir_api_client.dart';
import 'package:noir_app/features/patch/patch_review_screen.dart';
import 'package:noir_app/features/plan/plan_review_screen.dart';
import 'package:noir_app/features/workspace/workspace_screen.dart';
import 'package:noir_app/features/build/build_screen.dart';
import 'package:noir_app/features/signing/signing_screen.dart';
import 'package:noir_app/features/audit/audit_screen.dart';

http.Response response(Object json, [int status = 200]) => http.Response.bytes(
  utf8.encode(jsonEncode(json)),
  status,
  headers: {'content-type': 'application/json; charset=utf-8'},
);
ConnectionController connection(
  Future<http.Response> Function(http.Request) handler,
) => ConnectionController(
  restore: false,
  client: NoirApiClient(token: 'test-only', client: MockClient(handler)),
);
Widget shell(ConnectionController conn, Widget child) =>
    ChangeNotifierProvider.value(
      value: conn,
      child: MaterialApp(theme: NoirTheme.darkTheme, home: child),
    );

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

  testWidgets(
    'phone home renders selected icon and opens connection settings',
    (tester) async {
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final conn = connection((_) async => response({}));
      await tester.pumpWidget(NoirApp(connection: conn));
      await tester.pumpAndSettle();
      await tester.runAsync(
        () => precacheImage(
          const AssetImage('assets/branding/phantom.png'),
          tester.element(find.byType(NoirApp)),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('DECODE & MODIFY'), findsOneWidget);
      expect(find.text('PROJECTS'), findsNothing);
      expect(find.byIcon(Icons.terminal), findsWidgets);
      final select = tester.widget<NoirPrimaryButton>(
        find.widgetWithText(NoirPrimaryButton, 'SELECT APK'),
      );
      expect(select.onPressed, isNull);
      await expectLater(
        find.byType(NoirApp),
        matchesGoldenFile('goldens/home_phone.png'),
      );
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      expect(find.text('YOUR WORKSPACE'), findsOneWidget);
      await tester.ensureVisible(find.text('Custom backend / owner access'));
      await tester.tap(find.text('Custom backend / owner access'));
      await tester.pumpAndSettle();
      expect(find.text('NOIR BEARER TOKEN'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('patch approval blocked when deterministic diff fails', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(900, 1100));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    var writes = 0;
    final conn = connection((request) async {
      if (request.method != 'GET') writes++;
      if (request.url.path.endsWith('/diff')) {
        return response({'error': 'Preimage changed'}, 400);
      }
      return response({
        'patch_id': 'patch',
        'project_id': 'p',
        'plan_id': 'plan',
        'patch_hash': 'a' * 64,
        'workspace_revision': 0,
        'operations': [
          {'relative_path': 'label.txt', 'operation': 'replace_block'},
        ],
      });
    });
    await tester.pumpWidget(
      shell(conn, const PatchReviewScreen(projectId: 'p', patchId: 'patch')),
    );
    await tester.pumpAndSettle();
    expect(find.text('Preimage changed'), findsOneWidget);
    final button = tester.widget<NoirPrimaryButton>(
      find.widgetWithText(NoirPrimaryButton, 'APPROVE PATCH'),
    );
    expect(button.onPressed, isNull);
    expect(writes, 0);
    await tester.pumpWidget(const SizedBox());
    conn.dispose();
  });

  testWidgets(
    'plan displays sensitive changes and approval never generates patch automatically',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(900, 1500));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      var approved = false;
      var patchCalls = 0;
      final hash = 'b' * 64;
      final conn = connection((request) async {
        if (request.url.path.endsWith('/approve')) {
          expect(jsonDecode(request.body), {'hash': hash});
          approved = true;
          return response({'target_hash': hash});
        }
        if (request.url.path.endsWith('/patches')) patchCalls++;
        return response({
          'plan_id': 'plan',
          'project_id': 'p',
          'plan_hash': hash,
          'user_request': 'Rename fixture',
          'intended_outcome': 'New display label',
          'network_destinations': ['https://example.invalid'],
          'data_categories': ['No collection'],
          'background_behavior': ['No background work'],
          'review': {'approved': approved, 'stale': false},
          'file_changes': [
            {
              'relative_path': 'AndroidManifest.xml',
              'operation': 'replace_block',
              'description': 'Label only',
            },
          ],
        });
      });
      await tester.pumpWidget(
        shell(conn, const PlanReviewScreen(projectId: 'p', planId: 'plan')),
      );
      await tester.pumpAndSettle();
      expect(find.text('NETWORK DESTINATIONS'), findsOneWidget);
      expect(find.text('DATA CATEGORIES'), findsOneWidget);
      await tester.ensureVisible(find.text('APPROVE PLAN'));
      await tester.tap(find.text('APPROVE PLAN'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Approve'));
      await tester.pumpAndSettle();
      expect(approved, true);
      expect(patchCalls, 0);
      expect(find.text('GENERATE PATCH'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      conn.dispose();
    },
  );

  testWidgets(
    'mobile workspace can edit, save and close a file without opening empty path',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      var saved = false;
      final conn = connection((request) async {
        final path = request.url.path;
        if (request.method == 'PUT') {
          expect(jsonDecode(request.body)['expected_revision'], 0);
          expect(jsonDecode(request.body)['content'], 'after');
          saved = true;
          return response({'replaced': 'label.txt'});
        }
        if (path.endsWith('/manual/session')) {
          return response({
            'active': true,
            'session': {'workspace_revision_start': 0},
          });
        }
        if (path.endsWith('/files/read')) {
          expect(request.url.queryParameters['path'], 'label.txt');
          return response({'content': 'before'});
        }
        if (path.endsWith('/files')) {
          return response({
            'files': ['label.txt'],
          });
        }
        return response({
          'id': 'p',
          'package_name': 'app.noir.fixture',
          'workspace_revision': 0,
        });
      });
      final ws = WorkspaceController(conn.api);
      await tester.pumpWidget(
        shell(
          conn,
          ChangeNotifierProvider.value(
            value: ws,
            child: const WorkspaceScreen(projectId: 'p'),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('label.txt'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'after');
      await tester.pump();
      await tester.tap(find.byTooltip('Save to backend'));
      await tester.pumpAndSettle();
      expect(saved, true);
      await tester.enterText(find.byType(TextField), 'unsaved buffer');
      await tester.pump();
      await tester.tap(find.byTooltip('Close file'));
      await tester.pumpAndSettle();
      expect(find.text('Unsaved editor changes'), findsOneWidget);
      await tester.tap(find.text('Discard buffer'));
      await tester.pumpAndSettle();
      expect(ws.selectedFilePath, isNull);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      ws.dispose();
      conn.dispose();
    },
  );

  testWidgets('small phone build controls fit with no existing job', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(320, 740));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final conn = connection((request) async {
      if (request.url.path.endsWith('/builds')) {
        return response({'builds': []});
      }
      if (request.url.path.endsWith('/jobs')) return response({'jobs': []});
      return response({});
    });
    final build = BuildController(conn.api);
    await tester.pumpWidget(
      shell(
        conn,
        ChangeNotifierProvider.value(
          value: build,
          child: const BuildScreen(projectId: 'p'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('READY'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    build.dispose();
    conn.dispose();
  });

  testWidgets(
    'audit renders tables on a small phone without remote image requests',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(320, 740));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final conn = connection((request) async {
        if (request.url.queryParameters['format'] == 'markdown') {
          return response({
            'markdown':
                '# NOIR Audit Report\n\n| Property | Value |\n|---|---|\n| SHA-256 | `${'d' * 64}` |\n\n✅ Validated\n\n![omitted image](https://example.invalid/image.png)',
          });
        }
        return response({
          'project': {'id': 'p'},
        });
      });
      await tester.pumpWidget(shell(conn, const AuditScreen(projectId: 'p')));
      await tester.pumpAndSettle();
      expect(find.text('NOIR Audit Report'), findsOneWidget);
      expect(find.text('PASS Validated'), findsOneWidget);
      expect(find.text('omitted image'), findsOneWidget);
      expect(find.byType(Image), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      conn.dispose();
    },
  );

  testWidgets(
    'signing screen loads real records without signing or downloading',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      var mutations = 0;
      final conn = connection((request) async {
        if (request.method != 'GET') mutations++;
        if (request.url.path == '/v1/keys') {
          return response({
            'profiles': [
              {'name': 'local-test', 'type': 'persistent_local'},
            ],
          });
        }
        if (request.url.path.endsWith('/builds')) {
          return response({
            'builds': [
              {
                'build_id': 'build1',
                'workspace_revision': 1,
                'success': true,
                'unsigned_apk_hash': 'c' * 64,
              },
            ],
          });
        }
        return response({'id': 'p', 'workspace_revision': 1});
      });
      await tester.pumpWidget(shell(conn, const SigningScreen(projectId: 'p')));
      await tester.pumpAndSettle();
      final button = tester.widget<NoirPrimaryButton>(
        find.widgetWithText(NoirPrimaryButton, 'SIGN BUILD'),
      );
      expect(
        button.onPressed,
        isNotNull,
      ); // The sole private profile is selected; signing still needs confirmation.
      expect(mutations, 0);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      conn.dispose();
    },
  );
}
