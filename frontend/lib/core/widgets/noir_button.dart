/// Primary and ghost button variants with glow effects.
library;

import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';
import '../theme/noir_typography.dart';

/// Solid white button with black text — primary actions.
class NoirPrimaryButton extends StatelessWidget {
  const NoirPrimaryButton({
    super.key,
    required this.label,
    this.icon,
    this.onPressed,
    this.loading = false,
  });

  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool loading;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      decoration: BoxDecoration(
        boxShadow: onPressed != null
            ? [
                BoxShadow(
                  color: Colors.white.withValues(alpha: 0.15),
                  blurRadius: 20,
                ),
              ]
            : null,
      ),
      child: ElevatedButton(
        onPressed: loading ? null : onPressed,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (loading)
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: NoirColors.black,
                ),
              )
            else if (icon != null)
              Icon(icon, size: 20),
            if (icon != null || loading) const SizedBox(width: 8),
            Text(
              label.toUpperCase(),
              style: NoirTypography.codeSm.copyWith(
                letterSpacing: 1.6,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Transparent button with thin white border — secondary actions.
class NoirGhostButton extends StatefulWidget {
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
  State<NoirGhostButton> createState() => _NoirGhostButtonState();
}

class _NoirGhostButtonState extends State<NoirGhostButton> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 300),
        decoration: BoxDecoration(
          border: Border.all(
            color: _hovered
                ? Colors.white.withValues(alpha: 0.6)
                : Colors.white.withValues(alpha: 0.3),
          ),
          borderRadius: BorderRadius.circular(2),
          color: _hovered
              ? Colors.white.withValues(alpha: 0.05)
              : Colors.transparent,
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: widget.loading ? null : widget.onPressed,
            borderRadius: BorderRadius.circular(2),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
              child: Row(
                mainAxisSize: widget.expand
                    ? MainAxisSize.max
                    : MainAxisSize.min,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (widget.loading)
                    const SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(
                        strokeWidth: 1.5,
                        color: NoirColors.primary,
                      ),
                    )
                  else if (widget.icon != null)
                    Icon(widget.icon, size: 18, color: NoirColors.primary),
                  if (widget.icon != null || widget.loading)
                    const SizedBox(width: 8),
                  Text(
                    widget.label.toUpperCase(),
                    style: NoirTypography.codeSm.copyWith(
                      color: NoirColors.primary,
                      letterSpacing: 1.6,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
