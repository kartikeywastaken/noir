import 'package:flutter/material.dart';
import '../theme/noir_typography.dart';

/// Selectable, scrollable full text. Never truncate a review diff.
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
  Widget build(BuildContext context) => SingleChildScrollView(
    padding: const EdgeInsets.all(16),
    child: SelectableText(content, style: NoirTypography.codeSm),
  );
}
