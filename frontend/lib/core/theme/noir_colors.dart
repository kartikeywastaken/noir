/// Obsidian Protocol color palette — strictly monochromatic.
///
/// All colors are neutral grayscale. No hue-based tokens.
/// Hierarchy is conveyed through luminosity and opacity.
import 'package:flutter/material.dart';

class NoirColors {
  NoirColors._();

  // ── Base surfaces ────────────────────────────────────────────
  static const Color black = Color(0xFF000000);
  static const Color background = Color(0xFF131313);
  static const Color surface = Color(0xFF131313);
  static const Color surfaceDim = Color(0xFF131313);
  static const Color surfaceBright = Color(0xFF393939);

  // ── Container hierarchy ──────────────────────────────────────
  static const Color surfaceContainerLowest = Color(0xFF0E0E0E);
  static const Color surfaceContainerLow = Color(0xFF1B1B1B);
  static const Color surfaceContainer = Color(0xFF1F1F1F);
  static const Color surfaceContainerHigh = Color(0xFF2A2A2A);
  static const Color surfaceContainerHighest = Color(0xFF353535);

  // ── Primary ──────────────────────────────────────────────────
  static const Color primary = Color(0xFFFFFFFF);
  static const Color onPrimary = Color(0xFF2F3131);
  static const Color primaryContainer = Color(0xFFE2E2E2);
  static const Color onPrimaryContainer = Color(0xFF636565);

  // ── On-surface ───────────────────────────────────────────────
  static const Color onSurface = Color(0xFFE2E2E2);
  static const Color onSurfaceVariant = Color(0xFFC4C7C8);
  static const Color onBackground = Color(0xFFE2E2E2);

  // ── Outline ──────────────────────────────────────────────────
  static const Color outline = Color(0xFF8E9192);
  static const Color outlineVariant = Color(0xFF444748);

  // ── Surface tint & variant ───────────────────────────────────
  static const Color surfaceTint = Color(0xFFC6C6C7);
  static const Color surfaceVariant = Color(0xFF353535);

  // ── Inverse ──────────────────────────────────────────────────
  static const Color inverseSurface = Color(0xFFE2E2E2);
  static const Color inverseOnSurface = Color(0xFF303030);
  static const Color inversePrimary = Color(0xFF5D5F5F);

  // ── Glass panels ─────────────────────────────────────────────
  static Color glassBackground = Colors.white.withValues(alpha: 0.03);
  static Color glassHighlight = Colors.white.withValues(alpha: 0.05);
  static Color glassBorder = Colors.white.withValues(alpha: 0.1);
  static Color glassBorderHighlight = Colors.white.withValues(alpha: 0.4);

  // ── Glow effects ─────────────────────────────────────────────
  static Color glowWhite = Colors.white.withValues(alpha: 0.3);
  static Color glowStrong = Colors.white.withValues(alpha: 0.8);

  // ── Semantic (grayscale only per design spec) ────────────────
  static const Color error = Color(0xFFAAAAAA);
  static const Color warning = Color(0xFF999999);
  static const Color success = Color(0xFFCCCCCC);
}
