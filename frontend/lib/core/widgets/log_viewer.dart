/// Auto-scrolling terminal-style log panel.
import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';
import '../theme/noir_typography.dart';

class LogViewer extends StatefulWidget {
  const LogViewer({super.key, required this.entries, this.title = 'CONSOLE'});

  final List<String> entries;
  final String title;

  @override
  State<LogViewer> createState() => _LogViewerState();
}

class _LogViewerState extends State<LogViewer> {
  final _scrollController = ScrollController();

  @override
  void didUpdateWidget(LogViewer old) {
    super.didUpdateWidget(old);
    if (widget.entries.length > old.entries.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scrollController.hasClients) {
          _scrollController.animateTo(
            _scrollController.position.maxScrollExtent,
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.03),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.4),
              border: Border(
                bottom: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
              ),
            ),
            child: Row(
              children: [
                Icon(Icons.terminal, size: 14,
                    color: NoirColors.onSurfaceVariant.withValues(alpha: 0.7)),
                const SizedBox(width: 8),
                Text(widget.title,
                    style: NoirTypography.labelCaps
                        .copyWith(color: NoirColors.onSurfaceVariant)),
                const Spacer(),
                _dot(), const SizedBox(width: 4),
                _dot(), const SizedBox(width: 4),
                _dot(active: true),
              ],
            ),
          ),
          // Log entries
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.all(12),
              itemCount: widget.entries.length,
              itemBuilder: (context, i) {
                return Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Text(
                    '> ${widget.entries[i]}',
                    style: NoirTypography.codeSm.copyWith(
                      color: NoirColors.onSurfaceVariant.withValues(alpha: 0.6),
                    ),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }

  Widget _dot({bool active = false}) {
    return Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: active ? NoirColors.primary : NoirColors.outlineVariant,
        boxShadow: active
            ? [BoxShadow(color: Colors.white.withValues(alpha: 0.8), blurRadius: 8)]
            : null,
      ),
    );
  }
}
