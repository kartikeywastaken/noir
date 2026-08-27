/// Obsidian Protocol typography.
///
/// Inter for UI/content text, JetBrains Mono for technical data.
import 'package:flutter/material.dart';

class NoirTypography {
  NoirTypography._();

  static const String _inter = 'Inter';
  static const String _jetBrainsMono = 'JetBrainsMono';

  // ── Headlines ────────────────────────────────────────────────
  static const TextStyle headlineLg = TextStyle(
    fontFamily: _inter,
    fontSize: 32,
    fontWeight: FontWeight.w700,
    height: 40 / 32,
    letterSpacing: -0.64, // -0.02em
  );

  static const TextStyle headlineMd = TextStyle(
    fontFamily: _inter,
    fontSize: 24,
    fontWeight: FontWeight.w600,
    height: 32 / 24,
    letterSpacing: -0.24, // -0.01em
  );

  // ── Body ─────────────────────────────────────────────────────
  static const TextStyle bodyLg = TextStyle(
    fontFamily: _inter,
    fontSize: 16,
    fontWeight: FontWeight.w400,
    height: 24 / 16,
    letterSpacing: 0,
  );

  static const TextStyle bodySm = TextStyle(
    fontFamily: _inter,
    fontSize: 14,
    fontWeight: FontWeight.w400,
    height: 20 / 14,
    letterSpacing: 0,
  );

  // ── Code ─────────────────────────────────────────────────────
  static const TextStyle codeLg = TextStyle(
    fontFamily: _jetBrainsMono,
    fontSize: 16,
    fontWeight: FontWeight.w500,
    height: 24 / 16,
    letterSpacing: 0,
  );

  static const TextStyle codeSm = TextStyle(
    fontFamily: _jetBrainsMono,
    fontSize: 12,
    fontWeight: FontWeight.w400,
    height: 16 / 12,
    letterSpacing: 0.6, // 0.05em
  );

  // ── Labels ───────────────────────────────────────────────────
  static const TextStyle labelCaps = TextStyle(
    fontFamily: _jetBrainsMono,
    fontSize: 10,
    fontWeight: FontWeight.w700,
    height: 12 / 10,
    letterSpacing: 1.0, // 0.1em
  );
}
