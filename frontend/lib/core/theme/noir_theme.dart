/// Complete NOIR ThemeData built from the Material Design 3 Cyberpunk design system.
library;

import 'package:flutter/material.dart';
import 'noir_colors.dart';
import 'noir_typography.dart';

class NoirTheme {
  NoirTheme._();

  static ThemeData get darkTheme {
    return ThemeData(
      brightness: Brightness.dark,
      useMaterial3: true,
      scaffoldBackgroundColor: NoirColors.background,
      canvasColor: NoirColors.background,
      fontFamily: 'Inter',
      colorScheme: const ColorScheme.dark(
        primary: NoirColors.primaryFixed,
        onPrimary: NoirColors.onPrimaryContainer,
        primaryContainer: NoirColors.primaryContainer,
        onPrimaryContainer: NoirColors.onPrimaryContainer,
        secondary: NoirColors.secondary,
        onSecondary: NoirColors.onSecondary,
        secondaryContainer: NoirColors.secondaryContainer,
        onSecondaryContainer: NoirColors.onSurface,
        tertiary: NoirColors.tertiaryFixed,
        onTertiary: NoirColors.onTertiaryFixedVariant,
        tertiaryContainer: NoirColors.tertiaryContainer,
        onTertiaryContainer: NoirColors.onTertiaryContainer,
        surfaceTint: NoirColors.surfaceTint,
        error: NoirColors.error,
        onError: NoirColors.onError,
        errorContainer: NoirColors.errorContainer,
        onErrorContainer: NoirColors.onErrorContainer,
        surface: NoirColors.surface,
        onSurface: NoirColors.onSurface,
        onSurfaceVariant: NoirColors.onSurfaceVariant,
        outline: NoirColors.outline,
        outlineVariant: NoirColors.outlineVariant,
        inverseSurface: NoirColors.inverseSurface,
        onInverseSurface: NoirColors.inverseOnSurface,
        inversePrimary: NoirColors.inversePrimary,
      ),
      textTheme: TextTheme(
        headlineLarge: NoirTypography.headlineLg.copyWith(
          color: NoirColors.primary,
          fontWeight: FontWeight.w700,
        ),
        headlineMedium: NoirTypography.headlineMd.copyWith(
          color: NoirColors.primary,
          fontWeight: FontWeight.w600,
        ),
        titleLarge: const TextStyle(
          fontFamily: 'Inter',
          fontSize: 22,
          fontWeight: FontWeight.w500,
          color: NoirColors.primary,
        ),
        bodyLarge: NoirTypography.bodyLg.copyWith(color: NoirColors.onSurface),
        bodyMedium: const TextStyle(
          fontFamily: 'Inter',
          fontSize: 14,
          color: NoirColors.onSurfaceVariant,
        ),
        bodySmall: NoirTypography.bodySm.copyWith(
          color: NoirColors.onSurfaceVariant,
        ),
        labelSmall: NoirTypography.labelCaps.copyWith(
          color: NoirColors.onSurfaceVariant,
        ),
        labelMedium: const TextStyle(
          fontFamily: 'Inter',
          fontSize: 12,
          fontWeight: FontWeight.w500,
          letterSpacing: 0.5,
          color: NoirColors.onSurfaceVariant,
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: NoirColors.surfaceDim.withValues(alpha: 0.8),
        elevation: 0,
        scrolledUnderElevation: 0,
        titleTextStyle: const TextStyle(
          fontFamily: 'Inter',
          fontSize: 24,
          fontWeight: FontWeight.w900,
          letterSpacing: -1.0,
          color: NoirColors.primaryFixed,
        ),
      ),
      cardTheme: CardThemeData(
        color: NoirColors.surfaceContainerLow,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: Colors.white.withValues(alpha: 0.06)),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: NoirColors.primaryFixed,
          foregroundColor: NoirColors.onPrimaryContainer,
          textStyle: const TextStyle(
            fontFamily: 'Inter',
            fontSize: 14,
            fontWeight: FontWeight.w700,
            letterSpacing: 0.5,
          ),
          shape: const StadiumBorder(),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
          elevation: 0,
          shadowColor: NoirColors.primaryFixed.withValues(alpha: 0.3),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: NoirColors.onSurface,
          textStyle: const TextStyle(
            fontFamily: 'Inter',
            fontSize: 14,
            fontWeight: FontWeight.w500,
          ),
          side: const BorderSide(color: NoirColors.outlineVariant),
          shape: const StadiumBorder(),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: NoirColors.surfaceContainerHigh.withValues(alpha: 0.5),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: NoirColors.outlineVariant),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: NoirColors.outlineVariant),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: NoirColors.primaryFixed, width: 1.5),
        ),
        labelStyle: const TextStyle(
          fontFamily: 'Inter',
          color: NoirColors.onSurfaceVariant,
        ),
        hintStyle: TextStyle(
          fontFamily: 'Inter',
          color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
        ),
      ),
    );
  }
}
