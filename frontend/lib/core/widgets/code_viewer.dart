/// Read-only code viewer with line numbers and monospace font.
import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';
import '../theme/noir_typography.dart';

class CodeViewer extends StatelessWidget {
  const CodeViewer({
    super.key,
    required this.content,
    this.language,
    this.startLine = 1,
  });

  final String content;
  final String? language;
  final int startLine;

  @override
  Widget build(BuildContext context) {
    final lines = content.split('\n');
    final lineNumberWidth = '${startLine + lines.length}'.length * 10.0 + 16;

    return LayoutBuilder(
      builder: (context, constraints) {
        return SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: ConstrainedBox(
            constraints: BoxConstraints(minWidth: constraints.maxWidth),
            child: SingleChildScrollView(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Line numbers
                  Container(
                    width: lineNumberWidth,
                    padding: const EdgeInsets.symmetric(vertical: 16),
                    decoration: BoxDecoration(
                      color: Colors.black.withValues(alpha: 0.2),
                      border: Border(
                        right: BorderSide(
                          color: Colors.white.withValues(alpha: 0.05),
                        ),
                      ),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: List.generate(lines.length, (i) {
                        return Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: Text(
                            '${startLine + i}',
                            style: NoirTypography.codeSm.copyWith(
                              color: NoirColors.onSurfaceVariant.withValues(alpha: 0.3),
                            ),
                          ),
                        );
                      }),
                    ),
                  ),
                  // Code content
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: SelectableText(
                        content,
                        style: NoirTypography.codeLg.copyWith(
                          color: NoirColors.onSurfaceVariant,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
