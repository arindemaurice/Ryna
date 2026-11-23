import 'dart:math' as math;
import 'package:flutter/material.dart';

class CompanionOrb extends StatefulWidget {
  final String state;
  final double size;

  const CompanionOrb({super.key, required this.state, this.size = 180});

  @override
  State<CompanionOrb> createState() => _CompanionOrbState();
}

class _CompanionOrbState extends State<CompanionOrb> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 4),
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Color _accentForState(String st) {
    switch (st) {
      case 'listening':
        return const Color(0xFF38BDF8);
      case 'thinking':
        return const Color(0xFF818CF8);
      case 'speaking':
        return const Color(0xFF2DD4BF);
      case 'working':
        return const Color(0xFF60A5FA);
      case 'waiting':
        return const Color(0xFFFBBF24);
      case 'alert':
        return const Color(0xFFFB7185);
      case 'sleeping':
        return const Color(0xFF64748B);
      default:
        return const Color(0xFF22D3EE);
    }
  }

  @override
  Widget build(BuildContext context) {
    final accent = _accentForState(widget.state);
    return AnimatedBuilder(
      animation: _controller,
      builder: (_, __) {
        return CustomPaint(
          size: Size.square(widget.size),
          painter: _OrbPainter(phase: _controller.value * 2 * math.pi, accent: accent),
        );
      },
    );
  }
}

class _OrbPainter extends CustomPainter {
  final double phase;
  final Color accent;

  _OrbPainter({required this.phase, required this.accent});

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final baseR = size.width * 0.28;
    final pulse = math.sin(phase * 2) * 5;

    final glowPaint = Paint()
      ..shader = RadialGradient(
        colors: [accent.withOpacity(0.35), Colors.transparent],
      ).createShader(Rect.fromCircle(center: center, radius: size.width * 0.48));
    canvas.drawCircle(center, size.width * 0.48, glowPaint);

    final arcPaint = Paint()
      ..color = accent
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3.5;
    canvas.drawArc(
      Rect.fromCircle(center: center, radius: baseR + 14),
      phase,
      math.pi * 0.7,
      false,
      arcPaint,
    );

    canvas.drawCircle(center, baseR + pulse * 0.3, Paint()..color = const Color(0xFF0E1D36));
    canvas.drawCircle(
      center,
      baseR + pulse * 0.3,
      Paint()
        ..color = accent
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5,
    );
    canvas.drawCircle(center, baseR * 0.42, Paint()..color = accent);
  }

  @override
  bool shouldRepaint(covariant _OrbPainter oldDelegate) =>
      oldDelegate.phase != phase || oldDelegate.accent != accent;
}
