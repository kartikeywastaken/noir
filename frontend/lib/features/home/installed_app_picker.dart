import 'package:flutter/material.dart';
import '../../core/theme/noir_colors.dart';
import '../../data/device/installed_apps_service.dart';

Future<InstalledAppInfo?> showInstalledAppPicker(
  BuildContext context,
  InstalledAppsService service,
) => showModalBottomSheet<InstalledAppInfo>(
  context: context,
  isScrollControlled: true,
  useSafeArea: true,
  backgroundColor: NoirColors.surfaceContainerLow,
  builder: (_) => FractionallySizedBox(
    heightFactor: 0.92,
    child: _InstalledAppPicker(service: service),
  ),
);

class _InstalledAppPicker extends StatefulWidget {
  const _InstalledAppPicker({required this.service});
  final InstalledAppsService service;

  @override
  State<_InstalledAppPicker> createState() => _InstalledAppPickerState();
}

class _InstalledAppPickerState extends State<_InstalledAppPicker> {
  final _search = TextEditingController();
  List<InstalledAppInfo> _apps = const [];
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final apps = await widget.service.listInstalledApps();
      if (mounted) setState(() => _apps = apps);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final query = _search.text.trim().toLowerCase();
    final visible = query.isEmpty
        ? _apps
        : _apps
              .where(
                (app) =>
                    app.name.toLowerCase().contains(query) ||
                    app.packageName.toLowerCase().contains(query),
              )
              .toList(growable: false);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 12, 8),
          child: Row(
            children: [
              const Expanded(
                child: Text(
                  'CHOOSE AN INSTALLED APP',
                  style: TextStyle(
                    fontWeight: FontWeight.bold,
                    letterSpacing: 1.4,
                  ),
                ),
              ),
              IconButton(
                tooltip: 'Close',
                onPressed: () => Navigator.pop(context),
                icon: const Icon(Icons.close),
              ),
            ],
          ),
        ),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 20),
          child: Text(
            'NOIR copies only the app’s public APK. It cannot read accounts, saved games, private files or server data. The rebuilt APK uses a different signature and cannot update the original app.',
            style: TextStyle(color: NoirColors.onSurfaceVariant, fontSize: 12),
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(20),
          child: TextField(
            controller: _search,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: 'Search apps or package names',
            ),
          ),
        ),
        if (_loading) const LinearProgressIndicator(),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              children: [
                Text(_error!, textAlign: TextAlign.center),
                TextButton(onPressed: _load, child: const Text('Retry')),
              ],
            ),
          ),
        if (!_loading && _error == null && visible.isEmpty)
          const Padding(
            padding: EdgeInsets.all(20),
            child: Text('No matching launcher applications found.'),
          ),
        if (_error == null)
          Expanded(
            child: ListView.separated(
              itemCount: visible.length,
              separatorBuilder: (_, _) => const Divider(),
              itemBuilder: (context, index) {
                final app = visible[index];
                return ListTile(
                  enabled: !app.isSplit,
                  leading: SizedBox.square(
                    dimension: 44,
                    child: app.icon == null
                        ? const Icon(Icons.android)
                        : Image.memory(
                            app.icon!,
                            fit: BoxFit.contain,
                            errorBuilder: (_, _, _) =>
                                const Icon(Icons.android),
                          ),
                  ),
                  title: Text(app.name),
                  subtitle: Text(
                    [
                      app.packageName,
                      if (app.versionName.isNotEmpty) app.versionName,
                    ].join(' · '),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  trailing: app.isSplit
                      ? const Tooltip(
                          message:
                              'Split APK applications are not supported yet.',
                          child: Text(
                            'SPLIT',
                            style: TextStyle(fontSize: 10, letterSpacing: 1.2),
                          ),
                        )
                      : const Icon(Icons.chevron_right),
                  onTap: app.isSplit ? null : () => Navigator.pop(context, app),
                );
              },
            ),
          ),
      ],
    );
  }
}
