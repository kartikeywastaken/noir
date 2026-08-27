/// Ambient mesh gradient background layer.
import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';

class MeshGradientBackground extends StatelessWidget {
  const MeshGradientBackground({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        Positioned.fill(
          child: Container(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                center: const Alignment(-0.2, -0.6),
                radius: 1.2,
                colors: [
                  Colors.white.withValues(alpha: 0.04),
                  Colors.transparent,
                ],
              ),
            ),
          ),
        ),
        Positioned.fill(
          child: Container(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                center: const Alignment(0.6, -1.0),
                radius: 1.0,
                colors: [
                  Colors.white.withValues(alpha: 0.02),
                  Colors.transparent,
                ],
              ),
            ),
          ),
        ),
        Positioned.fill(
          child: Container(color: NoirColors.surface.withValues(alpha: 0.6)),
        ),
        child,
      ],
    );
  }
}
