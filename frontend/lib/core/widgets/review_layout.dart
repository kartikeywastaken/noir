import 'package:flutter/material.dart';
import '../theme/noir_typography.dart';
import 'glass_panel.dart';
import 'mesh_gradient_background.dart';
import 'noir_app_bar.dart';

class ReviewLayout extends StatelessWidget {
  const ReviewLayout({
    super.key,
    required this.title,
    required this.children,
    this.busy = false,
    this.error,
    this.onRefresh,
    this.bottomNavigationBar,
  });
  final String title;
  final List<Widget> children;
  final bool busy;
  final String? error;
  final VoidCallback? onRefresh;
  final Widget? bottomNavigationBar;
  @override
  Widget build(BuildContext context) => Scaffold(
    bottomNavigationBar: bottomNavigationBar,
    appBar: NoirAppBar(
      title: title,
      showBackButton: true,
      actions: [
        if (onRefresh != null)
          IconButton(
            tooltip: 'Refresh',
            onPressed: busy ? null : onRefresh,
            icon: const Icon(Icons.refresh),
          ),
      ],
    ),
    body: MeshGradientBackground(
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1000),
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              if (busy)
                const Padding(
                  padding: EdgeInsets.only(bottom: 16),
                  child: LinearProgressIndicator(),
                ),
              if (error != null)
                Section(title: 'ATTENTION', child: SelectableText(error!)),
              ...children,
            ],
          ),
        ),
      ),
    ),
  );
}

class Section extends StatelessWidget {
  const Section({super.key, required this.title, required this.child});
  final String title;
  final Widget child;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 16),
    child: GlassPanel(
      padding: const EdgeInsets.all(18),
      child: Material(
        color: Colors.transparent,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(title, style: NoirTypography.labelCaps),
            const SizedBox(height: 12),
            child,
          ],
        ),
      ),
    ),
  );
}

Future<bool> confirmAction(
  BuildContext context,
  String title,
  String message,
  String action,
) async =>
    await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(title),
        content: SingleChildScrollView(child: SelectableText(message)),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: Text(action),
          ),
        ],
      ),
    ) ??
    false;
