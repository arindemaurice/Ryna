import 'package:flutter_test/flutter_test.dart';
import 'package:ryna_flutter_ui/main.dart';

void main() {
  test('RynaStatusSnapshot parses live status payload accurately', () {
    final snap = RynaStatusSnapshot.fromJson({
      'version': '0.1.0',
      'robot_state': 'listening',
      'operating_mode': {'label': 'Online · Local AI', 'active_model': 'qwen3.5:4b'},
      'robot_asset': {'available': false},
    });
    expect(snap.robotState, 'listening');
    expect(snap.modeLabel, 'Online · Local AI');
    expect(snap.activeModel, 'qwen3.5:4b');
    expect(snap.robotAssetAvailable, isFalse);
  });
}
