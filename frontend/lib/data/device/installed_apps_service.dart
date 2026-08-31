import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

class InstalledAppInfo {
  const InstalledAppInfo({
    required this.name,
    required this.packageName,
    required this.versionName,
    required this.versionCode,
    required this.isSplit,
    required this.icon,
  });

  final String name;
  final String packageName;
  final String versionName;
  final int versionCode;
  final bool isSplit;
  final Uint8List? icon;

  factory InstalledAppInfo.fromMap(Map<Object?, Object?> value) =>
      InstalledAppInfo(
        name: '${value['name'] ?? ''}',
        packageName: '${value['packageName'] ?? ''}',
        versionName: '${value['versionName'] ?? ''}',
        versionCode: (value['versionCode'] as num?)?.toInt() ?? 0,
        isSplit: value['isSplit'] == true,
        icon: value['icon'] as Uint8List?,
      );
}

class ExtractedInstalledApk {
  const ExtractedInstalledApk({
    required this.path,
    required this.filename,
    required this.length,
    required this.packageName,
  });

  final String path;
  final String filename;
  final int length;
  final String packageName;

  factory ExtractedInstalledApk.fromMap(Map<Object?, Object?> value) =>
      ExtractedInstalledApk(
        path: '${value['path'] ?? ''}',
        filename: '${value['filename'] ?? 'installed.apk'}',
        length: (value['length'] as num?)?.toInt() ?? 0,
        packageName: '${value['packageName'] ?? ''}',
      );
}

class InstalledAppsException implements Exception {
  const InstalledAppsException(this.message);
  final String message;
  @override
  String toString() => message;
}

class InstalledAppsService {
  const InstalledAppsService({MethodChannel? channel})
    : _channel =
          channel ?? const MethodChannel('app.noir.noir_app/installed_apps');

  final MethodChannel _channel;

  bool get isSupported =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  Future<List<InstalledAppInfo>> listInstalledApps() async {
    if (!isSupported) {
      throw const InstalledAppsException(
        'Installed-app selection is available on Android only.',
      );
    }
    try {
      final values = await _channel.invokeListMethod<Object?>(
        'listInstalledApps',
      );
      return (values ?? const [])
          .whereType<Map<Object?, Object?>>()
          .map(InstalledAppInfo.fromMap)
          .where((app) => app.packageName.isNotEmpty)
          .toList(growable: false);
    } on PlatformException catch (error) {
      throw InstalledAppsException(
        error.message ?? 'Android could not list installed applications.',
      );
    } on MissingPluginException {
      throw const InstalledAppsException(
        'Reinstall the latest Android build of NOIR to use installed apps.',
      );
    }
  }

  Future<ExtractedInstalledApk> extract(InstalledAppInfo app) async {
    if (app.isSplit) {
      throw const InstalledAppsException(
        'This application uses split APKs. NOIR cannot safely rebuild it yet.',
      );
    }
    try {
      final value = await _channel.invokeMapMethod<Object?, Object?>(
        'extractInstalledApp',
        {'packageName': app.packageName},
      );
      if (value == null) {
        throw const InstalledAppsException(
          'Android returned no APK for this application.',
        );
      }
      final extracted = ExtractedInstalledApk.fromMap(value);
      if (extracted.path.isEmpty || extracted.length <= 0) {
        throw const InstalledAppsException(
          'The extracted APK was empty or incomplete.',
        );
      }
      return extracted;
    } on PlatformException catch (error) {
      throw InstalledAppsException(
        error.message ?? 'Android could not prepare this installed app.',
      );
    } on MissingPluginException {
      throw const InstalledAppsException(
        'Reinstall the latest Android build of NOIR to use installed apps.',
      );
    }
  }
}
