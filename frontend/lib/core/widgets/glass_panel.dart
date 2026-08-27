/// Glassmorphic panel — the core container of the NOIR UI.
///
/// Translucent background with backdrop blur and thin border.
import 'dart:ui';
import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';

class GlassPanel extends StatelessWidget {
  const GlassPanel({
    super.key,
    required this.child,
    this.highlight = false,
    this.padding,
    this.borderRadius,
    this.onTap,
  });

  final Widget child;
  final bool highlight;
  final EdgeInsetsGeometry? padding;
  final BorderRadius? borderRadius;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final radius = borderRadius ?? BorderRadius.circular(4);
    final bg = highlight ? NoirColors.glassHighlight : NoirColors.glassBackground;
    final border = highlight ? NoirColors.glassBorderHighlight : NoirColors.glassBorder;

    Widget panel = ClipRRect(
      borderRadius: radius,
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
        child: Container(
          decoration: BoxDecoration(
            color: bg,
            borderRadius: radius,
            border: Border.all(color: border, width: 1),
          ),
          padding: padding ?? const EdgeInsets.all(16),
          child: child,
        ),
      ),
    );

    if (onTap != null) {
      panel = MouseRegion(
        cursor: SystemMouseCursors.click,
        child: GestureDetector(onTap: onTap, child: panel),
      );
    }

    return panel;
  }
}
