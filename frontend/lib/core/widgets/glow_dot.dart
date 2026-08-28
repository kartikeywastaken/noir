/// Animated glow dot status indicator.
library;

import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';

class GlowDot extends StatelessWidget {
  const GlowDot({super.key, this.active = true, this.size = 8, this.color});

  final bool active;
  final double size;
  final Color? color;

  @override
  Widget build(BuildContext context) {
    final dotColor = color ?? NoirColors.primary;
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: dotColor,
        shape: BoxShape.circle,
        boxShadow: active
            ? [
                BoxShadow(
                  color: dotColor.withValues(alpha: 0.8),
                  blurRadius: 10,
                ),
              ]
            : null,
      ),
    );
  }
}
