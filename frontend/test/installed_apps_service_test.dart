import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:noir_app/data/device/installed_apps_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('noir.test/installed_apps');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  setUp(() {
    debugDefaultTargetPlatformOverride = TargetPlatform.android;
  });

  tearDown(() {
    debugDefaultTargetPlatformOverride = null;
    messenger.setMockMethodCallHandler(channel, null);
  });

  test('parses launcher apps and extracted standalone APK metadata', () async {
    messenger.setMockMethodCallHandler(channel, (call) async {
      if (call.method == 'listInstalledApps') {
        return [
          {
            'name': 'Chess',
            'packageName': 'example.chess',
            'versionName': '1.2.3',
            'versionCode': 12,
            'isSplit': false,
            'icon': Uint8List.fromList([1, 2, 3]),
          },
        ];
      }
      expect(call.method, 'extractInstalledApp');
      expect(call.arguments, {'packageName': 'example.chess'});
      return {
        'path': '/private/cache/example.chess.apk',
        'filename': 'example.chess.apk',
        'length': 4096,
        'packageName': 'example.chess',
      };
    });

    const service = InstalledAppsService(channel: channel);
    final apps = await service.listInstalledApps();
    expect(apps.single.name, 'Chess');
    expect(apps.single.icon, Uint8List.fromList([1, 2, 3]));
    final extracted = await service.extract(apps.single);
    expect(extracted.filename, 'example.chess.apk');
    expect(extracted.length, 4096);
  });

  test('rejects split installs before invoking Android extraction', () async {
    var calls = 0;
    messenger.setMockMethodCallHandler(channel, (_) async {
      calls++;
      return null;
    });
    const service = InstalledAppsService(channel: channel);
    const split = InstalledAppInfo(
      name: 'Split game',
      packageName: 'example.split',
      versionName: '',
      versionCode: 1,
      isSplit: true,
      icon: null,
    );
    await expectLater(
      service.extract(split),
      throwsA(
        isA<InstalledAppsException>().having(
          (error) => error.message,
          'message',
          contains('split APKs'),
        ),
      ),
    );
    expect(calls, 0);
  });

  test('does not claim installed-app support on non-Android platforms', () {
    debugDefaultTargetPlatformOverride = TargetPlatform.macOS;
    const service = InstalledAppsService(channel: channel);
    expect(service.isSupported, false);
  });
}
