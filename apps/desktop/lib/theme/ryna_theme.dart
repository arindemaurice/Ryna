import 'package:flutter/material.dart';

class RynaTheme {
  static const Color deepBg = Color(0xFF060B16);
  static const Color navySurface = Color(0xFF0B1324);
  static const Color panelSurface = Color(0xFF101B31);
  static const Color cyanAccent = Color(0xFF22D3EE);
  static const Color electricBlue = Color(0xFF3B82F6);
  static const Color border = Color(0xFF1E3357);

  static ThemeData dark() {
    return ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: deepBg,
      colorScheme: const ColorScheme.dark(
        primary: cyanAccent,
        secondary: electricBlue,
        surface: panelSurface,
      ),
      cardColor: panelSurface,
      dividerColor: border,
      useMaterial3: true,
    );
  }
}
