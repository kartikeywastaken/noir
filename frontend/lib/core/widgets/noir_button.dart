/// Material Design 3 Electric Lime primary and ghost buttons with stadium shape.
library;

import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';

/// Electric Lime stadium button with dark text — primary actions.
class NoirPrimaryButton extends StatelessWidget {
  const NoirPrimaryButton({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
    this.loading = false,
    this.expand = false,
  });

  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool loading;
  final bool expand;

  @override
  Widget build(BuildContext context) {
    final isEnabled = onPressed != null && !loading;

    return AnimatedContainer(
      duration: const Duration(milliseconds: 200),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(9999),
        boxShadow: isEnabled
            ? [
                BoxShadow(
                  color: NoirColors.primaryFixed.withValues(alpha: 0.25),
                  blurRadius: 18,
                  offset: const Offset(0, 4),
                ),
              ]
            : null,
      ),
      child: SizedBox(
        width: expand ? double.infinity : null,
        child: ElevatedButton(
          onPressed: loading ? null : onPressed,
          style: ElevatedButton.styleFrom(
            backgroundColor: NoirColors.primaryFixed,
            foregroundColor: NoirColors.onPrimaryFixed,
            disabledBackgroundColor: NoirColors.surfaceContainerHigh,
            disabledForegroundColor: NoirColors.onSurfaceVariant.withValues(alpha: 0.4),
            shape: const StadiumBorder(),
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
            elevation: 0,
          ),
          child: Row(
            mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              if (loading) ...[
                const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2.2,
                    color: NoirColors.onPrimaryFixed,
                  ),
                ),
                const SizedBox(width: 10),
              ] else if (icon != null) ...[
                Icon(icon, size: 20, color: isEnabled ? NoirColors.onPrimaryFixed : null),
                const SizedBox(width: 8),
              ],
              Text(
                label.toUpperCase(),
                style: const TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.8,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Outlined stadium button with translucent hover background — secondary actions.
class NoirGhostButton extends StatelessWidget {
  const NoirGhostButton({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
    this.loading = false,
    this.expand = false,
  });

  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool loading;
  final bool expand;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: expand ? double.infinity : null,
      child: OutlinedButton(
        onPressed: loading ? null : onPressed,
        style: OutlinedButton.styleFrom(
          foregroundColor: NoirColors.onSurface,
          side: const BorderSide(color: NoirColors.outlineVariant),
          shape: const StadiumBorder(),
          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
        ),
        child: Row(
          mainAxisSize: expand ? MainAxisSize.max : MainAxisSize.min,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (loading) ...[
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: NoirColors.onSurface,
                ),
              ),
              const SizedBox(width: 8),
            ] else if (icon != null) ...[
              Icon(icon, size: 18, color: NoirColors.onSurface),
              const SizedBox(width: 8),
            ],
            Text(
              label.toUpperCase(),
              style: const TextStyle(
                fontFamily: 'Inter',
                fontSize: 12,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.8,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
