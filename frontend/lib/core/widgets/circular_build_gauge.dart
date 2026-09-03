import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../theme/noir_colors.dart';

/// Circular progress ring matching the NOIR Material 3 rebuild screen.
///
/// Displays an animated circular arc with percentage and stage label.
class CircularBuildGauge extends StatelessWidget {
  const CircularBuildGauge({
    super.key,
    required this.progress,
    required this.stageLabel,
    this.size = 180,
    this.strokeWidth = 10,
  });

  /// Value from 0.0 to 1.0.
  final double progress;
  final String stageLabel;
  final double size;
  final double strokeWidth;

  @override
  Widget build(BuildContext context) {
    final percent = (progress.clamp(0.0, 1.0) * 100).toInt();

    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: NoirColors.surfaceContainerHigh,
        border: Border.all(
          color: NoirColors.surfaceContainerHighest,
          width: 4,
        ),
        boxShadow: [
          BoxShadow(
            color: NoirColors.primaryFixed.withValues(alpha: 0.15),
            blurRadius: 24,
            spreadRadius: 2,
          ),
        ],
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          CustomPaint(
            size: Size(size, size),
            painter: _GaugePainter(
              progress: progress.clamp(0.0, 1.0),
              strokeWidth: strokeWidth,
              trackColor: NoirColors.surfaceContainerHighest,
              progressColor: NoirColors.primaryFixed,
            ),
          ),
          Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '$percent',
                    style: const TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 48,
                      fontWeight: FontWeight.w700,
                      color: NoirColors.primaryFixed,
                      height: 1.0,
                    ),
                  ),
                  const Text(
                    '%',
                    style: TextStyle(
                      fontFamily: 'Inter',
                      fontSize: 20,
                      fontWeight: FontWeight.w600,
                      color: NoirColors.primaryFixed,
                      height: 1.3,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                stageLabel.toUpperCase(),
                style: const TextStyle(
                  fontFamily: 'Inter',
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 2.0,
                  color: NoirColors.onSurfaceVariant,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _GaugePainter extends CustomPainter {
  _GaugePainter({
    required this.progress,
    required this.strokeWidth,
    required this.trackColor,
    required this.progressColor,
  });

  final double progress;
  final double strokeWidth;
  final Color trackColor;
  final Color progressColor;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.width - strokeWidth * 2) / 2;

    // Background track circle
    final trackPaint = Paint()
      ..color = trackColor
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..strokeCap = StrokeCap.round;

    canvas.drawCircle(center, radius, trackPaint);

    if (progress > 0) {
      // Progress arc starting from top (-90 degrees)
      final progressPaint = Paint()
        ..color = progressColor
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeCap = StrokeCap.round;

      const startAngle = -math.pi / 2;
      final sweepAngle = 2 * math.pi * progress;

      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        startAngle,
        sweepAngle,
        false,
        progressPaint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _GaugePainter oldDelegate) {
    return oldDelegate.progress != progress ||
        oldDelegate.trackColor != trackColor ||
        oldDelegate.progressColor != progressColor;
  }
}
