/// Material Design 3 Cyberpunk / Electric Lime color palette.
///
/// Tailored for dark mode with high-contrast electric lime accents,
/// rich dark surfaces, and subtle translucent borders.
library;

import 'package:flutter/material.dart';

class NoirColors {
  NoirColors._();

  // ── Electric Lime / Primary Accents ───────────────────────────
  static const Color primaryFixed = Color(0xFFC7F300);
  static const Color onPrimaryFixed = Color(0xFF171E00);
  static const Color onPrimaryFixedVariant = Color(0xFF3D4D00);
  static const Color primaryFixedDim = Color(0xFFAED500);
  static const Color primaryContainer = Color(0xFFC7F300);
  static const Color onPrimaryContainer = Color(0xFF171E00);
  static const Color primary = Color(0xFFFFFFFF);
  static const Color onPrimary = Color(0xFF293500);
  static const Color surfaceTint = Color(0xFFAED500);

  // ── Cyan / Tertiary Accents ──────────────────────────────────
  static const Color tertiaryFixed = Color(0xFFA5EEFF);
  static const Color tertiaryFixedDim = Color(0xFF00DAF8);
  static const Color tertiaryContainer = Color(0xFFA5EEFF);
  static const Color onTertiaryContainer = Color(0xFF006F7F);
  static const Color onTertiaryFixedVariant = Color(0xFF004E5A);
  static const Color tertiary = Color(0xFFFFFFFF);

  // ── Base Surfaces ────────────────────────────────────────────
  static const Color black = Color(0xFF000000);
  static const Color background = Color(0xFF131313);
  static const Color surface = Color(0xFF131313);
  static const Color surfaceDim = Color(0xFF131313);
  static const Color surfaceBright = Color(0xFF3A3939);

  // ── Surface Container Hierarchy ──────────────────────────────
  static const Color surfaceContainerLowest = Color(0xFF0E0E0E);
  static const Color surfaceContainerLow = Color(0xFF1C1B1B);
  static const Color surfaceContainer = Color(0xFF201F1F);
  static const Color surfaceContainerHigh = Color(0xFF2A2A2A);
  static const Color surfaceContainerHighest = Color(0xFF353534);
  static const Color surfaceVariant = Color(0xFF353534);

  // ── On-surface & Text ─────────────────────────────────────────
  static const Color onSurface = Color(0xFFE5E2E1);
  static const Color onSurfaceVariant = Color(0xFFC5C9AC);
  static const Color onBackground = Color(0xFFE5E2E1);
  static const Color secondary = Color(0xFFC5C7C9);
  static const Color secondaryContainer = Color(0xFF444749);
  static const Color onSecondary = Color(0xFF2E3133);

  // ── Outline & Borders ────────────────────────────────────────
  static const Color outline = Color(0xFF8E9378);
  static const Color outlineVariant = Color(0xFF444933);

  // ── Glass Panels ─────────────────────────────────────────────
  static Color glassBackground = Colors.white.withValues(alpha: 0.03);
  static Color glassHighlight = const Color(0xFFC7F300).withValues(alpha: 0.05);
  static Color glassBorder = Colors.white.withValues(alpha: 0.08);
  static Color glassBorderHighlight = const Color(0xFFC7F300).withValues(alpha: 0.3);

  // ── Glow Effects ─────────────────────────────────────────────
  static Color glowLime = const Color(0xFFC7F300).withValues(alpha: 0.3);
  static Color glowLimeStrong = const Color(0xFFC7F300).withValues(alpha: 0.6);
  static Color glowWhite = Colors.white.withValues(alpha: 0.2);
  static Color glowStrong = const Color(0xFFC7F300).withValues(alpha: 0.5);

  // ── Semantic Alerts ──────────────────────────────────────────
  static const Color error = Color(0xFFFFB4AB);
  static const Color errorBright = Color(0xFFFF5252);
  static const Color errorContainer = Color(0xFF93000A);
  static const Color onError = Color(0xFF690005);
  static const Color onErrorContainer = Color(0xFFFFDAD6);
  static const Color warning = Color(0xFFFFB74D);
  static const Color success = Color(0xFFC7F300);

  // ── Inverse ──────────────────────────────────────────────────
  static const Color inverseSurface = Color(0xFFE5E2E1);
  static const Color inverseOnSurface = Color(0xFF313030);
  static const Color inversePrimary = Color(0xFF526600);
}
