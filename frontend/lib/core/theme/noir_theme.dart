/// Complete NOIR ThemeData built from the Obsidian Protocol design system.
import 'package:flutter/material.dart';
import 'noir_colors.dart';
import 'noir_typography.dart';

class NoirTheme {
  NoirTheme._();

  static ThemeData get darkTheme {
    return ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: NoirColors.black,
      canvasColor: NoirColors.background,
      fontFamily: 'Inter',
      colorScheme: const ColorScheme.dark(
        primary: NoirColors.primary,
        onPrimary: NoirColors.onPrimary,
        primaryContainer: NoirColors.primaryContainer,
        onPrimaryContainer: NoirColors.onPrimaryContainer,
        surface: NoirColors.surface,
        onSurface: NoirColors.onSurface,
        onSurfaceVariant: NoirColors.onSurfaceVariant,
        outline: NoirColors.outline,
        outlineVariant: NoirColors.outlineVariant,
        inverseSurface: NoirColors.inverseSurface,
        onInverseSurface: NoirColors.inverseOnSurface,
        inversePrimary: NoirColors.inversePrimary,
        error: NoirColors.error,
      ),
      textTheme: TextTheme(
        headlineLarge: NoirTypography.headlineLg.copyWith(color: NoirColors.primary),
        headlineMedium: NoirTypography.headlineMd.copyWith(color: NoirColors.primary),
        bodyLarge: NoirTypography.bodyLg.copyWith(color: NoirColors.onSurface),
        bodySmall: NoirTypography.bodySm.copyWith(color: NoirColors.onSurfaceVariant),
        labelSmall: NoirTypography.labelCaps.copyWith(color: NoirColors.onSurfaceVariant),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: NoirColors.surface.withValues(alpha: 0.8),
        elevation: 0,
        scrolledUnderElevation: 0,
        titleTextStyle: NoirTypography.labelCaps.copyWith(
          color: NoirColors.primary,
          letterSpacing: 3.2,
        ),
      ),
      cardTheme: CardThemeData(
        color: NoirColors.surfaceContainerLow,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(4),
          side: BorderSide(color: NoirColors.glassBorder),
        ),
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: NoirColors.primary,
          foregroundColor: NoirColors.black,
          textStyle: NoirTypography.codeLg.copyWith(fontWeight: FontWeight.w700),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(2)),
          padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 16),
          elevation: 0,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: NoirColors.primary,
          textStyle: NoirTypography.codeSm.copyWith(letterSpacing: 1.6),
          side: BorderSide(color: Colors.white.withValues(alpha: 0.3)),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(2)),
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white.withValues(alpha: 0.05),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(2),
          borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(2),
          borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.1)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(2),
          borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.3)),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        hintStyle: NoirTypography.codeSm.copyWith(
          color: NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
        ),
      ),
      dividerTheme: DividerThemeData(
        color: Colors.white.withValues(alpha: 0.1),
        thickness: 1,
        space: 1,
      ),
      bottomNavigationBarTheme: BottomNavigationBarThemeData(
        backgroundColor: NoirColors.surface.withValues(alpha: 0.6),
        selectedItemColor: NoirColors.primary,
        unselectedItemColor: NoirColors.onSurfaceVariant.withValues(alpha: 0.5),
        type: BottomNavigationBarType.fixed,
        elevation: 0,
        selectedLabelStyle: NoirTypography.labelCaps,
        unselectedLabelStyle: NoirTypography.labelCaps,
      ),
      snackBarTheme: SnackBarThemeData(
        backgroundColor: NoirColors.surfaceContainerHigh,
        contentTextStyle: NoirTypography.bodySm.copyWith(color: NoirColors.onSurface),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
        behavior: SnackBarBehavior.floating,
      ),
      dialogTheme: DialogThemeData(
        backgroundColor: NoirColors.surfaceContainerLow,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(4),
          side: BorderSide(color: Colors.white.withValues(alpha: 0.15), width: 1),
        ),
        titleTextStyle: NoirTypography.headlineMd.copyWith(color: NoirColors.primary),
        contentTextStyle: NoirTypography.bodySm.copyWith(color: NoirColors.onSurfaceVariant),
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: NoirColors.primary,
        linearTrackColor: NoirColors.outlineVariant,
      ),
      scrollbarTheme: ScrollbarThemeData(
        thumbColor: WidgetStateProperty.all(Colors.white.withValues(alpha: 0.2)),
        trackColor: WidgetStateProperty.all(Colors.white.withValues(alpha: 0.05)),
        radius: const Radius.circular(2),
        thickness: WidgetStateProperty.all(4),
      ),
    );
  }
}
