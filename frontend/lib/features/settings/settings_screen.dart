/// Settings / Connection screen.
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/state/connection_controller.dart';
import '../../core/theme/noir_colors.dart';
import '../../core/theme/noir_typography.dart';
import '../../core/widgets/glass_panel.dart';
import '../../core/widgets/glow_dot.dart';
import '../../core/widgets/mesh_gradient_background.dart';
import '../../core/widgets/noir_app_bar.dart';
import '../../core/widgets/noir_button.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _urlController = TextEditingController();
  final _tokenController = TextEditingController();
  bool _obscureToken = true;

  @override
  void initState() {
    super.initState();
    final conn = context.read<ConnectionController>();
    _urlController.text = conn.baseUrl;
  }

  @override
  void dispose() {
    _urlController.dispose();
    _tokenController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<ConnectionController>(
      builder: (context, conn, _) {
        return Scaffold(
          backgroundColor: NoirColors.black,
          appBar: NoirAppBar(
            title: 'CONFIGURATION',
            showBackButton: true,
            actions: const [SizedBox(width: 48)],
          ),
          body: MeshGradientBackground(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 560),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Connection status
                      GlassPanel(
                        padding: const EdgeInsets.all(20),
                        child: Row(
                          children: [
                            GlowDot(
                              active: conn.isConnected,
                              color: conn.isConnected
                                  ? NoirColors.primary
                                  : NoirColors.outlineVariant,
                            ),
                            const SizedBox(width: 12),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  conn.isConnected ? 'CONNECTED' : 'DISCONNECTED',
                                  style: NoirTypography.labelCaps
                                      .copyWith(color: NoirColors.primary),
                                ),
                                if (conn.health != null)
                                  Text(
                                    'v${conn.health!.version}',
                                    style: NoirTypography.codeSm.copyWith(
                                      color: NoirColors.onSurfaceVariant.withValues(alpha: 0.6),
                                    ),
                                  ),
                              ],
                            ),
                            const Spacer(),
                            if (conn.health != null) ...[
                              _capBadge('IMPORT', conn.health!.capabilities.import_),
                              const SizedBox(width: 6),
                              _capBadge('BUILD', conn.health!.capabilities.build),
                              const SizedBox(width: 6),
                              _capBadge('AI', conn.health!.capabilities.ai),
                            ],
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),

                      // Backend URL
                      Text('BACKEND URL',
                          style: NoirTypography.labelCaps
                              .copyWith(color: NoirColors.onSurfaceVariant)),
                      const SizedBox(height: 8),
                      TextField(
                        controller: _urlController,
                        style: NoirTypography.codeSm.copyWith(color: NoirColors.primary),
                        decoration: const InputDecoration(
                          hintText: 'http://127.0.0.1:8787',
                        ),
                        onSubmitted: (v) => conn.setBaseUrl(v),
                      ),
                      const SizedBox(height: 24),

                      // Token
                      Text('BEARER TOKEN',
                          style: NoirTypography.labelCaps
                              .copyWith(color: NoirColors.onSurfaceVariant)),
                      const SizedBox(height: 4),
                      Text(
                        'Generated via: noir token create',
                        style: NoirTypography.codeSm.copyWith(
                          color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
                        ),
                      ),
                      const SizedBox(height: 8),
                      TextField(
                        controller: _tokenController,
                        obscureText: _obscureToken,
                        style: NoirTypography.codeSm.copyWith(color: NoirColors.primary),
                        decoration: InputDecoration(
                          hintText: 'Paste token here',
                          suffixIcon: IconButton(
                            icon: Icon(
                              _obscureToken ? Icons.visibility_off : Icons.visibility,
                              color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
                              size: 18,
                            ),
                            onPressed: () => setState(() => _obscureToken = !_obscureToken),
                          ),
                        ),
                      ),
                      const SizedBox(height: 24),

                      // Error
                      if (conn.errorMessage != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 16),
                          child: GlassPanel(
                            padding: const EdgeInsets.all(12),
                            child: Row(
                              children: [
                                const Icon(Icons.error_outline,
                                    color: NoirColors.error, size: 16),
                                const SizedBox(width: 8),
                                Expanded(
                                  child: Text(
                                    conn.errorMessage!,
                                    style: NoirTypography.codeSm
                                        .copyWith(color: NoirColors.error),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),

                      // Actions
                      Row(
                        children: [
                          Expanded(
                            child: NoirPrimaryButton(
                              label: 'Test Connection',
                              icon: Icons.sensors,
                              loading: conn.state == ConnectionState.connecting,
                              onPressed: () async {
                                await conn.setBaseUrl(_urlController.text);
                                if (_tokenController.text.isNotEmpty) {
                                  await conn.setToken(_tokenController.text);
                                }
                                await conn.testConnection();
                              },
                            ),
                          ),
                          if (conn.isConnected) ...[
                            const SizedBox(width: 12),
                            NoirGhostButton(
                              label: 'Clear',
                              icon: Icons.close,
                              onPressed: () {
                                _tokenController.clear();
                                conn.clearToken();
                              },
                            ),
                          ],
                        ],
                      ),
                      const SizedBox(height: 32),

                      // Info
                      GlassPanel(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('NOTE',
                                style: NoirTypography.labelCaps
                                    .copyWith(color: NoirColors.onSurfaceVariant)),
                            const SizedBox(height: 8),
                            Text(
                              'This is the NOIR backend API token, not a Gemini API key. '
                              'Create one with the CLI:\n\n'
                              '  noir token create\n\n'
                              'For Android USB debugging, run:\n\n'
                              '  adb reverse tcp:8787 tcp:8787',
                              style: NoirTypography.codeSm.copyWith(
                                color: NoirColors.onSurfaceVariant.withValues(alpha: 0.7),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _capBadge(String label, bool available) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        border: Border.all(
          color: available
              ? Colors.white.withValues(alpha: 0.3)
              : Colors.white.withValues(alpha: 0.1),
        ),
        borderRadius: BorderRadius.circular(2),
      ),
      child: Text(
        label,
        style: NoirTypography.labelCaps.copyWith(
          color: available
              ? NoirColors.primary
              : NoirColors.onSurfaceVariant.withValues(alpha: 0.3),
          fontSize: 8,
        ),
      ),
    );
  }
}
