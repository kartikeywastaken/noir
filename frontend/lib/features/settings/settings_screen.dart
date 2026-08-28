import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../../core/state/connection_controller.dart';
import '../../core/widgets/noir_button.dart';
import '../../core/widgets/review_layout.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});
  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _url = TextEditingController();
  final _token = TextEditingController();
  bool _busy = false;
  bool _obscure = true;
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
    super.dispose();
  }

  Future<void> _connect() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final connection = context.read<ConnectionController>();
      await connection.configure(_url.text, _token.text);
      if (await connection.testConnection()) _token.clear();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) => Consumer<ConnectionController>(
    builder: (context, connection, _) => ReviewLayout(
      title: 'CONFIGURATION',
      busy: _busy,
      error: _error ?? connection.errorMessage,
      children: [
        Section(
          title: 'BACKEND CONNECTION',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                connection.isConnected
                    ? 'Connected and authenticated'
                    : 'Not authenticated',
              ),
              if (connection.health != null) ...[
                const SizedBox(height: 8),
                Text('Backend v${connection.health!.version}'),
                Text(
                  'Import: ${connection.health!.capabilities.import_} · Build: ${connection.health!.capabilities.build}',
                ),
                Text(
                  'AI configured: ${connection.health!.capabilities.ai} (not a live Gemini check)',
                ),
              ],
            ],
          ),
        ),
        Section(
          title: 'ADDRESS',
          child: TextField(
            controller: _url,
            enabled: !_busy,
            autocorrect: false,
            enableSuggestions: false,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(
              hintText: 'http://127.0.0.1:8787',
            ),
          ),
        ),
        Section(
          title: 'NOIR BEARER TOKEN',
          child: Column(
            children: [
              TextField(
                controller: _token,
                enabled: !_busy,
                obscureText: _obscure,
                autocorrect: false,
                enableSuggestions: false,
                decoration: InputDecoration(
                  hintText: connection.hasToken
                      ? 'Saved securely (leave blank to keep)'
                      : 'Paste backend token',
                  suffixIcon: IconButton(
                    tooltip: 'Toggle visibility',
                    onPressed: () => setState(() => _obscure = !_obscure),
                    icon: Icon(
                      _obscure ? Icons.visibility_off : Icons.visibility,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              const SelectableText(
                'Generate this token with: noir api token\nThis is NOT your Gemini API key. Credentials are stored in the OS keychain/keystore.',
              ),
            ],
          ),
        ),
        Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            NoirPrimaryButton(
              label: 'Save & test connection',
              loading: _busy,
              onPressed: _connect,
            ),
            NoirGhostButton(
              label: 'Forget token',
              onPressed: _busy || !connection.hasToken
                  ? null
                  : () async {
                      try {
                        await connection.clearToken();
                        _token.clear();
                      } catch (_) {
                        if (mounted) {
                          setState(
                            () => _error = 'Could not delete saved credential.',
                          );
                        }
                      }
                    },
            ),
          ],
        ),
        const SizedBox(height: 20),
        const Section(
          title: 'LOCAL-FIRST SETUP',
          child: SelectableText(
            'Start the laptop backend:\nnoir api serve --host 127.0.0.1 --port 8787\n\nPhone over USB:\nadb reverse tcp:8787 tcp:8787\nUse http://127.0.0.1:8787 in this app.\n\nAndroid emulator: http://10.0.2.2:8787\n\nGemini settings stay in backend/.env. Restart the backend after changing them. Apktool and signing run on the laptop, not the phone.',
          ),
        ),
      ],
    ),
  );
}
