import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const RynaApp());
}

class RynaStatusSnapshot {
  final String version;
  final String modeLabel;
  final String activeModel;
  final String robotState;
  final bool robotAssetAvailable;

  const RynaStatusSnapshot({
    required this.version,
    required this.modeLabel,
    required this.activeModel,
    required this.robotState,
    required this.robotAssetAvailable,
  });

  factory RynaStatusSnapshot.fromJson(Map<String, dynamic> json) {
    final op = (json['operating_mode'] as Map<String, dynamic>?) ?? {};
    final asset = (json['robot_asset'] as Map<String, dynamic>?) ?? {};
    return RynaStatusSnapshot(
      version: (json['version'] ?? '0.1.0').toString(),
      modeLabel: (op['label'] ?? 'Checking').toString(),
      activeModel: (op['active_model'] ?? '').toString(),
      robotState: (json['robot_state'] ?? 'idle').toString(),
      robotAssetAvailable: asset['available'] == true,
    );
  }
}

class RynaApp extends StatelessWidget {
  const RynaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'RYNA',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF05070C),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF38BDF8),
          secondary: Color(0xFF818CF8),
          surface: Color(0xFF0F1522),
        ),
        useMaterial3: true,
      ),
      home: const RynaHomeScreen(),
    );
  }
}

class RynaHomeScreen extends StatefulWidget {
  const RynaHomeScreen({super.key});

  @override
  State<RynaHomeScreen> createState() => _RynaHomeScreenState();
}

class _RynaHomeScreenState extends State<RynaHomeScreen> {
  final String _baseUrl = 'http://127.0.0.1:8765';
  final TextEditingController _input = TextEditingController();
  RynaStatusSnapshot? _status;
  String? _convId;
  List<Map<String, dynamic>> _messages = [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<Map<String, dynamic>> _api(String method, String path, [Map<String, dynamic>? body]) async {
    final client = HttpClient();
    try {
      final req = await client.openUrl(method, Uri.parse('$_baseUrl$path'));
      req.headers.contentType = ContentType.json;
      if (body != null) req.write(jsonEncode(body));
      final resp = await req.close();
      final text = await resp.transform(utf8.decoder).join();
      final data = text.isEmpty ? <String, dynamic>{} : jsonDecode(text) as Map<String, dynamic>;
      if (resp.statusCode >= 400) throw Exception(data['error'] ?? 'HTTP ${resp.statusCode}');
      return data;
    } finally {
      client.close();
    }
  }

  Future<void> _refresh() async {
    try {
      final data = await _api('GET', '/api/v1/status');
      setState(() {
        _status = RynaStatusSnapshot.fromJson(data);
        _error = null;
      });
    } catch (e) {
      setState(() => _error = 'RYNA engine unreachable at $_baseUrl: $e');
    }
  }

  Future<void> _send() async {
    final txt = _input.text.trim();
    if (txt.isEmpty) return;
    _input.clear();
    try {
      final res = await _api('POST', '/api/v1/chat', {'conversation_id': _convId, 'message': txt});
      _convId = res['conversation_id']?.toString();
      if (_convId != null) {
        final hist = await _api('GET', '/api/v1/conversations/$_convId/messages');
        setState(() => _messages = List<Map<String, dynamic>>.from(hist['messages'] as List? ?? []));
      }
      await _refresh();
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final st = _status;
    return Scaffold(
      appBar: AppBar(
        backgroundColor: const Color(0xFF0A0E17),
        title: Text('RYNA · ${st?.modeLabel ?? 'Connecting…'}${st != null && st.activeModel.isNotEmpty ? ' · ${st.activeModel}' : ''}'),
        actions: [IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh))],
      ),
      body: Column(
        children: [
          if (_error != null)
            Container(
              width: double.infinity,
              color: Colors.red.withOpacity(0.18),
              padding: const EdgeInsets.all(10),
              child: Text(_error!, style: const TextStyle(color: Colors.redAccent)),
            ),
          Padding(
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                if (st?.robotAssetAvailable == true)
                  Image.network('$_baseUrl/api/v1/robot/asset', width: 72, height: 72)
                else
                  Container(
                    width: 72,
                    height: 72,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: const Color(0xFF38BDF8)),
                    ),
                    child: const Text('Awaiting\nRobot PNG', textAlign: TextAlign.center, style: TextStyle(fontSize: 10)),
                  ),
                const SizedBox(width: 14),
                Text('RYNA State: ${st?.robotState ?? 'idle'}', style: Theme.of(context).textTheme.titleMedium),
              ],
            ),
          ),
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.all(16),
              itemCount: _messages.length,
              itemBuilder: (_, i) {
                final m = _messages[i];
                return Card(
                  child: ListTile(
                    title: Text((m['content'] ?? '').toString()),
                    subtitle: Text('${(m['role'] ?? '').toString().toUpperCase()} · ${m['model'] ?? m['mode'] ?? ''}'),
                  ),
                );
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _input,
                    decoration: const InputDecoration(hintText: 'Message RYNA…'),
                    onSubmitted: (_) => _send(),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton(onPressed: _send, child: const Text('Send')),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
