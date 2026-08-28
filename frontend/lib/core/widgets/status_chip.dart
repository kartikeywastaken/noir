/// Status chip: [DECODED], [BUILDING], [SIGNED] etc.
library;

import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';
import '../theme/noir_typography.dart';

class StatusChip extends StatelessWidget {
  const StatusChip({super.key, required this.label, this.bright = false});

  final String label;
  final bool bright;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: bright
            ? Colors.white.withValues(alpha: 0.2)
            : Colors.white.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(2),
        border: bright
            ? Border.all(color: Colors.white.withValues(alpha: 0.3))
            : null,
      ),
      child: Text(
        '[${label.toUpperCase()}]',
        style: NoirTypography.labelCaps.copyWith(
          color: NoirColors.primary,
          fontSize: 8,
          letterSpacing: 1.2,
        ),
      ),
    );
  }
}
