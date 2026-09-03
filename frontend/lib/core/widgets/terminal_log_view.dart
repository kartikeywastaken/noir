import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';

/// Interactive terminal log view mimicking the macOS-style console in Screenshot 4.
class TerminalLogView extends StatelessWidget {
  const TerminalLogView({
    super.key,
    required this.lines,
    this.title = 'noir_build_log_session.log',
    this.height = 280,
  });

  final List<String> lines;
  final String title;
  final double height;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      decoration: BoxDecoration(
        color: NoirColors.surfaceContainerLowest,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: NoirColors.surfaceContainerHighest),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.5),
            blurRadius: 16,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header with traffic light dots
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            decoration: const BoxDecoration(
              color: NoirColors.surfaceContainer,
              border: Border(
                bottom: BorderSide(color: NoirColors.surfaceContainerHighest),
              ),
            ),
            child: Row(
              children: [
                Container(
                  width: 10,
                  height: 10,
                  decoration: const BoxDecoration(
                    color: Color(0xFFFF5252),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Container(
                  width: 10,
                  height: 10,
                  decoration: const BoxDecoration(
                    color: Color(0xFF444749),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Container(
                  width: 10,
                  height: 10,
                  decoration: const BoxDecoration(
                    color: NoirColors.primaryFixed,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Text(
                    title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontFamily: 'JetBrainsMono',
                      fontSize: 11,
                      color: NoirColors.onSurfaceVariant,
                    ),
                  ),
                ),
                const Icon(
                  Icons.filter_list,
                  size: 16,
                  color: NoirColors.onSurfaceVariant,
                ),
              ],
            ),
          ),
          // Console output
          Expanded(
            child: lines.isEmpty
                ? const Center(
                    child: Text(
                      '> Awaiting build events...',
                      style: TextStyle(
                        fontFamily: 'JetBrainsMono',
                        fontSize: 12,
                        color: NoirColors.outline,
                      ),
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.all(12),
                    itemCount: lines.length,
                    itemBuilder: (context, index) {
                      final line = lines[index];
                      return _buildLogLine(line, index);
                    },
                  ),
          ),
          // Footer prompt
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            decoration: const BoxDecoration(
              border: Border(
                top: BorderSide(color: Color(0xFF1F1F1F)),
              ),
            ),
            child: const Row(
              children: [
                Text(
                  '> ',
                  style: TextStyle(
                    fontFamily: 'JetBrainsMono',
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    color: NoirColors.primaryFixed,
                  ),
                ),
                Expanded(
                  child: Text(
                    'Streaming live operations...',
                    style: TextStyle(
                      fontFamily: 'JetBrainsMono',
                      fontSize: 11,
                      color: NoirColors.onSurfaceVariant,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildLogLine(String text, int index) {
    // Generate simulated timestamp or extract if present
    final timeStr = '00:00:${(index + 1).toString().padLeft(2, '0')}';

    Color textColor = NoirColors.onSurface;
    Color? badgeColor;
    String? badgeText;

    if (text.contains('[NOIR]')) {
      badgeColor = NoirColors.primaryFixed;
      badgeText = '[NOIR]';
    } else if (text.contains('[APKTOOL]')) {
      badgeColor = NoirColors.tertiaryFixed;
      badgeText = '[APKTOOL]';
    } else if (text.contains('[WARN]') || text.toLowerCase().contains('warn')) {
      badgeColor = NoirColors.errorBright;
      badgeText = '[WARN]';
    } else if (text.contains('[SYS]')) {
      badgeColor = Colors.white;
      badgeText = '[SYS]';
    }

    final cleanText = badgeText != null ? text.replaceAll(badgeText, '').trim() : text;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2.5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 58,
            child: Text(
              timeStr,
              style: const TextStyle(
                fontFamily: 'JetBrainsMono',
                fontSize: 11,
                color: Color(0xFF555555),
              ),
            ),
          ),
          if (badgeText != null) ...[
            Text(
              '$badgeText ',
              style: TextStyle(
                fontFamily: 'JetBrainsMono',
                fontSize: 11,
                fontWeight: FontWeight.bold,
                color: badgeColor,
              ),
            ),
          ],
          Expanded(
            child: Text(
              cleanText,
              style: TextStyle(
                fontFamily: 'JetBrainsMono',
                fontSize: 11,
                color: textColor,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
