/// Decorative L-shaped corner markers from the home screen hero panel.
import 'package:flutter/material.dart';

class CornerMarkers extends StatelessWidget {
  const CornerMarkers({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        child,
        Positioned(top: 16, left: 16, child: _corner(true, true)),
        Positioned(top: 16, right: 16, child: _corner(true, false)),
        Positioned(bottom: 16, left: 16, child: _corner(false, true)),
        Positioned(bottom: 16, right: 16, child: _corner(false, false)),
      ],
    );
  }

  Widget _corner(bool top, bool left) {
    return Container(
      width: 16,
      height: 16,
      decoration: BoxDecoration(
        border: Border(
          top: top
              ? BorderSide(color: Colors.white.withValues(alpha: 0.3))
              : BorderSide.none,
          bottom: !top
              ? BorderSide(color: Colors.white.withValues(alpha: 0.3))
              : BorderSide.none,
          left: left
              ? BorderSide(color: Colors.white.withValues(alpha: 0.3))
              : BorderSide.none,
          right: !left
              ? BorderSide(color: Colors.white.withValues(alpha: 0.3))
              : BorderSide.none,
        ),
      ),
    );
  }
}
