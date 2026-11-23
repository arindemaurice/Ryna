import 'package:flutter/material.dart';
import 'screens/shell_screen.dart';
import 'theme/ryna_theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const RynaApp());
}

class RynaApp extends StatelessWidget {
  const RynaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'RYNA 0.1.0',
      debugShowCheckedModeBanner: false,
      theme: RynaTheme.dark(),
      home: const RynaShellScreen(),
    );
  }
}
