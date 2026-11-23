import 'package:flutter_test/flutter_test.dart';
import 'package:ryna_desktop/models/protocol.dart';

void main() {
  test('RynaStatus parses local engine status payload', () {
    final status = RynaStatus.fromJson({
      'version': '0.1.0',
      'online': false,
      'companion_state': 'listening',
      'emergency_stopped': false,
      'ai': {'any_provider_available': false},
      'hardware': {'ram_total_gb': 16.0, 'recommended_tier': 'medium'},
    });

    expect(status.version, '0.1.0');
    expect(status.online, isFalse);
    expect(status.companionState, 'listening');
    expect(status.ramTotalGb, 16.0);
    expect(status.recommendedTier, 'medium');
  });
}
