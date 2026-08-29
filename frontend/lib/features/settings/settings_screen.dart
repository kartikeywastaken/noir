import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:go_router/go_router.dart';
import '../../core/state/connection_controller.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';
import '../../core/widgets/noir_bottom_nav.dart';
import '../../data/api/noir_api_client.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _url = TextEditingController();
  final _token = TextEditingController();
  final _invite = TextEditingController();
  bool _busy = false;
  String? _error;
  @override
  void initState() {
    super.initState();
    final connection = context.read<ConnectionController>();
    _url.text = connection.baseUrl;
    connection.ready.then((_) {
      if (mounted && !_busy) setState(() => _url.text = connection.baseUrl);
    });
  }

  @override
  void dispose() {
    _url.dispose();
    _token.dispose();
    _invite.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function(ConnectionController) action) async {
    final connection = context.read<ConnectionController>();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action(connection);
    } catch (error) {
      if (mounted) setState(() => _error = error.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) => Consumer<ConnectionController>(
    builder: (context, connection, _) => ReviewLayout(
      title: 'YOUR WORKSPACE',
      bottomNavigationBar: NoirBottomNav(
        currentIndex: 2,
        onTap: (i) {
          if (i == 0) context.go('/');
          if (i == 1) context.go('/history');
        },
      ),
      busy: _busy,
      error: _error ?? connection.errorMessage,
      children: [
        Section(
          title: 'NOIR CLOUD',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                connection.isConnected
                    ? 'Connected · ${connection.workspaceName}'
                    : 'Your own private APK workspace',
              ),
              const SizedBox(height: 8),
              const Text(
                'Your APKs, build history and signing keys are private. NOIR reconnects automatically after activation.',
              ),
              if (connection.isConnected) ...[
                const SizedBox(height: 8),
                Text('Backend v${connection.health?.version ?? ""}'),
                Text(
                  'AI configured: ${connection.health?.capabilities.ai ?? false}',
                ),
              ],
            ],
          ),
        ),
        if (!connection.hasToken)
          Section(
            title: 'ACTIVATE INVITATION',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Paste the one-time code the app owner gave you. No server address or Gemini key is needed.',
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _invite,
                  enabled: !_busy,
                  obscureText: false,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: const InputDecoration(
                    labelText: 'Invitation code',
                  ),
                ),
                const SizedBox(height: 16),
                NoirPrimaryButton(
                  label: 'Activate workspace',
                  loading: _busy,
                  onPressed: () => _run((connection) async {
                    if (_invite.text.trim().isEmpty) {
                      throw Exception('Enter your invitation code.');
                    }
                    await connection.activateInvite(_invite.text.trim());
                    if (mounted) _invite.clear();
                  }),
                ),
                const SizedBox(height: 12),
                const Text(
                  'Codes are single-use. For another device or after signing out, ask the owner for a new code for your existing workspace.',
                ),
              ],
            ),
          ),
        if (connection.hasToken)
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              NoirPrimaryButton(
                label: 'Test connection',
                loading: _busy,
                onPressed: () => _run((connection) async {
                  await connection.testConnection();
                }),
              ),
              NoirGhostButton(
                label: 'Sign out',
                onPressed: _busy
                    ? null
                    : () async {
                        if (!await confirmAction(
                              context,
                              'Sign out?',
                              'This revokes this device’s session. Your builds stay private on the server. You will need a new invitation for the same workspace to sign in again.',
                              'Sign out',
                            ) ||
                            !mounted) {
                          return;
                        }
                        await _run((connection) => connection.signOut());
                      },
              ),
            ],
          ),
        const SizedBox(height: 20),
        Section(
          title: 'ADVANCED',
          child: ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: const Text('Custom backend / owner access'),
            subtitle: const Text('Not needed for invited users'),
            children: [
              TextField(
                controller: _url,
                enabled: !_busy,
                autocorrect: false,
                enableSuggestions: false,
                keyboardType: TextInputType.url,
                decoration: const InputDecoration(
                  labelText: 'Backend URL',
                  hintText: NoirApiClient.cloudBaseUrl,
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _token,
                enabled: !_busy,
                obscureText: true,
                autocorrect: false,
                enableSuggestions: false,
                decoration: InputDecoration(
                  labelText: 'NOIR BEARER TOKEN',
                  hintText: connection.hasToken
                      ? 'Leave blank to retain this session'
                      : 'Owner or custom-server token',
                ),
              ),
              const SizedBox(height: 16),
              NoirPrimaryButton(
                label: 'Save & test connection',
                loading: _busy,
                onPressed: () => _run((connection) async {
                  await connection.configure(_url.text, _token.text);
                  await connection.testConnection();
                  if (mounted) _token.clear();
                }),
              ),
              const SizedBox(height: 12),
              const Text(
                'Never share an owner token. Gemini and signing credentials stay on the server. Changing the address requires an explicit token for that server.',
              ),
            ],
          ),
        ),
      ],
    ),
  );
}
